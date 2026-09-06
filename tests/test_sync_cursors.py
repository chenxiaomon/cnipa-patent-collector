import multiprocessing
import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import db_manager
from db_manager import SYNC_CURSOR_FIELD, PatentsDB, normalize_sync_cursor


class _FrozenClock(datetime):
    @classmethod
    def now(cls, tz=None):
        instant = cls(2026, 9, 6, 12, 0, 0, tzinfo=timezone.utc)
        return instant if tz else instant.replace(tzinfo=None)


def _write_concurrent_patents(db_path, prefix, start_writes):
    with patch.object(db_manager, 'datetime', _FrozenClock):
        patents = PatentsDB(Path(db_path))
        if not start_writes.wait(10):
            raise RuntimeError('Timed out waiting to start concurrent writes')
        for index in range(5):
            patents.upsert({'application_no': f'{prefix}{index}'})


class TestSyncCursors(unittest.TestCase):
    def test_all_write_operations_advance_within_one_clock_tick(self):
        with tempfile.TemporaryDirectory() as tmpdir, patch.object(db_manager, 'datetime', _FrozenClock):
            patents = PatentsDB(Path(tmpdir) / 'patents.db')
            business_timestamp = '2020-01-01T00:00:00Z'
            fee_timestamp = '2026-09-01T08:00:00+08:00'
            patents.upsert({
                'application_no': 'A', 'timestamp': business_timestamp,
                'anjianywzt': 'active', 'status_code': 200,
            })
            previous_cursor = patents.export_delta('1970-01-01T00:00:00Z')[-1][SYNC_CURSOR_FIELD]
            operations = [
                lambda: patents.upsert({'application_no': 'A', 'zhuanlimc': 'new title'}),
                lambda: patents.update_fields('A', {'daili_jg': 'agency'}),
                lambda: patents.update_fee_snapshot('A', {
                    'fee_snapshot_at': fee_timestamp, 'paid_fee_records': [],
                }),
                patents.snapshot_previous_status,
                lambda: patents.upsert_batch([{'application_no': 'B'}, {'application_no': 'C'}]),
                lambda: patents.apply_master_delta([
                    {'application_no': 'B', 'zhuanlimc': 'master title'},
                    {'application_no': 'C', 'zhuanlimc': 'master title'},
                ]),
                patents.mark_unattempted_records_pending,
            ]
            for operation in operations:
                with self.subTest(operation=operations.index(operation)):
                    operation()
                    delta = patents.export_delta(previous_cursor)
                    self.assertTrue(delta)
                    next_cursor = delta[-1][SYNC_CURSOR_FIELD]
                    self.assertGreater(next_cursor, previous_cursor)
                    self.assertRegex(next_cursor, r'^2026-09-06T12:00:00\.\d{6}Z$')
                    self.assertEqual({patent[SYNC_CURSOR_FIELD] for patent in delta}, {next_cursor})
                    previous_cursor = next_cursor
            self.assertEqual(patents.get_record('A')['timestamp'], business_timestamp)
            self.assertEqual(patents.get_record('A')['fee_snapshot_at'], fee_timestamp)

    def test_clock_rollback_keeps_later_commit_visible(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            patents = PatentsDB(Path(tmpdir) / 'patents.db')
            with patch.object(db_manager, 'datetime', _FrozenClock):
                patents.upsert({'application_no': 'A'})
                cursor = patents.export_delta('1970-01-01T00:00:00Z')[0][SYNC_CURSOR_FIELD]
            with patch.object(db_manager, 'datetime', wraps=datetime) as clock:
                clock.now.return_value = datetime(2020, 1, 1, tzinfo=timezone.utc)
                patents.upsert({'application_no': 'B'})
            delta = patents.export_delta(cursor)
            self.assertEqual([patent['application_no'] for patent in delta], ['B'])
            self.assertEqual(delta[0][SYNC_CURSOR_FIELD], '2026-09-06T12:00:00.000001Z')

    def test_encoding_before_another_commit_cannot_backdate_the_later_commit(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / 'patents.db'
            delayed_writer = PatentsDB(db_path)
            fast_writer = PatentsDB(db_path)
            encoded = threading.Event()
            release_writer = threading.Event()
            original_encode = delayed_writer._encode

            def pause_after_encoding(record):
                encoded_patent = original_encode(record)
                encoded.set()
                if not release_writer.wait(10):
                    raise RuntimeError('Timed out waiting to resume delayed writer')
                return encoded_patent

            with patch.object(delayed_writer, '_encode', side_effect=pause_after_encoding):
                with ThreadPoolExecutor(max_workers=1) as executor:
                    delayed_write = executor.submit(delayed_writer.upsert, {'application_no': 'A'})
                    try:
                        self.assertTrue(encoded.wait(5))
                        fast_writer.upsert({'application_no': 'B'})
                        cursor = fast_writer.export_delta('1970-01-01T00:00:00Z')[0][SYNC_CURSOR_FIELD]
                    finally:
                        release_writer.set()
                    delayed_write.result(timeout=5)
            self.assertEqual(
                [patent['application_no'] for patent in fast_writer.export_delta(cursor)], ['A']
            )

    def test_separate_processes_share_one_commit_order(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / 'patents.db'
            PatentsDB(db_path)
            context = multiprocessing.get_context('spawn')
            start_writes = context.Event()
            writers = [
                context.Process(target=_write_concurrent_patents, args=(str(db_path), prefix, start_writes))
                for prefix in ('A', 'B')
            ]
            try:
                for writer in writers:
                    writer.start()
                start_writes.set()
                for writer in writers:
                    writer.join(timeout=15)
                    self.assertEqual(writer.exitcode, 0)
            finally:
                for writer in writers:
                    if writer.is_alive():
                        writer.terminate()
                    writer.join(timeout=5)
            delta = PatentsDB(db_path).export_delta('1970-01-01T00:00:00Z')
            cursors = [patent[SYNC_CURSOR_FIELD] for patent in delta]
            self.assertEqual(len(cursors), 10)
            self.assertEqual(len(set(cursors)), 10)
            self.assertEqual(cursors[0], '2026-09-06T12:00:00.000000Z')
            self.assertEqual(cursors[-1], '2026-09-06T12:00:00.000009Z')

    def test_rollback_does_not_advance_committed_cursor(self):
        with tempfile.TemporaryDirectory() as tmpdir, patch.object(db_manager, 'datetime', _FrozenClock):
            patents = PatentsDB(Path(tmpdir) / 'patents.db')
            patents.upsert({'application_no': 'A', 'status_code': 200})
            cursor = patents.export_delta('1970-01-01T00:00:00Z')[0][SYNC_CURSOR_FIELD]
            with self.assertRaises(sqlite3.ProgrammingError):
                patents.apply_master_delta([
                    {'application_no': 'A', 'status_code': 0},
                    {'application_no': 'B', 'status_code': {'invalid': 'value'}},
                ])
            self.assertEqual(patents.export_delta(cursor), [])
            self.assertEqual(patents.get_record('A')['status_code'], 200)
            patents.upsert({'application_no': 'B'})
            next_cursor = patents.export_delta(cursor)[0][SYNC_CURSOR_FIELD]
            self.assertEqual(
                datetime.fromisoformat(next_cursor.replace('Z', '+00:00'))
                - datetime.fromisoformat(cursor.replace('Z', '+00:00')),
                timedelta(microseconds=1),
            )

    def test_legacy_cursor_migration_is_atomic_and_runs_once(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / 'patents.db'
            patents = PatentsDB(db_path)
            patents.upsert_batch([{'application_no': app_no} for app_no in ('A', 'B', 'C', 'D')])
            legacy_cursors = [
                ('2026-09-06T12:00:00Z', '2020-01-01T00:00:00Z', 'A'),
                ('2026-09-06T12:00:00.100000Z', None, 'B'),
                ('2026-09-06T20:00:00.200+08:00', None, 'C'),
                ('invalid', '2026-09-06T12:00:00.300', 'D'),
            ]
            with closing(sqlite3.connect(db_path)) as connection:
                connection.executemany(
                    'UPDATE patents SET updated_at=?, timestamp=? WHERE application_no=?', legacy_cursors
                )
                connection.execute('PRAGMA user_version=0')
                connection.commit()
            with ThreadPoolExecutor(max_workers=3) as executor:
                reopened = list(executor.map(PatentsDB, [db_path] * 3))[0]
            delta = reopened.export_delta('2026-09-06T20:00:00+08:00')
            self.assertEqual([patent['application_no'] for patent in delta], ['B', 'C', 'D'])
            self.assertEqual([patent[SYNC_CURSOR_FIELD] for patent in delta], [
                '2026-09-06T12:00:00.100000Z',
                '2026-09-06T12:00:00.200000Z',
                '2026-09-06T12:00:00.300000Z',
            ])
            self.assertEqual(reopened.get_record('A')['timestamp'], '2020-01-01T00:00:00Z')
            with patch.object(db_manager, 'parse_timestamp', wraps=db_manager.parse_timestamp) as parser:
                PatentsDB(db_path)
                parser.assert_not_called()

    def test_export_delta_normalizes_legacy_precision_and_rejects_invalid_cursor(self):
        with tempfile.TemporaryDirectory() as tmpdir, patch.object(db_manager, 'datetime', _FrozenClock):
            patents = PatentsDB(Path(tmpdir) / 'patents.db')
            patents.upsert({'application_no': 'A'})
            patents.upsert({'application_no': 'B'})
            for cursor in ('2026-09-06T12:00:00Z', '2026-09-06T12:00:00', '2026-09-06T20:00:00+08:00'):
                with self.subTest(cursor=cursor):
                    self.assertEqual(
                        [patent['application_no'] for patent in patents.export_delta(cursor)], ['B']
                    )
            with self.assertRaises(ValueError):
                patents.export_delta('invalid')
            self.assertEqual(normalize_sync_cursor('2026-09-06T12:00:00Z'), '2026-09-06T12:00:00.000000Z')
