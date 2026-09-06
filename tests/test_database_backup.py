import sqlite3
import tempfile
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


if __name__ == '__main__':
    unittest.main()
