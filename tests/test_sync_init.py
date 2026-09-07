import io
import json
import os
import sqlite3
import subprocess
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import sync
from db_manager import PatentsDB, rebuild_patents_database_from_jsonl


class TestSyncInitialization(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory(prefix='cnipa sync ')
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name)
        self.upstream = self.root / 'upstream'
        self.replica = self.root / 'replica with spaces'
        self.upstream.mkdir()
        self.backup_relative_path = Path('data/results/detection_log.jsonl')
        self.upstream_backup = self.upstream / self.backup_relative_path
        self.upstream_backup.parent.mkdir(parents=True)
        self.git(self.upstream, 'init')
        self.write_upstream_snapshot('initial')
        self.git(self.root, 'clone', str(self.upstream), str(self.replica))
        self.replica_backup = self.replica / self.backup_relative_path
        self.database_path = self.replica / 'data/patents.db'

    def git(self, working_directory, *arguments):
        return subprocess.run(
            [sync._GIT, '-c', 'user.name=Sync Test', '-c', 'user.email=sync@example.test',
             '-c', 'core.autocrlf=false', *arguments],
            cwd=working_directory,
            text=True,
            encoding='utf-8',
            capture_output=True,
            check=True,
        )

    def write_upstream_snapshot(self, title):
        self.upstream_backup.write_text(
            json.dumps({'application_no': 'CN202310000001', 'zhuanlimc': title}) + '\n',
            encoding='utf-8',
        )
        self.git(self.upstream, 'add', '--', self.backup_relative_path.as_posix())
        self.git(self.upstream, 'commit', '-m', title)

    def initialize_replica(self):
        with patch.object(sync, 'BASE_DIR', self.replica), patch.object(
            sync, 'PATENTS_DB_FILE', self.database_path
        ), patch.object(
            sync, 'DETECTION_LOG_JSONL_FILE', self.replica_backup
        ), patch.object(
            sync, 'require_database_rebuild_authorization', return_value='replica'
        ), patch.object(sync.sys, 'argv', ['sync.py', 'init']), redirect_stdout(io.StringIO()):
            sync.cmd_init()

    def test_successful_fast_forward_imports_updated_backup_from_spaced_path(self):
        self.write_upstream_snapshot('updated')

        self.initialize_replica()

        records = PatentsDB(self.database_path).get_all_records()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]['zhuanlimc'], 'updated')

    def test_diverged_backup_is_preserved_without_creating_database(self):
        self.replica_backup.write_text(
            json.dumps({'application_no': 'CN202310000001', 'zhuanlimc': 'local'}) + '\n',
            encoding='utf-8',
        )
        self.git(self.replica, 'add', '--', self.backup_relative_path.as_posix())
        self.git(self.replica, 'commit', '-m', 'local')
        previous_backup = self.replica_backup.read_bytes()
        self.write_upstream_snapshot('remote')

        with self.assertRaises(SystemExit) as stopped:
            self.initialize_replica()

        self.assertEqual(stopped.exception.code, 1)
        self.assertEqual(self.replica_backup.read_bytes(), previous_backup)
        self.assertFalse(self.database_path.exists())
        self.assertEqual(self.git(self.replica, 'ls-files', '-u').stdout, '')

    def test_unavailable_remote_does_not_import_local_backup(self):
        self.git(self.replica, 'remote', 'set-url', 'origin', str(self.root / 'missing'))
        previous_backup = self.replica_backup.read_bytes()

        with self.assertRaises(SystemExit) as stopped:
            self.initialize_replica()

        self.assertEqual(stopped.exception.code, 1)
        self.assertEqual(self.replica_backup.read_bytes(), previous_backup)
        self.assertFalse(self.database_path.exists())


