#!/usr/bin/env python3

import json
import tempfile
import os
import subprocess
import sys
import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import collection_health
import collection_watchdog
from collection_watchdog import heartbeat_age_seconds


class _RunningProcess:
    pid = 12345
    returncode = None

    def poll(self):
        return None


class _FailedProcess:
    pid = 12345
    returncode = 1

    def poll(self):
        return self.returncode


class _SuccessfulProcess(_FailedProcess):
    returncode = 0


@contextmanager
def _reserved_operation(_operation_name):
    yield


class TestCollectionHealth(unittest.TestCase):
    def setUp(self):
        alert_patch = patch.object(collection_watchdog, 'read_alert_status', return_value={'status': 'ok'})
        alert_patch.start()
        self.addCleanup(alert_patch.stop)

    def test_progress_heartbeat_is_readable(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            heartbeat_path = Path(tmpdir) / 'heartbeat.json'
            with patch.object(collection_health, 'COLLECTION_HEARTBEAT_FILE', heartbeat_path):
                collection_health.write_collection_progress_heartbeat('2023001', 3, 10, 2)
                heartbeat = collection_health.read_collection_heartbeat()
            self.assertEqual(heartbeat['application_no'], '2023001')
            self.assertEqual(heartbeat['completed'], 3)
            self.assertEqual(heartbeat['consecutive_failures'], 2)

    def test_heartbeat_age_uses_utc_timestamp(self):
        timestamp = (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat()
        age = heartbeat_age_seconds({'timestamp': timestamp})
        self.assertGreaterEqual(age, 29)
        self.assertLess(age, 35)

    def test_alert_status_round_trip(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            alert_path = Path(tmpdir) / 'alert.json'
            events_path = Path(tmpdir) / 'events.jsonl'
            with patch.object(collection_health, 'ALERT_STATUS_FILE', alert_path), patch.object(
                collection_health, 'WATCHDOG_EVENTS_FILE', events_path
            ):
                collection_health.record_collection_alert('heartbeat_timeout', 'stale', 1)
                alert = collection_health.read_alert_status()
            self.assertEqual(alert['status'], 'alert')
            self.assertEqual(alert['reason'], 'heartbeat_timeout')
            self.assertTrue(events_path.read_text(encoding='utf-8').strip())

    def test_watchdog_detects_stale_heartbeat(self):
        stale_timestamp = (datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat()
        disk_usage = type('DiskUsage', (), {'free': 100 * 1024 ** 3})()
        with patch.object(collection_watchdog.shutil, 'disk_usage', return_value=disk_usage), patch.object(
            collection_watchdog, 'read_collection_heartbeat',
            return_value={'timestamp': stale_timestamp, 'consecutive_failures': 0},
        ), patch.object(collection_watchdog, 'WATCHDOG_HEARTBEAT_TIMEOUT_SECONDS', 600):
            failure = collection_watchdog.supervision_failure(_RunningProcess())
        self.assertEqual(failure[0], 'heartbeat_timeout')

    def test_watchdog_detects_consecutive_failures(self):
        disk_usage = type('DiskUsage', (), {'free': 100 * 1024 ** 3})()
        with patch.object(collection_watchdog.shutil, 'disk_usage', return_value=disk_usage), patch.object(
            collection_watchdog, 'read_collection_heartbeat',
            return_value={
                'timestamp': datetime.now(timezone.utc).isoformat(),
                'consecutive_failures': 20,
            },
        ):
            failure = collection_watchdog.supervision_failure(_RunningProcess())
        self.assertEqual(failure[0], 'consecutive_failures')

    def test_watchdog_stops_after_three_failed_restarts(self):
        with patch.object(collection_watchdog, '_stop_requested', False), patch.object(
            collection_watchdog, 'write_collection_start_heartbeat'
        ), patch.object(
            collection_watchdog, 'start_collection_process', side_effect=[
                _FailedProcess(), _FailedProcess(), _FailedProcess(),
            ]
        ) as start_process, patch.object(
            collection_watchdog, 'supervision_failure',
            return_value=('collection_exited', 'exit 1'),
        ), patch.object(collection_watchdog, 'terminate_process_tree'), patch.object(
            collection_watchdog, 'record_collection_alert'
        ) as record_alert, patch.object(collection_watchdog.time, 'sleep'), patch.object(
            collection_watchdog, 'read_collection_batch', return_value={'remaining': 2, 'succeeded': 0}
        ), patch.object(
            collection_watchdog, 'WATCHDOG_MAX_RESTARTS', 3
        ):
            exit_code = collection_watchdog._supervise_collection_batch('a' * 32)
        self.assertEqual(exit_code, 1)
        self.assertEqual(start_process.call_count, 3)
        self.assertEqual([call.args for call in start_process.call_args_list], [('a' * 32,)] * 3)
        self.assertEqual(record_alert.call_args_list[-1].args[0], 'restart_limit_reached')

    def test_new_successes_restart_the_failure_streak(self):
        batch_snapshots = [
            {'remaining': 10, 'succeeded': 0},
            {'remaining': 6, 'succeeded': 4},
            {'remaining': 6, 'succeeded': 4},
            {'remaining': 6, 'succeeded': 4},
        ]
        with patch.object(collection_watchdog, '_stop_requested', False), patch.object(
            collection_watchdog, 'write_collection_start_heartbeat'
        ), patch.object(
            collection_watchdog, 'start_collection_process', side_effect=[
                _FailedProcess(), _FailedProcess(), _FailedProcess(), _FailedProcess(),
            ]
        ) as start_process, patch.object(
            collection_watchdog, 'supervision_failure',
            return_value=('collection_exited', 'exit 1'),
        ), patch.object(collection_watchdog, 'terminate_process_tree'), patch.object(
            collection_watchdog, 'record_collection_alert'
        ) as record_alert, patch.object(collection_watchdog.time, 'sleep'), patch.object(
            collection_watchdog, 'read_collection_batch', side_effect=batch_snapshots
        ), patch.object(
            collection_watchdog, 'WATCHDOG_MAX_RESTARTS', 3
        ):
            exit_code = collection_watchdog._supervise_collection_batch('a' * 32)

        self.assertEqual(exit_code, 1)
        self.assertEqual(start_process.call_count, 4)
        self.assertEqual(
            [call.args[2] for call in record_alert.call_args_list if call.args[0] == 'collection_exited'],
            [1, 1, 2, 3],
        )
        self.assertEqual(record_alert.call_args_list[-1].args[0], 'restart_limit_reached')

    def test_watchdog_requires_noninteractive_login_confirmation(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            stdin_report = Path(temporary_directory) / 'stdin.json'
            probe_command = [
                sys.executable, '-c',
                'import json,sys; from pathlib import Path; '
                'Path(sys.argv[1]).write_text(json.dumps({'
                '"interactive": sys.stdin.isatty(), '
                '"input": sys.stdin.readline()}), encoding="utf-8")',
                str(stdin_report),
            ]
            with patch.object(collection_watchdog, 'collection_command', return_value=probe_command):
                probe_process = collection_watchdog.start_collection_process('a' * 32)
            try:
                self.assertEqual(probe_process.wait(timeout=10), 0)
                self.assertEqual(
                    json.loads(stdin_report.read_text(encoding='utf-8')),
                    {'interactive': False, 'input': ''},
                )
            finally:
                if probe_process.poll() is None:
                    probe_process.kill()
                    probe_process.wait(timeout=10)

    def test_watchdog_prioritizes_required_login_over_heartbeat_timeout(self):
        with patch.object(collection_watchdog, 'read_alert_status', return_value={
            'status': 'alert', 'reason': 'login_required', 'details': 'login not confirmed',
        }), patch.object(collection_watchdog, 'read_collection_heartbeat') as read_heartbeat:
            failure = collection_watchdog.supervision_failure(_RunningProcess())
        self.assertEqual(failure, ('login_required', 'login not confirmed'))
        read_heartbeat.assert_not_called()

    def test_watchdog_does_not_restart_when_login_requires_operator(self):
        with patch.object(collection_watchdog, '_stop_requested', False), patch.object(
            collection_watchdog, 'write_collection_start_heartbeat'
        ), patch.object(
            collection_watchdog, 'start_collection_process', return_value=_FailedProcess()
        ) as collection_start, patch.object(
            collection_watchdog, 'supervision_failure',
            return_value=('login_required', 'login not confirmed'),
        ), patch.object(collection_watchdog, 'terminate_process_tree'), patch.object(
            collection_watchdog, 'record_collection_alert'
        ) as record_alert, patch.object(collection_watchdog.time, 'sleep') as retry_delay:
            exit_code = collection_watchdog._supervise_collection_batch('a' * 32)
        self.assertEqual(exit_code, 1)
        self.assertEqual(collection_start.call_count, 1)
        retry_delay.assert_not_called()
        record_alert.assert_called_once_with('login_required', 'login not confirmed', 0)

    def test_watchdog_stops_at_coordinate_failure_without_retry_delay(self):
        calibration_message = '浏览器窗口几何不匹配；请重新运行坐标校准'
        collection_process = _FailedProcess()
        collection_process.returncode = 2
        with (
            patch.object(collection_watchdog, '_stop_requested', False),
            patch.object(collection_watchdog, 'write_collection_start_heartbeat'),
            patch.object(collection_watchdog, 'start_collection_process', return_value=collection_process) as collection_start,
            patch.object(collection_watchdog, 'read_alert_status', return_value={
                'status': 'alert', 'reason': 'coordinate_calibration_required', 'details': calibration_message,
            }),
            patch.object(collection_watchdog, 'terminate_process_tree'),
            patch.object(collection_watchdog, 'record_collection_alert') as record_alert,
            patch.object(collection_watchdog.time, 'sleep') as retry_delay,
            patch.object(collection_watchdog, 'read_collection_batch') as read_batch,
        ):
            exit_code = collection_watchdog._supervise_collection_batch('a' * 32)

        self.assertEqual(exit_code, 1)
        collection_start.assert_called_once_with('a' * 32)
        retry_delay.assert_not_called()
        read_batch.assert_not_called()
        record_alert.assert_called_once_with('coordinate_calibration_required', calibration_message, 0)

    def test_exit_two_without_coordinate_alert_keeps_retry_policy(self):
        collection_process = _FailedProcess()
        collection_process.returncode = 2
        disk_usage = type('DiskUsage', (), {'free': 100 * 1024 ** 3})()
        with (
            patch.object(collection_watchdog, '_stop_requested', False),
            patch.object(collection_watchdog, 'write_collection_start_heartbeat'),
            patch.object(collection_watchdog, 'start_collection_process', return_value=collection_process) as collection_start,
            patch.object(collection_watchdog.shutil, 'disk_usage', return_value=disk_usage),
            patch.object(collection_watchdog, 'read_collection_heartbeat', return_value=None),
            patch.object(collection_watchdog, 'terminate_process_tree'),
            patch.object(collection_watchdog, 'record_collection_alert') as record_alert,
            patch.object(collection_watchdog.time, 'sleep'),
            patch.object(collection_watchdog, 'read_collection_batch', return_value={'remaining': 2, 'succeeded': 0}),
            patch.object(collection_watchdog, 'WATCHDOG_MAX_RESTARTS', 3),
        ):
            exit_code = collection_watchdog._supervise_collection_batch('a' * 32)

        self.assertEqual(exit_code, 1)
        self.assertEqual(collection_start.call_count, 3)
        self.assertEqual(record_alert.call_args_list[0].args, ('collection_exited', '采集进程退出码 2', 1))
        self.assertEqual(record_alert.call_args_list[-1].args[0], 'restart_limit_reached')

    def test_new_watchdog_run_clears_old_coordinate_alert(self):
        with (
            tempfile.TemporaryDirectory() as temporary_directory,
            patch.object(collection_health, 'ALERT_STATUS_FILE', Path(temporary_directory) / 'alert.json'),
            patch.object(collection_health, 'WATCHDOG_EVENTS_FILE', Path(temporary_directory) / 'events.jsonl'),
            patch.object(collection_watchdog, 'read_alert_status', collection_health.read_alert_status),
            patch.object(collection_watchdog.signal, 'signal'),
            patch.object(collection_watchdog, '_stop_requested', False),
            patch.object(collection_watchdog, 'reserve_supervised_collection', _reserved_operation),
            patch.object(collection_watchdog, 'reserve_detail_collection_desktop', _reserved_operation),
            patch.object(collection_watchdog, 'select_main_collection_targets', return_value=['A']),
            patch.object(collection_watchdog.CollectionBatch, 'prepare', return_value='a' * 32),
            patch.object(collection_watchdog, 'write_collection_start_heartbeat'),
            patch.object(collection_watchdog, 'start_collection_process', return_value=_SuccessfulProcess()) as collection_start,
            patch.object(collection_watchdog, 'read_collection_heartbeat', return_value=None),
            patch.object(collection_watchdog, 'read_collection_batch', return_value={'status': 'completed', 'remaining': 0}),
            patch.object(collection_watchdog.shutil, 'disk_usage', return_value=type('DiskUsage', (), {'free': 100 * 1024 ** 3})()),
        ):
            collection_health.record_collection_alert('coordinate_calibration_required', 'old coordinates', 0)

            exit_code = collection_watchdog.run_supervised_collection()

            self.assertEqual(collection_health.read_alert_status()['status'], 'ok')
        self.assertEqual(exit_code, 0)
        collection_start.assert_called_once_with('a' * 32)

    def test_watchdog_prepares_exact_targets_before_supervision(self):
        call_order = []

        def prepare_batch(collector, application_nos):
            call_order.append(('prepare', collector, application_nos))
            return 'b' * 32

        def supervise_batch(batch_id):
            call_order.append(('supervise', batch_id))
            return 0

        with patch.object(collection_watchdog, '_stop_requested', False), patch.object(
            collection_watchdog.signal, 'signal'
        ), patch.object(collection_watchdog, 'reserve_supervised_collection', _reserved_operation), patch.object(
            collection_watchdog, 'reserve_detail_collection_desktop', _reserved_operation
        ), patch.object(collection_watchdog, 'clear_collection_alert'), patch.object(
            collection_watchdog, 'select_main_collection_targets', return_value=['A', 'B']
        ), patch.object(collection_watchdog.CollectionBatch, 'prepare', side_effect=prepare_batch), patch.object(
            collection_watchdog, '_supervise_collection_batch', side_effect=supervise_batch
        ):
            exit_code = collection_watchdog.run_supervised_collection()

        self.assertEqual(exit_code, 0)
        self.assertEqual(call_order, [
            ('prepare', 'main', ['A', 'B']),
            ('supervise', 'b' * 32),
        ])

    def test_watchdog_succeeds_without_starting_process_when_no_targets(self):
        with patch.object(collection_watchdog, '_stop_requested', False), patch.object(
            collection_watchdog.signal, 'signal'
        ), patch.object(collection_watchdog, 'reserve_supervised_collection', _reserved_operation), patch.object(
            collection_watchdog, 'reserve_detail_collection_desktop', _reserved_operation
        ), patch.object(collection_watchdog, 'clear_collection_alert'), patch.object(
            collection_watchdog, 'select_main_collection_targets', return_value=[]
        ), patch.object(collection_watchdog.CollectionBatch, 'prepare') as prepare_batch, patch.object(
            collection_watchdog, 'start_collection_process'
        ) as start_process, patch.object(
            collection_watchdog, 'write_collection_stopped_heartbeat'
        ) as stopped_heartbeat:
            exit_code = collection_watchdog.run_supervised_collection()

        self.assertEqual(exit_code, 0)
        prepare_batch.assert_not_called()
        start_process.assert_not_called()
        stopped_heartbeat.assert_called_once_with(0, 0)

    def test_prepare_failure_stops_before_supervision(self):
        with patch.object(collection_watchdog, '_stop_requested', False), patch.object(
            collection_watchdog.signal, 'signal'
        ), patch.object(collection_watchdog, 'reserve_supervised_collection', _reserved_operation), patch.object(
            collection_watchdog, 'reserve_detail_collection_desktop', _reserved_operation
        ), patch.object(collection_watchdog, 'clear_collection_alert'), patch.object(
            collection_watchdog, 'select_main_collection_targets', return_value=['A']
        ), patch.object(
            collection_watchdog.CollectionBatch, 'prepare', side_effect=OSError('disk full')
        ), patch.object(collection_watchdog, '_supervise_collection_batch') as supervise, patch.object(
            collection_watchdog, 'record_collection_alert'
        ) as record_alert:
            exit_code = collection_watchdog.run_supervised_collection()

        self.assertEqual(exit_code, 1)
        supervise.assert_not_called()
        record_alert.assert_called_once_with('collection_start_failed', 'disk full', 0)

    def test_zero_exit_with_remaining_targets_restarts_same_batch(self):
        snapshots = [
            {'status': 'paused', 'remaining': 1, 'succeeded': 1},
            {'status': 'paused', 'remaining': 1, 'succeeded': 1},
            {'status': 'completed', 'remaining': 0, 'succeeded': 2},
        ]
        with patch.object(collection_watchdog, '_stop_requested', False), patch.object(
            collection_watchdog, 'write_collection_start_heartbeat'
        ), patch.object(
            collection_watchdog, 'start_collection_process', side_effect=[
                _SuccessfulProcess(), _SuccessfulProcess(),
            ]
        ) as start_process, patch.object(
            collection_watchdog, 'supervision_failure', return_value=None
        ), patch.object(collection_watchdog, 'terminate_process_tree'), patch.object(
            collection_watchdog, 'read_collection_batch', side_effect=snapshots
        ), patch.object(collection_watchdog, 'record_collection_alert'), patch.object(
            collection_watchdog, 'clear_collection_alert'
        ) as clear_alert, patch.object(collection_watchdog.time, 'sleep'):
            exit_code = collection_watchdog._supervise_collection_batch('c' * 32)

        self.assertEqual(exit_code, 0)
        self.assertEqual([call.args for call in start_process.call_args_list], [('c' * 32,)] * 2)
        clear_alert.assert_called_once_with()

    def test_windows_termination_waits_for_batch_lease_release(self):
        process = MagicMock(pid=12345)
        process.poll.return_value = None
        process.wait.side_effect = [
            subprocess.TimeoutExpired('collector', 8),
            0,
        ]
        with patch.object(collection_watchdog.sys, 'platform', 'win32'), patch.object(
            collection_watchdog.subprocess, 'run'
        ) as taskkill:
            collection_watchdog.terminate_process_tree(process)

        taskkill.assert_called_once_with(
            ['taskkill', '/PID', '12345', '/T', '/F'],
            check=False,
            capture_output=True,
        )
        process.kill.assert_called_once_with()
        self.assertEqual(process.wait.call_count, 2)

    def test_supervisor_reads_batch_after_process_tree_termination(self):
        call_order = []

        def terminate(_process):
            call_order.append('terminate')

        def read_batch(_batch_id):
            call_order.append('read')
            return {'remaining': 0}

        with patch.object(collection_watchdog, '_stop_requested', False), patch.object(
            collection_watchdog, 'write_collection_start_heartbeat'
        ), patch.object(
            collection_watchdog, 'start_collection_process', return_value=_RunningProcess()
        ), patch.object(
            collection_watchdog, 'supervision_failure', return_value=('heartbeat_timeout', 'stale')
        ), patch.object(
            collection_watchdog, 'terminate_process_tree', side_effect=terminate
        ), patch.object(
            collection_watchdog, 'read_collection_batch', side_effect=read_batch
        ), patch.object(collection_watchdog, 'record_collection_alert'):
            exit_code = collection_watchdog._supervise_collection_batch('d' * 32)

        self.assertEqual(exit_code, 1)
        self.assertEqual(call_order, ['terminate', 'read'])

    @unittest.skipIf(sys.platform == 'win32', 'POSIX process-group behavior')
    def test_terminate_process_tree_stops_process_group(self):
        process = subprocess.Popen(
            [
                sys.executable,
                '-c',
                'import subprocess,sys,time; '
                'subprocess.Popen([sys.executable,"-c","import time; time.sleep(60)"]); '
                'time.sleep(60)',
            ],
            start_new_session=True,
        )
        try:
            collection_watchdog.terminate_process_tree(process)
            self.assertIsNotNone(process.poll())
            with self.assertRaises(ProcessLookupError):
                os.killpg(process.pid, 0)
        finally:
            if process.poll() is None:
                process.kill()
