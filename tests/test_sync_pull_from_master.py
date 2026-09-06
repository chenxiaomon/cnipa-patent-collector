#!/usr/bin/env python3

import json
import multiprocessing
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import sync_pull_from_master
from db_manager import SYNC_CURSOR_FIELD, PatentsDB


class _FakeResponse:
    def __init__(self, lines: list[bytes]):
        self._lines = lines

    def __enter__(self):
        return iter(self._lines)

    def __exit__(self, exc_type, exc, traceback):
        return False


def _hold_master_sync(lock_path, lock_acquired, release_lock):
    with patch.object(sync_pull_from_master, 'MASTER_SYNC_LOCK_FILE', Path(lock_path)):
        with sync_pull_from_master.reserve_master_sync():
            lock_acquired.set()
            if not release_lock.wait(15):
                raise RuntimeError('Timed out waiting to release synchronization lock')


def _attempt_master_sync(lock_path, cli_arguments, attempts):
    with patch.object(sync_pull_from_master, 'MASTER_SYNC_LOCK_FILE', Path(lock_path)), patch.object(
        sync_pull_from_master, 'require_replica_pull_role'
    ), patch.object(
        sync_pull_from_master, 'load_master_url', return_value='http://master:8765'
    ), patch.object(
        sync_pull_from_master, 'load_sync_cursor', return_value=sync_pull_from_master.INITIAL_SYNC_TIMESTAMP
    ) as read_cursor, patch.object(
        sync_pull_from_master, 'fetch_master_delta', return_value=([], 0)
    ) as fetch_delta, patch.object(sync_pull_from_master, 'save_sync_cursor') as save_cursor:
        exit_code = 0
        try:
            sync_pull_from_master.main(cli_arguments)
        except SystemExit as exc:
            exit_code = exc.code
        attempts.put((exit_code, read_cursor.call_count, fetch_delta.call_count, save_cursor.call_count))


