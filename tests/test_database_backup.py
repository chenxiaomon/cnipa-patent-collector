import json
import sqlite3
import tempfile
import threading
import unittest
from contextlib import closing, nullcontext
from pathlib import Path
from unittest.mock import Mock, patch

from db_manager import PatentsDB


class TestDatabaseBackup(unittest.TestCase):
    def test_backup_restores_committed_wal_and_all_database_tables(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source_path = root / 'patents.db'
            backup_path = root / 'backup.db'
            database = PatentsDB(source_path)
            application_no = '202310411762X'
            with closing(sqlite3.connect(source_path)) as writer:
                writer.execute('PRAGMA wal_autocheckpoint=0')
                writer.execute(
                    'INSERT INTO patents(application_no, status_code, zhuanlimc) VALUES (?, 200, ?)',
                    (application_no, 'committed in WAL'),
                )
                writer.commit()
                database.replace_fee_targets([application_no])
                database.submit_request([application_no], '127.0.0.1', 'pending request')
                database.record_collection_failure('fees', application_no, 'retry needed')
                self.assertGreater(Path(str(source_path) + '-wal').stat().st_size, 0)

                database.backup_to(backup_path)

                self.assertEqual(list(root.glob('backup.db-*')), [])
                with closing(sqlite3.connect(backup_path)) as restored:
                    self.assertEqual(restored.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
                    self.assertEqual(restored.execute('PRAGMA journal_mode').fetchone()[0], 'delete')
                    for table in ('patents', 'fee_targets', 'requests', 'collection_failures'):
                        with self.subTest(table=table):
                            self.assertEqual(
                                restored.execute(f'SELECT * FROM {table}').fetchall(),
                                writer.execute(f'SELECT * FROM {table}').fetchall(),
                            )

    def test_interrupted_backup_does_not_publish_partial_database(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            database = PatentsDB(root / 'patents.db')
            previous_path = root / 'previous.db'
            database.backup_to(previous_path)
            previous_bytes = previous_path.read_bytes()

            def interrupt_backup(destination):
                destination.execute('CREATE TABLE partial_copy(value TEXT)')
                destination.commit()
                raise sqlite3.OperationalError('backup interrupted')

            interrupted_source = Mock()
            interrupted_source.backup.side_effect = interrupt_backup
            for backup_path in (root / 'new.db', previous_path):
                with self.subTest(backup_path=backup_path), patch.object(
                    database, '_connect', return_value=nullcontext(interrupted_source)
                ):
                    with self.assertRaisesRegex(sqlite3.OperationalError, 'interrupted'):
                        database.backup_to(backup_path)

            self.assertFalse((root / 'new.db').exists())
            self.assertEqual(previous_path.read_bytes(), previous_bytes)
            self.assertEqual(list(root.glob('*.tmp*')), [])

    def test_failed_atomic_publication_preserves_previous_backup(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            database = PatentsDB(root / 'patents.db')
            backup_path = root / 'backup.db'
            database.backup_to(backup_path)
            previous_bytes = backup_path.read_bytes()
            database.upsert({'application_no': '202310411762X', 'status_code': 200})

            with patch('db_manager.os.replace', side_effect=PermissionError('backup locked')):
                with self.assertRaises(PermissionError):
                    database.backup_to(backup_path)

            self.assertEqual(backup_path.read_bytes(), previous_bytes)
            self.assertEqual(list(root.glob('*.tmp*')), [])
            self.assertEqual(database.count(), 1)

    def test_backup_cannot_replace_source_database(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            database_path = Path(tmpdir) / 'patents.db'
            database = PatentsDB(database_path)
            database.upsert({'application_no': '202310411762X', 'status_code': 200})

            with self.assertRaises(ValueError):
                database.backup_to(database_path)

            self.assertEqual(database.count(), 1)

    def test_concurrent_jsonl_exports_cannot_publish_an_older_snapshot_last(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            database = PatentsDB(root / 'patents.db')
            database.upsert({'application_no': '202310411762X', 'status_code': 200})
            snapshot_path = root / 'snapshot.jsonl'
            first_snapshot_read = threading.Event()
            release_first_export = threading.Event()
            second_snapshot_read = threading.Event()
            snapshot_counter_lock = threading.Lock()
            snapshot_counter = 0
            failures = []

            original_get_all_records = database.get_all_records

            def staged_snapshot_read():
                nonlocal snapshot_counter
                records = original_get_all_records()
                with snapshot_counter_lock:
                    snapshot_number = snapshot_counter
                    snapshot_counter += 1
                if snapshot_number == 0:
                    first_snapshot_read.set()
                    if not release_first_export.wait(5):
                        raise TimeoutError('test did not release first JSONL export')
                else:
                    second_snapshot_read.set()
                return records

            def export_snapshot():
                try:
                    database.export_to_jsonl(snapshot_path)
                except Exception as error:
                    failures.append(error)

            with patch.object(database, 'get_all_records', side_effect=staged_snapshot_read):
                exporters = [threading.Thread(target=export_snapshot) for _ in range(2)]
                try:
                    exporters[0].start()
                    self.assertTrue(first_snapshot_read.wait(5))
                    database.upsert({'application_no': '2024100659780', 'status_code': 200})
                    exporters[1].start()
                    self.assertFalse(second_snapshot_read.wait(0.2))
                    release_first_export.set()
                    for exporter in exporters:
                        exporter.join(timeout=5)
                finally:
                    release_first_export.set()
                    for exporter in exporters:
                        if exporter.is_alive():
                            exporter.join(timeout=5)

            self.assertTrue(all(not exporter.is_alive() for exporter in exporters))
            self.assertEqual(failures, [])
            application_nos = {
                json.loads(line)['application_no']
                for line in snapshot_path.read_text(encoding='utf-8').splitlines()
            }
            self.assertEqual(application_nos, {'202310411762X', '2024100659780'})
            self.assertEqual(list(root.glob('snapshot.jsonl.*.tmp')), [])


if __name__ == '__main__':
    unittest.main()
