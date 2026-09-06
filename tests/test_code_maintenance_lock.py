#!/usr/bin/env python3

import json
import multiprocessing
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import desktop_collection_lock
import fetch_update
import rollback
from code_release_safety import sha256_bytes
from desktop_collection_lock import (
    DetailCollectionDesktopBusyError,
    reserve_code_maintenance,
    reserve_detail_collection_desktop,
    reserve_phase0_browser,
    reserve_public_browser,
    reserve_public_pagination,
    reserve_supervised_collection,
)


def _hold_reservation(lock_directory, reserve_operation, ready, release):
    desktop_collection_lock.DETAIL_COLLECTION_LOCK_FILE = lock_directory / 'desktop.lock'
    desktop_collection_lock.SUPERVISED_COLLECTION_LOCK_FILE = lock_directory / 'supervisor.lock'
    desktop_collection_lock.PHASE0_BROWSER_LOCK_FILE = lock_directory / 'phase0.lock'
    desktop_collection_lock.PUBLIC_BROWSER_LOCK_FILE = lock_directory / 'public_browser.lock'
    desktop_collection_lock.PUBLIC_PAGINATION_LOCK_FILE = lock_directory / 'public_pagination.lock'
    with reserve_operation('synthetic child'):
        ready.set()
        if not release.wait(15):
            raise TimeoutError('Parent did not release the synthetic reservation')


