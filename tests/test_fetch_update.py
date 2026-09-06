#!/usr/bin/env python3

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import fetch_update
from code_release_safety import (
    CodeReleaseVerificationError,
    latest_code_backup,
    restore_code_backup,
    sha256_bytes,
)


class TestFetchUpdate(unittest.TestCase):
    def test_same_day_revision_installs_and_rolls_back_with_its_code(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / 'VERSION').write_bytes(b'2026.09.06\n')
            (root / 'RELEASE_REVISION').write_bytes(b'1\n')
            (root / 'app.py').write_bytes(b'before\n')
            release_files = {'VERSION': b'2026.09.06\n', 'RELEASE_REVISION': b'2\n', 'app.py': b'after\n'}
            manifest = json.dumps({
                'manifest_version': 1,
                'release': {'version': '2026.09.06', 'revision': 2},
                'files': [{'path': path, 'sha256': sha256_bytes(content)} for path, content in release_files.items()],
            }).encode('utf-8')
            with patch.object(fetch_update, 'download_release_file', side_effect=lambda path: manifest if path == fetch_update.MANIFEST_NAME else release_files[path]):
                fetch_update.install_release(root, root / 'backups')
            self.assertEqual((root / 'RELEASE_REVISION').read_bytes(), b'2\n')
            self.assertEqual((root / 'app.py').read_bytes(), b'after\n')
            restore_code_backup(latest_code_backup(root / 'backups'), root)
            self.assertEqual((root / 'RELEASE_REVISION').read_bytes(), b'1\n')
            self.assertEqual((root / 'app.py').read_bytes(), b'before\n')

    def test_revision_downgrade_and_inconsistent_manifest_leave_code_untouched(self):
        for staged_date, staged_revision, advertised_revision in (
            ('2026.09.06', b'1\n', 1),
            ('2026.09.06', b'3\n', 4),
            ('2026.09.07', None, None),
        ):
            with self.subTest(staged_date=staged_date, staged_revision=staged_revision), tempfile.TemporaryDirectory() as tmpdir:
                root = Path(tmpdir)
                (root / 'VERSION').write_bytes(b'2026.09.06\n')
                (root / 'RELEASE_REVISION').write_bytes(b'2\n')
                (root / 'app.py').write_bytes(b'before\n')
                release_files = {'VERSION': staged_date.encode('ascii'), 'app.py': b'after\n'}
                if staged_revision is not None:
                    release_files['RELEASE_REVISION'] = staged_revision
                manifest_payload = {
                    'manifest_version': 1,
                    'files': [{'path': path, 'sha256': sha256_bytes(content)} for path, content in release_files.items()],
                }
                if advertised_revision is not None:
                    manifest_payload['release'] = {'version': staged_date, 'revision': advertised_revision}
                manifest = json.dumps(manifest_payload).encode('utf-8')
                with (
                    patch.object(fetch_update, 'download_release_file', side_effect=lambda path: manifest if path == fetch_update.MANIFEST_NAME else release_files[path]),
                    patch.object(fetch_update, 'create_code_backup') as backup,
                ):
                    with self.assertRaises(CodeReleaseVerificationError):
                        fetch_update.install_release(root, root / 'backups')
                backup.assert_not_called()
                self.assertEqual((root / 'RELEASE_REVISION').read_bytes(), b'2\n')
                self.assertEqual((root / 'app.py').read_bytes(), b'before\n')

    def test_verified_release_installs_and_can_rollback(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / 'project'
            backups = root / 'backups'
            root.mkdir()
            (root / 'app.py').write_bytes(b'before\n')
            (root / 'VERSION').write_bytes(b'2026.07.21\n')
            release_files = {
                'app.py': b'after\n',
                'VERSION': b'2026.07.27\n',
            }
            manifest = json.dumps({
                'manifest_version': 1,
                'files': [
                    {'path': path, 'sha256': sha256_bytes(content)}
                    for path, content in release_files.items()
                ],
            }).encode('utf-8')

            def download(path, timeout=30):
                del timeout
                return manifest if path == fetch_update.MANIFEST_NAME else release_files[path]

            with patch.object(fetch_update, 'download_release_file', side_effect=download):
                fetch_update.install_release(root, backups)
            self.assertEqual((root / 'app.py').read_bytes(), b'after\n')
            self.assertEqual((root / 'VERSION').read_bytes(), b'2026.07.27\n')

            restore_code_backup(latest_code_backup(backups), root)
            self.assertEqual((root / 'app.py').read_bytes(), b'before\n')
            self.assertEqual((root / 'VERSION').read_bytes(), b'2026.07.21\n')

    def test_hash_mismatch_leaves_code_and_existing_backups_untouched(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / 'project'
            backups = root / 'backups'
            root.mkdir()
            (root / 'app.py').write_bytes(b'before\n')
            (root / 'VERSION').write_bytes(b'2026.07.21\n')
            release_files = {
                'app.py': b'tampered\n',
                'VERSION': b'2026.07.27\n',
            }
            manifest = json.dumps({
                'manifest_version': 1,
                'files': [
                    {'path': 'app.py', 'sha256': sha256_bytes(b'expected\n')},
                    {'path': 'VERSION', 'sha256': sha256_bytes(release_files['VERSION'])},
                ],
            }).encode('utf-8')

            def download(path, timeout=30):
                del timeout
                return manifest if path == fetch_update.MANIFEST_NAME else release_files[path]

            with patch.object(fetch_update, 'download_release_file', side_effect=download), patch.object(
                fetch_update, 'create_code_backup'
            ) as create_backup, patch.object(fetch_update, 'restore_code_backup') as restore_backup:
                with self.assertRaises(CodeReleaseVerificationError):
                    fetch_update.install_release(root, backups)
            create_backup.assert_not_called()
            restore_backup.assert_not_called()
            self.assertEqual((root / 'app.py').read_bytes(), b'before\n')
            self.assertEqual((root / 'VERSION').read_bytes(), b'2026.07.21\n')

    def test_older_release_is_rejected_without_changing_installed_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / 'project'
            backups = root / 'backups'
            root.mkdir()
            (root / 'app.py').write_bytes(b'before\n')
            (root / 'VERSION').write_bytes(b'2026.07.27\n')
            release_files = {
                'app.py': b'after\n',
                'VERSION': b'2026.07.21\n',
            }
            manifest = json.dumps({
                'manifest_version': 1,
                'files': [
                    {'path': path, 'sha256': sha256_bytes(content)}
                    for path, content in release_files.items()
                ],
            }).encode('utf-8')

            def download(path, timeout=30):
                del timeout
                return manifest if path == fetch_update.MANIFEST_NAME else release_files[path]

            with patch.object(fetch_update, 'download_release_file', side_effect=download):
                with self.assertRaises(CodeReleaseVerificationError):
                    fetch_update.install_release(root, backups)

            self.assertEqual((root / 'app.py').read_bytes(), b'before\n')
            self.assertEqual((root / 'VERSION').read_bytes(), b'2026.07.27\n')

    def test_partial_installation_failure_restores_previous_code(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / 'project'
            root.mkdir()
            (root / 'app.py').write_bytes(b'before\n')
            (root / 'VERSION').write_bytes(b'2026.07.21\n')
            release_files = {'app.py': b'after\n', 'VERSION': b'2026.07.27\n'}
            manifest = json.dumps({
                'manifest_version': 1,
                'files': [
                    {'path': path, 'sha256': sha256_bytes(content)}
                    for path, content in release_files.items()
                ],
            }).encode('utf-8')

            def download(path, timeout=30):
                del timeout
                return manifest if path == fetch_update.MANIFEST_NAME else release_files[path]

            def fail_install(staging_root, manifest_entries, project_root):
                del staging_root, manifest_entries
                (project_root / 'app.py').write_bytes(b'partial installation\n')
                (project_root / 'new.py').write_bytes(b'new\n')
                raise OSError('installation failed')

            with patch.object(fetch_update, 'download_release_file', side_effect=download), patch.object(
                fetch_update, 'install_staged_release', side_effect=fail_install
            ):
                with self.assertRaises(OSError):
                    fetch_update.install_release(root, root / 'backups')

            self.assertEqual((root / 'app.py').read_bytes(), b'before\n')
            self.assertEqual((root / 'VERSION').read_bytes(), b'2026.07.21\n')
            self.assertFalse((root / 'new.py').exists())

    def test_backup_retention_failure_does_not_undo_a_successful_installation(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / 'project'
            root.mkdir()
            (root / 'VERSION').write_bytes(b'2026.07.21\n')
            release_version = b'2026.07.27\n'
            manifest = json.dumps({
                'manifest_version': 1,
                'files': [{'path': 'VERSION', 'sha256': sha256_bytes(release_version)}],
            }).encode('utf-8')

            def download(path, timeout=30):
                del timeout
                return manifest if path == fetch_update.MANIFEST_NAME else release_version

            with patch.object(fetch_update, 'download_release_file', side_effect=download), patch.object(
                fetch_update, 'prune_code_backups', side_effect=OSError('backup is locked')
            ), patch.object(fetch_update, 'restore_code_backup') as restore_backup:
                fetch_update.install_release(root, root / 'backups')

            restore_backup.assert_not_called()
            self.assertEqual((root / 'VERSION').read_bytes(), release_version)

    def test_release_without_version_is_rejected_without_changing_code(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / 'project'
            backups = root / 'backups'
            root.mkdir()
            (root / 'app.py').write_bytes(b'before\n')
            (root / 'VERSION').write_bytes(b'2026.07.27\n')
            manifest = json.dumps({
                'manifest_version': 1,
                'files': [
                    {'path': 'app.py', 'sha256': sha256_bytes(b'after\n')},
                ],
            }).encode('utf-8')

            def download(path, timeout=30):
                del timeout
                return manifest if path == fetch_update.MANIFEST_NAME else b'after\n'

            with patch.object(fetch_update, 'download_release_file', side_effect=download):
                with self.assertRaises(CodeReleaseVerificationError):
                    fetch_update.install_release(root, backups)

            self.assertEqual((root / 'app.py').read_bytes(), b'before\n')
            self.assertEqual((root / 'VERSION').read_bytes(), b'2026.07.27\n')
