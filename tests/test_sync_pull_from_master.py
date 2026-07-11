#!/usr/bin/env python3

import json
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


class TestSyncPullFromMaster(unittest.TestCase):
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

    def test_two_delta_runs_create_two_separate_data_commits(self):
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
                    sync_pull_from_master, 'require_replica_pull_role', return_value=None
                ), patch.object(
                    sync_pull_from_master, 'update_readme_statistics', return_value=None
                ), patch.dict(
                    sync_pull_from_master.os.environ, {'CNIPA_MASTER_URL': master_url}
                ):
                    sync_pull_from_master.main()
                    first_cursor = json.loads(state_path.read_text(encoding='utf-8'))[
                        'last_sync_updated_at'
                    ]

                    time.sleep(0.002)
                    master_db.update_fields(
                        '2023000000001', {'zhuanlimc': '第二天补充的字段'}
                    )
                    sync_pull_from_master.main()

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
                ])
                self.assertEqual(commit_count, '3')
                self.assertGreater(final_cursor, first_cursor)
                self.assertEqual(replica_record['zhuanlimc'], '第二天补充的字段')
            finally:
                server.shutdown()
                server.server_close()
                server_thread.join(timeout=5)