class TestCodeMaintenanceLock(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name)
        self.lock_directory = self.root / 'locks'
        for constant, filename in (
            ('DETAIL_COLLECTION_LOCK_FILE', 'desktop.lock'),
            ('SUPERVISED_COLLECTION_LOCK_FILE', 'supervisor.lock'),
            ('PHASE0_BROWSER_LOCK_FILE', 'phase0.lock'),
            ('PUBLIC_BROWSER_LOCK_FILE', 'public_browser.lock'),
            ('PUBLIC_PAGINATION_LOCK_FILE', 'public_pagination.lock'),
        ):
            replacement = patch.object(
                desktop_collection_lock, constant, self.lock_directory / filename,
            )
            replacement.start()
            self.addCleanup(replacement.stop)

    @contextmanager
    def _child_reservation(self, reserve_operation):
        context = multiprocessing.get_context('spawn')
        ready = context.Event()
        release = context.Event()
        child = context.Process(
            target=_hold_reservation,
            args=(self.lock_directory, reserve_operation, ready, release),
        )
        child.start()
        try:
            self.assertTrue(ready.wait(10), 'Child did not acquire its reservation')
            yield child
        finally:
            if child.is_alive():
                release.set()
            child.join(5)
            if child.is_alive():
                child.terminate()
                child.join(5)
            self.assertFalse(child.is_alive(), 'Child failed to exit')

    def _assert_collections_are_excluded(self):
        for reservation in (
            reserve_detail_collection_desktop,
            reserve_supervised_collection,
            reserve_phase0_browser,
            reserve_public_browser,
            reserve_public_pagination,
        ):
            with self.assertRaises(DetailCollectionDesktopBusyError):
                with reservation('contending collection'):
                    self.fail('Collection started during code maintenance')

    def _assert_maintenance_released(self):
        with reserve_code_maintenance('subsequent maintenance'):
            pass

    def test_collection_in_another_process_rejects_update_and_rollback_before_work(self):
        with self._child_reservation(reserve_detail_collection_desktop):
            with patch.object(fetch_update, 'download_release_file') as download:
                with self.assertRaisesRegex(DetailCollectionDesktopBusyError, '采集或更新/回滚'):
                    fetch_update.install_release(self.root, self.root / 'backups')
            download.assert_not_called()
            with patch.object(rollback, 'latest_code_backup') as latest_backup:
                with self.assertRaises(SystemExit) as failure:
                    rollback.main()
            self.assertEqual(failure.exception.code, 1)
            latest_backup.assert_not_called()
            # Failing to acquire the desktop must release the earlier supervisor lock.
            with reserve_supervised_collection('new supervisor'):
                pass
        self._assert_maintenance_released()

    def test_browser_runtime_in_another_process_rejects_code_update_before_download(self):
        for reservation in (
            reserve_phase0_browser,
            reserve_public_browser,
            reserve_public_pagination,
        ):
            with self.subTest(reservation=reservation.__name__), self._child_reservation(reservation):
                with patch.object(fetch_update, 'download_release_file') as download:
                    with self.assertRaises(DetailCollectionDesktopBusyError):
                        fetch_update.install_release(self.root, self.root / 'backups')
                download.assert_not_called()

    def test_public_browser_and_pagination_reservations_can_coexist(self):
        with reserve_public_browser('public browser'):
            with reserve_public_pagination('public pagination'):
                pass

    def test_maintenance_in_another_process_excludes_both_collectors_and_maintenance(self):
        with self._child_reservation(reserve_code_maintenance):
            self._assert_collections_are_excluded()
            with self.assertRaises(DetailCollectionDesktopBusyError):
                with reserve_code_maintenance('second maintenance'):
                    self.fail('Concurrent maintenance started')
        self._assert_maintenance_released()

    def test_supervisor_reserves_retry_gap_without_blocking_its_collector(self):
        with self._child_reservation(reserve_supervised_collection):
            with reserve_detail_collection_desktop('supervised child collection'):
                pass
            with self.assertRaises(DetailCollectionDesktopBusyError):
                with reserve_code_maintenance('update between collection attempts'):
                    self.fail('Update started while the supervisor was alive')
        self._assert_maintenance_released()

    def test_terminated_maintenance_process_releases_both_os_locks(self):
        with self._child_reservation(reserve_code_maintenance) as child:
            child.terminate()
            child.join(5)
            self.assertFalse(child.is_alive())
            self._assert_maintenance_released()

    def test_failure_inside_maintenance_releases_both_locks(self):
        with self.assertRaisesRegex(OSError, 'synthetic disk error'):
            with reserve_code_maintenance('failing update'):
                raise OSError('synthetic disk error')
        self._assert_maintenance_released()

    def test_verified_update_holds_locks_during_download_backup_install_and_retention(self):
        project_root = self.root / 'project'
        project_root.mkdir()
        (project_root / 'VERSION').write_bytes(b'2026.09.06\n')
        (project_root / 'app.py').write_bytes(b'old code\n')
        release_files = {'VERSION': b'2026.09.07\n', 'app.py': b'new code\n'}
        manifest = json.dumps({
            'manifest_version': 1,
            'files': [
                {'path': path, 'sha256': sha256_bytes(content)}
                for path, content in release_files.items()
            ],
        }).encode('utf-8')
        visited_stages = []

        def download(relative_path):
            self._assert_collections_are_excluded()
            visited_stages.append('download')
            return manifest if relative_path == fetch_update.MANIFEST_NAME else release_files[relative_path]

        def observe_stage(stage_name):
            stage_operation = getattr(fetch_update, stage_name)

            def run_stage(*arguments, **keywords):
                self._assert_collections_are_excluded()
                visited_stages.append(stage_name)
                return stage_operation(*arguments, **keywords)

            return run_stage

        with (
            patch.object(fetch_update, 'download_release_file', side_effect=download),
            patch.object(fetch_update, 'create_code_backup', side_effect=observe_stage('create_code_backup')),
            patch.object(fetch_update, 'install_staged_release', side_effect=observe_stage('install_staged_release')),
            patch.object(fetch_update, 'prune_code_backups', side_effect=observe_stage('prune_code_backups')),
        ):
            fetch_update.install_release(project_root, project_root / 'backups')

        self.assertEqual(visited_stages, [
            'download', 'download', 'download', 'create_code_backup',
            'install_staged_release', 'prune_code_backups',
        ])
        self.assertEqual((project_root / 'app.py').read_bytes(), b'new code\n')
        self._assert_maintenance_released()

    def test_failed_update_keeps_maintenance_lock_through_automatic_restore(self):
        project_root = self.root / 'project'
        project_root.mkdir()
        (project_root / 'VERSION').write_bytes(b'2026.09.06\n')
        (project_root / 'app.py').write_bytes(b'old code\n')
        version_bytes = b'2026.09.07\n'
        manifest = json.dumps({
            'manifest_version': 1,
            'files': [{'path': 'VERSION', 'sha256': sha256_bytes(version_bytes)}],
        }).encode('utf-8')
        restore_backup = fetch_update.restore_code_backup

        def fail_install(staging_root, manifest_entries, installed_root):
            self._assert_collections_are_excluded()
            (installed_root / 'app.py').write_bytes(b'partial code\n')
            raise OSError('synthetic install error')

        def restore(backup_path, installed_root):
            self._assert_collections_are_excluded()
            return restore_backup(backup_path, installed_root)

        with (
            patch.object(fetch_update, 'download_release_file', side_effect=lambda path: manifest if path == fetch_update.MANIFEST_NAME else version_bytes),
            patch.object(fetch_update, 'install_staged_release', side_effect=fail_install),
            patch.object(fetch_update, 'restore_code_backup', side_effect=restore) as restore_call,
        ):
            with self.assertRaisesRegex(OSError, 'synthetic install error'):
                fetch_update.install_release(project_root, project_root / 'backups')

        restore_call.assert_called_once()
        self.assertEqual((project_root / 'app.py').read_bytes(), b'old code\n')
        self._assert_maintenance_released()

    def test_rollback_holds_locks_before_backup_selection_until_restore_failure(self):
        def select_backup():
            self._assert_collections_are_excluded()
            return self.root / 'backup'

        def fail_restore(backup_path):
            self._assert_collections_are_excluded()
            raise OSError('synthetic restore error')

        with (
            patch.object(rollback, 'latest_code_backup', side_effect=select_backup),
            patch.object(rollback, 'restore_code_backup', side_effect=fail_restore) as restore_call,
        ):
            with self.assertRaises(SystemExit) as failure:
                rollback.main()

        self.assertEqual(failure.exception.code, 1)
        restore_call.assert_called_once_with(self.root / 'backup')
        self._assert_maintenance_released()

    def test_update_cli_reports_busy_as_failure_without_downloading(self):
        with reserve_supervised_collection('active watchdog'):
            with (
                patch.object(fetch_update.sys, 'argv', ['fetch_update.py']),
                patch.object(fetch_update, 'download_release_file') as download,
            ):
                with self.assertRaises(SystemExit) as failure:
                    fetch_update.main()
        self.assertEqual(failure.exception.code, 1)
        download.assert_not_called()


if __name__ == '__main__':
    unittest.main()