class TestBackupImportValidation(unittest.TestCase):
    def test_invalid_backup_does_not_partially_import(self):
        first_record = {'application_no': 'CN202310000001', 'zhuanlimc': 'new'}
        invalid_lines = (
            '{broken',
            '<<<<<<< HEAD',
            '[]',
            '{}',
            '{"application_no": 123}',
            '{"application_no": " "}',
        )
        for invalid_line in invalid_lines:
            with self.subTest(invalid_line=invalid_line), tempfile.TemporaryDirectory() as tmpdir:
                root = Path(tmpdir)
                database = PatentsDB(root / 'patents.db')
                database.upsert({'application_no': 'CN202310000002', 'zhuanlimc': 'existing'})
                previous_records = database.get_all_records()
                backup = root / 'backup.jsonl'
                backup.write_text(json.dumps(first_record) + '\n' + invalid_line + '\n', encoding='utf-8')

                with self.assertRaisesRegex(ValueError, '2'):
                    database.import_from_jsonl(backup)

                self.assertEqual(database.get_all_records(), previous_records)

    def test_valid_backup_accepts_blank_lines(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            database = PatentsDB(root / 'patents.db')
            backup = root / 'backup.jsonl'
            backup.write_text('\n{"application_no": "CN202310000001"}\n\n', encoding='utf-8')

            self.assertEqual(database.import_from_jsonl(backup), 1)
            self.assertEqual(database.count(), 1)

    def test_legacy_append_backup_replays_repeated_applications(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            database = PatentsDB(root / 'patents.db')
            backup = root / 'backup.jsonl'
            records = [
                {'application_no': 'CN202310000001', 'status_code': -1},
                {'application_no': 'CN202310000001', 'status_code': 200, 'zhuanlimc': 'collected'},
            ]
            backup.write_text(''.join(json.dumps(record) + '\n' for record in records), encoding='utf-8')

            self.assertEqual(database.import_from_jsonl(backup), 2)
            self.assertEqual(database.count(), 1)
            self.assertEqual(database.get_all_records()[0]['status_code'], 200)
            self.assertEqual(database.get_all_records()[0]['zhuanlimc'], 'collected')


class TestDatabaseRebuild(unittest.TestCase):
    def run_rebuild(self, database_path: Path, backup_path: Path):
        with patch.object(sync, 'PATENTS_DB_FILE', database_path), patch.object(
            sync, 'DETECTION_LOG_JSONL_FILE', backup_path
        ), patch.object(
            sync, 'LOG_FILE', str(backup_path)
        ), patch.object(
            sync, 'require_database_rebuild_authorization', return_value='replica'
        ), patch.object(sync.sys, 'argv', ['sync.py', 'rebuild']), redirect_stdout(io.StringIO()):
            return sync.cmd_rebuild()

    def test_rebuild_removes_extra_records_and_restores_null_exactly(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            database_path = root / 'patents.db'
            backup_path = root / 'backup.jsonl'
            database = PatentsDB(database_path)
            database.upsert({
                'application_no': 'CN202310000001',
                'status_code': 200,
                'zhuanlimc': 'stale title',
            })
            database.upsert({
                'application_no': 'CN202310000002',
                'zhuanlimc': 'record absent from snapshot',
            })
            backup_path.write_text(json.dumps({
                'application_no': 'CN202310000001',
                'status_code': None,
                'zhuanlimc': None,
            }) + '\n', encoding='utf-8')

            self.run_rebuild(database_path, backup_path)

            restored_records = PatentsDB(database_path).get_all_records()
            self.assertEqual(len(restored_records), 1)
            self.assertEqual(restored_records[0]['application_no'], 'CN202310000001')
            self.assertIsNone(restored_records[0]['status_code'])
            self.assertIsNone(restored_records[0]['zhuanlimc'])

    def test_rebuild_replays_legacy_append_merge_semantics(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            database_path = root / 'patents.db'
            backup_path = root / 'backup.jsonl'
            backup_path.write_text(''.join(
                json.dumps(record) + '\n'
                for record in (
                    {
                        'application_no': 'CN202310000001',
                        'zhuanlimc': 'preserved title',
                        'status_code': 200,
                        'error_message': 'transient error',
                        'payable_fee_records': [{'fee': 'current'}],
                        'fee_snapshot_at': '2026-09-06T00:00:00Z',
                    },
                    {
                        'application_no': 'CN202310000001',
                        'zhuanlimc': None,
                        'status_code': 0,
                        'error_message': None,
                        'payable_fee_records': [{'fee': 'older'}],
                        'fee_snapshot_at': '2026-09-05T00:00:00Z',
                    },
                )
            ), encoding='utf-8')

            self.run_rebuild(database_path, backup_path)

            restored_record = PatentsDB(database_path).get_all_records()[0]
            self.assertEqual(restored_record['zhuanlimc'], 'preserved title')
            self.assertEqual(restored_record['status_code'], 0)
            self.assertIsNone(restored_record['error_message'])
            self.assertEqual(restored_record['payable_fee_records'], [{'fee': 'current'}])
            self.assertEqual(restored_record['fee_snapshot_at'], '2026-09-06T00:00:00Z')

    def test_rebuild_preserves_operational_tables(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            database_path = root / 'patents.db'
            backup_path = root / 'backup.jsonl'
            database = PatentsDB(database_path)
            database.replace_fee_targets(['CN202310000001'])
            database.submit_request(['CN202310000001'], '127.0.0.1')
            database.record_collection_failure('fees', 'CN202310000001', 'temporary failure')
            backup_path.write_text(
                json.dumps({'application_no': 'CN202310000002', 'zhuanlimc': 'replacement'}) + '\n',
                encoding='utf-8',
            )

            self.run_rebuild(database_path, backup_path)

            with database._connect() as connection:
                self.assertEqual(connection.execute('SELECT COUNT(*) FROM fee_targets').fetchone()[0], 1)
                self.assertEqual(connection.execute('SELECT COUNT(*) FROM requests').fetchone()[0], 1)
                self.assertEqual(connection.execute('SELECT COUNT(*) FROM collection_failures').fetchone()[0], 1)

    def test_corrupt_database_requires_offline_recovery(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            database_path = root / 'patents.db'
            backup_path = root / 'backup.jsonl'
            database_path.write_bytes(b'not a sqlite database')
            backup_path.write_text(
                json.dumps({'application_no': 'CN202310000001', 'zhuanlimc': 'restored'}) + '\n',
                encoding='utf-8',
            )

            with self.assertRaisesRegex(RuntimeError, '停止 Dashboard'):
                self.run_rebuild(database_path, backup_path)

            self.assertEqual(database_path.read_bytes(), b'not a sqlite database')

    def test_corrupt_operational_table_is_not_reported_as_rebuilt(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            database_path = root / 'patents.db'
            backup_path = root / 'backup.jsonl'
            database = PatentsDB(database_path)
            database.upsert({'application_no': 'CN202310000001', 'zhuanlimc': 'preserved'})
            with sqlite3.connect(database_path) as connection:
                connection.executemany(
                    "INSERT INTO requests(id, payload, requester, note, created_at) "
                    "VALUES (?, '[]', 'test', ?, '2026-09-06T00:00:00Z')",
                    ((f'request-{index}', 'x' * 3000) for index in range(100)),
                )
                connection.commit()
                connection.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()
                connection.execute('PRAGMA journal_mode=DELETE').fetchone()
                page_size = connection.execute('PRAGMA page_size').fetchone()[0]
                try:
                    request_pages = connection.execute(
                        "SELECT pageno FROM dbstat WHERE name='requests' AND pagetype='leaf' ORDER BY pageno"
                    ).fetchall()
                except sqlite3.OperationalError:
                    self.skipTest('SQLite build does not provide the dbstat virtual table')
            self.assertGreater(len(request_pages), 2)
            corrupt_page = request_pages[len(request_pages) // 2][0]
            with database_path.open('r+b') as database_file:
                database_file.seek((corrupt_page - 1) * page_size)
                database_file.write(b'\0')
                database_file.flush()
                os.fsync(database_file.fileno())
            corrupt_database_bytes = database_path.read_bytes()
            backup_path.write_text(
                json.dumps({'application_no': 'CN202310000002', 'zhuanlimc': 'replacement'}) + '\n',
                encoding='utf-8',
            )

            with self.assertRaisesRegex(RuntimeError, '完整性检查失败'):
                self.run_rebuild(database_path, backup_path)

            self.assertEqual(database_path.read_bytes(), corrupt_database_bytes)
            with sqlite3.connect(database_path) as connection:
                self.assertEqual(
                    connection.execute(
                        'SELECT zhuanlimc FROM patents WHERE application_no=?',
                        ('CN202310000001',),
                    ).fetchone()[0],
                    'preserved',
                )

    def test_invalid_snapshot_leaves_existing_database_unchanged(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            database_path = root / 'patents.db'
            backup_path = root / 'backup.jsonl'
            database = PatentsDB(database_path)
            database.upsert({'application_no': 'CN202310000001', 'zhuanlimc': 'preserved'})
            expected_records = database.get_all_records()
            backup_path.write_text(
                json.dumps({'application_no': 'CN202310000002'}) + '\n{broken\n',
                encoding='utf-8',
            )

            with self.assertRaisesRegex(ValueError, '2'):
                self.run_rebuild(database_path, backup_path)

            self.assertEqual(PatentsDB(database_path).get_all_records(), expected_records)
            self.assertEqual(list(root.glob('patents.db.*.tmp*')), [])

    def test_empty_snapshot_does_not_clear_existing_patents(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            database_path = root / 'patents.db'
            backup_path = root / 'backup.jsonl'
            database = PatentsDB(database_path)
            database.upsert({'application_no': 'CN202310000001', 'zhuanlimc': 'preserved'})
            previous_records = database.get_all_records()
            backup_path.write_text('\n', encoding='utf-8')

            with self.assertRaisesRegex(ValueError, '不包含专利记录'):
                self.run_rebuild(database_path, backup_path)

            self.assertEqual(database.get_all_records(), previous_records)

    def test_patent_write_waits_until_snapshot_rebuild_commits(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            database_path = root / 'patents.db'
            backup_path = root / 'backup.jsonl'
            database = PatentsDB(database_path)
            database.upsert({'application_no': 'CN202310000001', 'zhuanlimc': 'replaced'})
            backup_path.write_text(
                json.dumps({'application_no': 'CN202310000002', 'zhuanlimc': 'snapshot'}) + '\n',
                encoding='utf-8',
            )
            snapshot_read = threading.Event()
            release_snapshot = threading.Event()
            writer_finished = threading.Event()
            thread_errors = []
            original_read_snapshot = PatentsDB._read_jsonl_records

            def paused_snapshot_read(path):
                records = original_read_snapshot(path)
                snapshot_read.set()
                if not release_snapshot.wait(5):
                    raise TimeoutError('test did not release database rebuild')
                return records

            def rebuild_snapshot():
                try:
                    rebuild_patents_database_from_jsonl(database_path, backup_path)
                except Exception as error:
                    thread_errors.append(error)

            def write_new_patent():
                try:
                    database.upsert({
                        'application_no': 'CN202310000003',
                        'zhuanlimc': 'concurrent write',
                    })
                except Exception as error:
                    thread_errors.append(error)
                finally:
                    writer_finished.set()

            with patch.object(PatentsDB, '_read_jsonl_records', side_effect=paused_snapshot_read):
                rebuild_thread = threading.Thread(target=rebuild_snapshot)
                writer_thread = threading.Thread(target=write_new_patent)
                try:
                    rebuild_thread.start()
                    self.assertTrue(snapshot_read.wait(5))
                    writer_thread.start()
                    self.assertFalse(writer_finished.wait(0.2))
                    release_snapshot.set()
                    rebuild_thread.join(timeout=5)
                    writer_thread.join(timeout=5)
                finally:
                    release_snapshot.set()
                    rebuild_thread.join(timeout=5)
                    writer_thread.join(timeout=5)

            self.assertFalse(rebuild_thread.is_alive())
            self.assertFalse(writer_thread.is_alive())
            self.assertEqual(thread_errors, [])
            self.assertEqual(
                database.get_all_app_nos(),
                {'CN202310000002', 'CN202310000003'},
            )

    def test_failed_transaction_leaves_existing_database_unchanged(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            database_path = root / 'patents.db'
            backup_path = root / 'backup.jsonl'
            database = PatentsDB(database_path)
            database.upsert({'application_no': 'CN202310000001', 'zhuanlimc': 'preserved'})
            previous_records = database.get_all_records()
            backup_path.write_text(
                json.dumps({'application_no': 'CN202310000002', 'zhuanlimc': 'replacement'}) + '\n',
                encoding='utf-8',
            )
            with database._connect() as connection:
                connection.execute(
                    "CREATE TRIGGER reject_snapshot_row BEFORE INSERT ON patents "
                    "WHEN NEW.application_no='CN202310000002' "
                    "BEGIN SELECT RAISE(ABORT, 'snapshot rejected'); END"
                )
                connection.commit()

            with self.assertRaisesRegex(sqlite3.IntegrityError, 'snapshot rejected'):
                self.run_rebuild(database_path, backup_path)

            self.assertEqual(database.get_all_records(), previous_records)

    def test_active_wal_connection_observes_transactional_rebuild(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            database_path = root / 'patents.db'
            backup_path = root / 'backup.jsonl'
            database = PatentsDB(database_path)
            database.upsert({'application_no': 'CN202310000001', 'zhuanlimc': 'preserved'})
            backup_path.write_text(
                json.dumps({'application_no': 'CN202310000002', 'zhuanlimc': 'replacement'}) + '\n',
                encoding='utf-8',
            )
            with database._connect() as active_connection:
                self.run_rebuild(database_path, backup_path)
                self.assertEqual(
                    active_connection.execute(
                        'SELECT zhuanlimc FROM patents WHERE application_no=?',
                        ('CN202310000002',),
                    ).fetchone()[0],
                    'replacement',
                )
            self.assertEqual(PatentsDB(database_path).get_all_app_nos(), {'CN202310000002'})

    def test_active_writer_is_reported_as_busy_without_offline_recovery_advice(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            database_path = root / 'patents.db'
            backup_path = root / 'backup.jsonl'
            database = PatentsDB(database_path)
            database.upsert({'application_no': 'CN202310000001', 'zhuanlimc': 'preserved'})
            database.replace_fee_targets(['CN202310000001'])
            backup_path.write_text(
                json.dumps({'application_no': 'CN202310000002', 'zhuanlimc': 'replacement'}) + '\n',
                encoding='utf-8',
            )
            sqlite_connect = sqlite3.connect

            def connect_without_wait(database_file, *args, **kwargs):
                kwargs['timeout'] = 0.01
                return sqlite_connect(database_file, *args, **kwargs)

            with sqlite3.connect(database_path) as active_writer:
                active_writer.execute('BEGIN IMMEDIATE')
                with patch('db_manager.sqlite3.connect', side_effect=connect_without_wait):
                    with self.assertRaisesRegex(RuntimeError, '另一个写任务') as stopped:
                        self.run_rebuild(database_path, backup_path)

            self.assertNotIn('移出', str(stopped.exception))
            self.assertEqual(database.get_all_app_nos(), {'CN202310000001'})
            with database._connect() as connection:
                self.assertEqual(connection.execute('SELECT COUNT(*) FROM fee_targets').fetchone()[0], 1)
