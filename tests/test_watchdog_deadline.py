import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import collection_health
import collection_watchdog


class TestWatchdogDeadline(unittest.TestCase):
    def setUp(self):
        temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        self.heartbeat_path = Path(temporary_directory.name) / 'heartbeat.json'
        self.alert_path = Path(temporary_directory.name) / 'alert.json'
        for constant, path in (
            ('COLLECTION_HEARTBEAT_FILE', self.heartbeat_path),
            ('ALERT_STATUS_FILE', self.alert_path),
            ('WATCHDOG_EVENTS_FILE', Path(temporary_directory.name) / 'events.jsonl'),
        ):
            state_path_patch = patch.object(collection_health, constant, path)
            state_path_patch.start()
            self.addCleanup(state_path_patch.stop)
        self.heartbeat = {
            'status': 'running',
            'timestamp': '2026-09-06T12:00:00Z',
            'application_no': '2023001',
            'completed': 1,
            'total': 2,
            'consecutive_failures': 0,
        }

    def test_heartbeat_reader_rejects_invalid_json_and_field_types(self):
        invalid_documents = ['{', '[]', 'null', '42', 'true', '"heartbeat"', '{}']
        for field, value in (
            ('timestamp', '2026-09-06T12:00:00'),
            ('timestamp', 'not-a-date'),
            ('timestamp', 123),
            ('status', ['running']),
            ('application_no', ['2023001']),
            ('completed', True),
            ('total', -1),
            ('consecutive_failures', '10'),
        ):
            invalid_documents.append(json.dumps({**self.heartbeat, field: value}))
        for serialized_heartbeat in invalid_documents:
            with self.subTest(heartbeat=serialized_heartbeat):
                self.heartbeat_path.write_text(serialized_heartbeat, encoding='utf-8')
                self.assertIsNone(collection_health.read_collection_heartbeat())
        self.heartbeat_path.write_bytes(b'\xff')
        self.assertIsNone(collection_health.read_collection_heartbeat())

    def test_alert_reader_rejects_invalid_json_and_field_types(self):
        alert = {
            'status': 'alert',
            'reason': 'login_required',
            'details': 'Login is required',
            'timestamp': self.heartbeat['timestamp'],
            'restart_count': 0,
        }
        invalid_documents = ['{', '[]', 'null', '42', 'true', '"alert"', '{}']
        for field, value in (
            ('timestamp', '2026-09-06T12:00:00'),
            ('timestamp', 'not-a-date'),
            ('timestamp', []),
            ('reason', ['login_required']),
            ('details', None),
            ('status', 'unknown'),
            ('restart_count', '1'),
            ('restart_count', True),
            ('restart_count', -1),
        ):
            invalid_documents.append(json.dumps({**alert, field: value}))
        for serialized_alert in invalid_documents:
            with self.subTest(alert=serialized_alert):
                self.alert_path.write_text(serialized_alert, encoding='utf-8')
                self.assertEqual(collection_health.read_alert_status()['status'], 'invalid')
        self.alert_path.write_bytes(b'\xff')
        self.assertEqual(collection_health.read_alert_status()['status'], 'invalid')

    def test_missing_state_and_writer_round_trips_remain_serializable(self):
        self.assertIsNone(collection_health.read_collection_heartbeat())
        self.assertEqual(collection_health.read_alert_status()['status'], 'unknown')
        for write_heartbeat, arguments, expected_status in (
            (collection_health.write_collection_start_heartbeat, (2,), 'starting'),
            (collection_health.write_collection_progress_heartbeat, ('2023001', 1, 2, 0), 'running'),
            (collection_health.write_collection_stopped_heartbeat, (2, 2), 'stopped'),
        ):
            write_heartbeat(*arguments)
            heartbeat = collection_health.read_collection_heartbeat()
            self.assertEqual(json.loads(json.dumps(heartbeat))['status'], expected_status)
        collection_health.clear_collection_alert()
        self.assertEqual(json.loads(json.dumps(collection_health.read_alert_status()))['status'], 'ok')

    def test_missing_and_invalid_heartbeat_reach_original_startup_deadline(self):
        child = MagicMock(pid=12345)
        child.poll.return_value = None
        disk_usage = type('DiskUsage', (), {'free': 100 * 1024 ** 3})()
        invalid_heartbeat = json.dumps({**self.heartbeat, 'timestamp': '2026-09-06T12:00:00'})
        with patch.object(collection_watchdog.time, 'monotonic', side_effect=[100, 101, 105, 108, 111]), patch.object(
            collection_watchdog.shutil, 'disk_usage', return_value=disk_usage
        ), patch.object(collection_watchdog, 'WATCHDOG_HEARTBEAT_TIMEOUT_SECONDS', 10):
            deadline = collection_watchdog.CollectionHeartbeatDeadline()
            self.assertIsNone(collection_watchdog.supervision_failure(child, deadline))
            for serialized_heartbeat in ('{', '[]'):
                self.heartbeat_path.write_text(serialized_heartbeat, encoding='utf-8')
                self.assertIsNone(collection_watchdog.supervision_failure(child, deadline))
            self.heartbeat_path.write_text(invalid_heartbeat, encoding='utf-8')
            failure = collection_watchdog.supervision_failure(child, deadline)
        self.assertEqual(failure, ('heartbeat_timeout', '采集心跳已中断 11 秒'))

    def test_only_newer_valid_heartbeat_renews_deadline(self):
        wall_clock = datetime(2026, 9, 6, 12, tzinfo=timezone.utc)
        with patch.object(collection_watchdog.time, 'monotonic', side_effect=[100, 100, 108, 109, 112, 115]), patch.object(
            collection_watchdog, 'datetime', wraps=datetime
        ) as utc_clock:
            utc_clock.now.return_value = wall_clock
            deadline = collection_watchdog.CollectionHeartbeatDeadline()
            self.assertEqual(deadline.elapsed_seconds(self.heartbeat), 0)
            self.assertEqual(deadline.elapsed_seconds(self.heartbeat), 8)
            utc_clock.now.return_value = wall_clock + timedelta(seconds=9)
            newer_heartbeat = {**self.heartbeat, 'timestamp': '2026-09-06T12:00:09Z'}
            self.assertEqual(deadline.elapsed_seconds(newer_heartbeat), 0)
            self.assertEqual(deadline.elapsed_seconds(self.heartbeat), 3)
            self.assertEqual(deadline.elapsed_seconds(None), 6)

    def test_valid_heartbeat_after_corruption_recovers_until_next_timeout(self):
        child = MagicMock(pid=12345)
        child.poll.return_value = None
        disk_usage = type('DiskUsage', (), {'free': 100 * 1024 ** 3})()
        with patch.object(collection_watchdog.time, 'monotonic', side_effect=[100, 101, 105, 109, 112, 120]), patch.object(
            collection_watchdog.shutil, 'disk_usage', return_value=disk_usage
        ), patch.object(collection_watchdog, 'WATCHDOG_HEARTBEAT_TIMEOUT_SECONDS', 10), patch.object(
            collection_watchdog, 'datetime', wraps=datetime
        ) as utc_clock:
            utc_clock.now.return_value = datetime(2026, 9, 6, 12, 0, 9, tzinfo=timezone.utc)
            deadline = collection_watchdog.CollectionHeartbeatDeadline()
            self.assertIsNone(collection_watchdog.supervision_failure(child, deadline))
            self.heartbeat_path.write_text('{', encoding='utf-8')
            self.assertIsNone(collection_watchdog.supervision_failure(child, deadline))
            self.heartbeat_path.write_text(json.dumps({
                **self.heartbeat, 'timestamp': '2026-09-06T12:00:09Z',
            }), encoding='utf-8')
            self.assertIsNone(collection_watchdog.supervision_failure(child, deadline))
            self.heartbeat_path.write_text('[]', encoding='utf-8')
            self.assertIsNone(collection_watchdog.supervision_failure(child, deadline))
            failure = collection_watchdog.supervision_failure(child, deadline)
        self.assertEqual(failure, ('heartbeat_timeout', '采集心跳已中断 11 秒'))

    def test_future_timestamp_and_clock_changes_do_not_extend_repeated_heartbeat(self):
        wall_clock = datetime(2026, 9, 6, 12, tzinfo=timezone.utc)
        future_heartbeat = {**self.heartbeat, 'timestamp': '2099-01-01T00:00:00Z'}
        equivalent_heartbeat = {**future_heartbeat, 'timestamp': '2099-01-01T08:00:00+08:00'}
        with patch.object(collection_watchdog.time, 'monotonic', side_effect=[100, 100, 106, 111]), patch.object(
            collection_watchdog, 'datetime', wraps=datetime
        ) as utc_clock:
            utc_clock.now.return_value = wall_clock
            deadline = collection_watchdog.CollectionHeartbeatDeadline()
            self.assertEqual(deadline.elapsed_seconds(future_heartbeat), 0)
            utc_clock.now.return_value = wall_clock - timedelta(days=1)
            self.assertEqual(deadline.elapsed_seconds(equivalent_heartbeat), 6)
            utc_clock.now.return_value = wall_clock + timedelta(days=1)
            self.assertEqual(deadline.elapsed_seconds(future_heartbeat), 11)

    def test_supervisor_cleans_up_on_monitoring_exception_and_interrupt(self):
        for monitoring_error in (OSError('disk unavailable'), RuntimeError('monitor failed'), KeyboardInterrupt()):
            with self.subTest(exception=type(monitoring_error).__name__):
                child = MagicMock(pid=12345)
                with patch.object(collection_watchdog, '_stop_requested', False), patch.object(
                    collection_watchdog, 'start_collection_process', return_value=child
                ), patch.object(
                    collection_watchdog, 'supervision_failure', side_effect=monitoring_error
                ), patch.object(collection_watchdog, 'terminate_process_tree') as terminate:
                    with self.assertRaises(type(monitoring_error)):
                        collection_watchdog._supervise_collection_batch('a' * 32)
                terminate.assert_called_once_with(child)

    def test_supervisor_cleans_up_on_completed_batch(self):
        child = MagicMock(pid=12345)
        child.poll.return_value = 0
        with patch.object(collection_watchdog, '_stop_requested', False), patch.object(
            collection_watchdog, 'start_collection_process', return_value=child
        ), patch.object(collection_watchdog, 'supervision_failure', return_value=None), patch.object(
            collection_watchdog, 'read_collection_batch', return_value={'status': 'completed', 'remaining': 0}
        ), patch.object(collection_watchdog, 'terminate_process_tree') as terminate:
            self.assertEqual(collection_watchdog._supervise_collection_batch('a' * 32), 0)
        terminate.assert_called_once_with(child)

    def test_supervisor_cleans_up_when_stop_requested(self):
        child = MagicMock(pid=12345)

        def stop_during_poll(_child, _deadline):
            collection_watchdog._request_stop(None, None)
            return None

        with patch.object(collection_watchdog, '_stop_requested', False), patch.object(
            collection_watchdog, 'start_collection_process', return_value=child
        ) as start_child, patch.object(
            collection_watchdog, 'supervision_failure', side_effect=stop_during_poll
        ), patch.object(collection_watchdog.time, 'sleep'), patch.object(
            collection_watchdog, 'terminate_process_tree'
        ) as terminate:
            self.assertEqual(collection_watchdog._supervise_collection_batch('a' * 32), 0)
        terminate.assert_called_once_with(child)
        start_child.assert_called_once_with('a' * 32)

    def test_new_run_resets_previous_stop_request(self):
        with patch.object(collection_watchdog, '_stop_requested', True), patch.object(
            collection_watchdog.signal, 'signal'
        ), patch.object(collection_watchdog, 'reserve_supervised_collection'), patch.object(
            collection_watchdog, 'reserve_detail_collection_desktop'
        ), patch.object(collection_watchdog, 'select_main_collection_targets', return_value=['2023001']), patch.object(
            collection_watchdog, 'require_main_mitm_proxy'
        ), patch.object(collection_watchdog.CollectionBatch, 'prepare', return_value='a' * 32), patch.object(
            collection_watchdog, '_supervise_collection_batch', return_value=0
        ) as supervise:
            self.assertEqual(collection_watchdog.run_supervised_collection(), 0)
            self.assertFalse(collection_watchdog._stop_requested)
        supervise.assert_called_once_with('a' * 32)