class TestSyncPullFromMaster(unittest.TestCase):
    def test_concurrent_processes_reject_incremental_and_full_before_reading_cursor(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            lock_path = str(Path(tmpdir) / 'master_sync.lock')
            context = multiprocessing.get_context('spawn')
            lock_acquired = context.Event()
            release_lock = context.Event()
            attempts = context.Queue()
            owner = context.Process(target=_hold_master_sync, args=(lock_path, lock_acquired, release_lock))
            owner.start()
            try:
                self.assertTrue(lock_acquired.wait(10))
                for cli_arguments in ([], ['--full']):
                    with self.subTest(cli_arguments=cli_arguments):
                        contender = context.Process(target=_attempt_master_sync, args=(lock_path, cli_arguments, attempts))
                        contender.start()
                        try:
                            contender.join(timeout=10)
                            self.assertEqual(contender.exitcode, 0)
                            self.assertEqual(attempts.get(timeout=2), (1, 0, 0, 0))
                        finally:
                            if contender.is_alive():
                                contender.terminate()
                            contender.join(timeout=5)
            finally:
                release_lock.set()
                owner.join(timeout=5)
                if owner.is_alive():
                    owner.terminate()
                    owner.join(timeout=5)
            self.assertEqual(owner.exitcode, 0)
            successor = context.Process(target=_attempt_master_sync, args=(lock_path, [], attempts))
            successor.start()
            try:
                successor.join(timeout=10)
                self.assertEqual(successor.exitcode, 0)
                self.assertEqual(attempts.get(timeout=2), (0, 1, 1, 1))
            finally:
                if successor.is_alive():
                    successor.terminate()
                successor.join(timeout=5)
                attempts.close()
                attempts.join_thread()

    def test_sync_lock_releases_when_the_operation_raises(self):
        with tempfile.TemporaryDirectory() as tmpdir, patch.object(
            sync_pull_from_master, 'MASTER_SYNC_LOCK_FILE', Path(tmpdir) / 'master_sync.lock'
        ):
            with self.assertRaisesRegex(RuntimeError, 'network failed'):
                with sync_pull_from_master.reserve_master_sync():
                    raise RuntimeError('network failed')
            with sync_pull_from_master.reserve_master_sync():
                pass

    def test_sync_remains_exclusive_until_the_cursor_is_saved(self):
        with tempfile.TemporaryDirectory() as tmpdir, patch.object(
            sync_pull_from_master, 'MASTER_SYNC_LOCK_FILE', Path(tmpdir) / 'master_sync.lock'
        ), patch.object(
            sync_pull_from_master, 'require_replica_pull_role'
        ), patch.object(
            sync_pull_from_master, 'load_master_url', return_value='http://master:8765'
        ), patch.object(
            sync_pull_from_master, 'fetch_master_delta', return_value=([
                {'application_no': '2024110065970', SYNC_CURSOR_FIELD: '2026-09-06T00:00:00.000000Z'},
            ], 0)
        ), patch.object(
            sync_pull_from_master, 'merge_master_delta', return_value={
                'records': 1, 'new_applications': 1, 'updated_applications': 0,
            }
        ), patch.object(sync_pull_from_master, 'update_readme_statistics'), patch.object(
            sync_pull_from_master, 'commit_patent_backup', return_value=False
        ), patch.object(sync_pull_from_master, 'save_sync_cursor') as save_cursor:
            def verify_exclusive_before_save(master_url, timestamp):
                with self.assertRaises(sync_pull_from_master.MasterSyncBusyError):
                    with sync_pull_from_master.reserve_master_sync():
                        self.fail('A second synchronization entered before the cursor was saved')

            save_cursor.side_effect = verify_exclusive_before_save
            sync_pull_from_master.main(['--full'])
            save_cursor.assert_called_once_with('http://master:8765', '2026-09-06T00:00:00.000000Z')

    def test_fetch_master_delta_normalizes_application_numbers(self):
        payload = json.dumps({
            'application_no': 'CN202411006597.0',
            'timestamp': '2026-07-01T00:00:00Z',
            SYNC_CURSOR_FIELD: '2026-07-02T00:00:00Z',
        }).encode('utf-8') + b'\n'
        with patch.object(sync_pull_from_master.urllib.request, 'urlopen', return_value=_FakeResponse([payload])):
            records, bad_lines = sync_pull_from_master.fetch_master_delta(
                'http://master:8765', '2026-06-01T00:00:00Z'
            )
        self.assertEqual(bad_lines, 0)
        self.assertEqual(records[0]['application_no'], '2024110065970')
        self.assertEqual(records[0][SYNC_CURSOR_FIELD], '2026-07-02T00:00:00.000000Z')

    def test_fetch_normalizes_mixed_precision_before_selecting_latest_cursor(self):
        payloads = [
            json.dumps({'application_no': '2024110065970', SYNC_CURSOR_FIELD: cursor}).encode('utf-8')
            for cursor in ('2026-07-02T00:00:00Z', '2026-07-02T08:00:00.1+08:00')
        ]
        with patch.object(sync_pull_from_master.urllib.request, 'urlopen', return_value=_FakeResponse(payloads)):
            records, bad_lines = sync_pull_from_master.fetch_master_delta('http://master:8765', '1970-01-01T00:00:00Z')
        self.assertEqual(bad_lines, 0)
        self.assertEqual(max(record[SYNC_CURSOR_FIELD] for record in records), '2026-07-02T00:00:00.100000Z')

    def test_fetch_counts_invalid_cursors_and_non_record_json(self):
        payloads = [
            b'[]', b'null', b'not json',
            json.dumps({'application_no': '2024110065970', SYNC_CURSOR_FIELD: 'invalid'}).encode('utf-8'),
        ]
        with patch.object(sync_pull_from_master.urllib.request, 'urlopen', return_value=_FakeResponse(payloads)):
            records, bad_lines = sync_pull_from_master.fetch_master_delta('http://master:8765', '1970-01-01T00:00:00Z')
        self.assertEqual(records, [])
        self.assertEqual(bad_lines, 4)

    def test_load_master_url_rejects_missing_configuration(self):
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(
            sync_pull_from_master.os.environ, {}, clear=True
        ), patch.object(
            sync_pull_from_master, 'MASTER_SYNC_CONFIG_FILE', Path(tmpdir) / 'missing.json'
        ):
            with self.assertRaises(sync_pull_from_master.MasterSyncConfigurationError):
                sync_pull_from_master.load_master_url()

    def test_merge_master_delta_updates_database_and_backup(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / 'patents.db'
            backup_path = Path(tmpdir) / 'detection_log.jsonl'
            PatentsDB(db_path).upsert({
                'application_no': '2023000000001',
                'timestamp': '2026-01-01T00:00:00Z',
            })
            records = [
                {'application_no': '2023000000001', 'timestamp': '2026-02-01T00:00:00Z'},
                {'application_no': '2023000000002', 'timestamp': '2026-02-02T00:00:00Z'},
            ]
            with patch.object(sync_pull_from_master, 'PATENTS_DB_FILE', db_path), patch.object(
                sync_pull_from_master, 'DETECTION_LOG_JSONL_FILE', backup_path
            ):
                summary = sync_pull_from_master.merge_master_delta(records)
            self.assertEqual(summary['new_applications'], 1)
            self.assertEqual(summary['updated_applications'], 1)
            self.assertEqual(len(backup_path.read_text(encoding='utf-8').splitlines()), 2)

    def test_master_reset_restores_pending_status_and_clears_stale_fields(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            master = PatentsDB(root / 'master.db')
            replica_path = root / 'replica.db'
            replica = PatentsDB(replica_path)
            backup_path = root / 'detection_log.jsonl'
            application_no = '202310411762X'
            master.upsert({
                'application_no': application_no,
                'status_code': 0,
                'zhuanlimc': 'old title',
                'anjianywzt': 'old status',
                'error_message': 'timeout',
                'fwxx_list': [],
            })
            first_delta = master.export_delta(sync_pull_from_master.INITIAL_SYNC_TIMESTAMP)
            with patch.object(sync_pull_from_master, 'PATENTS_DB_FILE', replica_path), patch.object(
                sync_pull_from_master, 'DETECTION_LOG_JSONL_FILE', backup_path
            ):
                sync_pull_from_master.merge_master_delta(first_delta)
                master.update_fields(application_no, {
                    'status_code': None,
                    'zhuanlimc': None,
                    'anjianywzt': None,
                    'error_message': None,
                })
                reset_delta = master.export_delta(first_delta[0][SYNC_CURSOR_FIELD])
                self.assertEqual(len(reset_delta), 1)
                sync_pull_from_master.merge_master_delta(reset_delta)

            self.assertEqual(replica.get_record(application_no), master.get_record(application_no))
            self.assertEqual(replica.get_processed_app_nos(), set())
            self.assertEqual(replica.get_stats()['pending'], 1)
            self.assertEqual(replica.get_stats()['failed'], 0)
            self.assertEqual(
                json.loads(backup_path.read_text(encoding='utf-8')),
                master.get_record(application_no),
            )

    def test_save_sync_cursor_is_atomic_and_reloadable(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / 'master_sync_state.json'
            with patch.object(sync_pull_from_master, 'MASTER_SYNC_STATE_FILE', state_path):
                sync_pull_from_master.save_sync_cursor(
                    'http://master:8765', '2026-07-01T00:00:00Z'
                )
                self.assertEqual(
                    sync_pull_from_master.load_sync_cursor('http://master:8765'),
                    '2026-07-01T00:00:00Z',
                )

    def test_legacy_cursor_or_changed_master_forces_full_reconciliation(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / 'master_sync_state.json'
            state_path.write_text(json.dumps({
                'master_url': 'http://old-master:8765',
                'last_timestamp': '2026-07-01T00:00:00Z',
            }), encoding='utf-8')
            with patch.object(sync_pull_from_master, 'MASTER_SYNC_STATE_FILE', state_path):
                self.assertEqual(
                    sync_pull_from_master.load_sync_cursor('http://old-master:8765'),
                    sync_pull_from_master.INITIAL_SYNC_TIMESTAMP,
                )

                sync_pull_from_master.save_sync_cursor(
                    'http://old-master:8765', '2026-07-02T00:00:00Z'
                )
                self.assertEqual(
                    sync_pull_from_master.load_sync_cursor('http://new-master:8765'),
                    sync_pull_from_master.INITIAL_SYNC_TIMESTAMP,
                )

    def test_cursor_from_null_preserving_import_forces_full_reconciliation(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / 'master_sync_state.json'
            state_path.write_text(json.dumps({
                'master_url': 'http://master:8765',
                'last_sync_updated_at': '2026-07-01T00:00:00Z',
            }), encoding='utf-8')
            with patch.object(sync_pull_from_master, 'MASTER_SYNC_STATE_FILE', state_path):
                self.assertEqual(
                    sync_pull_from_master.load_sync_cursor('http://master:8765'),
                    sync_pull_from_master.INITIAL_SYNC_TIMESTAMP,
                )

    def test_timestamp_cursor_upgrade_reconciles_even_when_master_is_empty(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / 'master_sync_state.json'
            state_path.write_text(json.dumps({
                'master_url': 'http://master:8765',
                'last_sync_updated_at': '2026-07-01T00:00:00Z',
                'snapshot_import_version': 1,
            }), encoding='utf-8')
            with patch.object(sync_pull_from_master, 'MASTER_SYNC_STATE_FILE', state_path), patch.object(
                sync_pull_from_master, 'MASTER_SYNC_LOCK_FILE', Path(tmpdir) / 'master_sync.lock'
            ), patch.object(
                sync_pull_from_master, 'require_replica_pull_role'
            ), patch.object(
                sync_pull_from_master, 'load_master_url', return_value='http://master:8765'
            ), patch.object(
                sync_pull_from_master, 'fetch_master_delta', return_value=([], 0)
            ) as fetch_delta:
                sync_pull_from_master.main([])
                fetch_delta.assert_called_once_with('http://master:8765', sync_pull_from_master.INITIAL_SYNC_TIMESTAMP)
            state = json.loads(state_path.read_text(encoding='utf-8'))
            self.assertEqual(state['snapshot_import_version'], 2)
            self.assertEqual(state['last_sync_updated_at'], sync_pull_from_master.INITIAL_SYNC_TIMESTAMP)

    def test_incremental_runs_commit_and_full_reconciliation_repairs_replica(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            master_db = PatentsDB(root / 'master.db')
            replica_db_path = root / 'replica.db'
            backup_path = root / 'data' / 'results' / 'detection_log.jsonl'
            state_path = root / 'data' / 'master_sync_state.json'
            readme_path = root / 'README.md'
            backup_path.parent.mkdir(parents=True)
            backup_path.write_text('', encoding='utf-8')
            readme_path.write_text('# Test\n', encoding='utf-8')
            master_db.upsert({'application_no': '2023000000001', 'timestamp': None})
            master_db.upsert({'application_no': '2023000000002', 'timestamp': None})

            served_batches: list[list[str]] = []

            class MasterDeltaHandler(BaseHTTPRequestHandler):
                def log_message(self, format, *args):
                    pass

                def do_GET(self):
                    parsed = urllib.parse.urlparse(self.path)
                    since = urllib.parse.parse_qs(parsed.query)['since'][0]
                    records = master_db.export_delta(since)
                    served_batches.append([record['application_no'] for record in records])
                    body = ''.join(
                        json.dumps(record, ensure_ascii=False) + '\n'
                        for record in records
                    ).encode('utf-8')
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/x-ndjson')
                    self.send_header('Content-Length', str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)

            server = ThreadingHTTPServer(('127.0.0.1', 0), MasterDeltaHandler)
            server_thread = threading.Thread(target=server.serve_forever, daemon=True)
            server_thread.start()
            master_url = f'http://127.0.0.1:{server.server_address[1]}'

            subprocess.run(['git', 'init', '-q'], cwd=root, check=True)
            subprocess.run(['git', 'config', 'user.name', 'Test'], cwd=root, check=True)
            subprocess.run(['git', 'config', 'user.email', 'test@example.com'], cwd=root, check=True)
            subprocess.run(['git', 'add', '.'], cwd=root, check=True)
            subprocess.run(['git', 'commit', '-qm', 'initial'], cwd=root, check=True)

            try:
                with patch.object(sync_pull_from_master, 'BASE_DIR', root), patch.object(
                    sync_pull_from_master, 'PATENTS_DB_FILE', replica_db_path
                ), patch.object(
                    sync_pull_from_master, 'DETECTION_LOG_JSONL_FILE', backup_path
                ), patch.object(
                    sync_pull_from_master, 'MASTER_SYNC_STATE_FILE', state_path
                ), patch.object(
                    sync_pull_from_master, 'MASTER_SYNC_LOCK_FILE', root / 'data' / 'master_sync.lock'
                ), patch.object(
                    sync_pull_from_master, 'require_replica_pull_role', return_value=None
                ), patch.object(
                    sync_pull_from_master, 'update_readme_statistics', return_value=None
                ), patch.dict(
                    sync_pull_from_master.os.environ, {'CNIPA_MASTER_URL': master_url}
                ):
                    sync_pull_from_master.main([])
                    first_cursor = json.loads(state_path.read_text(encoding='utf-8'))[
                        'last_sync_updated_at'
                    ]

                    time.sleep(0.002)
                    master_db.update_fields(
                        '2023000000001', {'zhuanlimc': '第二天补充的字段'}
                    )
                    sync_pull_from_master.main([])
                    PatentsDB(replica_db_path).update_fields(
                        '2023000000001', {'zhuanlimc': 'stale local value'}
                    )
                    sync_pull_from_master.main(['--full'])

                commit_count = subprocess.run(
                    ['git', 'rev-list', '--count', 'HEAD'],
                    cwd=root, check=True, capture_output=True, text=True,
                ).stdout.strip()
                final_cursor = json.loads(state_path.read_text(encoding='utf-8'))[
                    'last_sync_updated_at'
                ]
                replica_record = PatentsDB(replica_db_path).get_record('2023000000001')
                self.assertEqual(served_batches, [
                    ['2023000000001', '2023000000002'],
                    ['2023000000001'],
                    ['2023000000002', '2023000000001'],
                ])
                self.assertEqual(commit_count, '3')
                self.assertGreater(final_cursor, first_cursor)
                self.assertEqual(replica_record['zhuanlimc'], '第二天补充的字段')
            finally:
                server.shutdown()
                server.server_close()
                server_thread.join(timeout=5)
