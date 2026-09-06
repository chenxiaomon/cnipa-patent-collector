import io
import json
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import sync
from db_manager import PatentsDB


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
