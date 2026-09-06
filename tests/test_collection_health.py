#!/usr/bin/env python3

import tempfile
import os
import subprocess
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import collection_health
import collection_watchdog
from collection_watchdog import heartbeat_age_seconds


class _RunningProcess:
    returncode = None

    def poll(self):
        return None


class _FailedProcess:
    pid = 12345
    returncode = 1

    def poll(self):
        return self.returncode


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
            collection_watchdog.signal, 'signal'
        ), patch.object(collection_watchdog, 'clear_collection_alert'), patch.object(
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
            collection_watchdog, 'WATCHDOG_MAX_RESTARTS', 3
        ):
            exit_code = collection_watchdog.run_supervised_collection()
        self.assertEqual(exit_code, 1)
        self.assertEqual(start_process.call_count, 3)
        self.assertEqual(record_alert.call_args_list[-1].args[0], 'restart_limit_reached')

    def test_watchdog_requires_noninteractive_login_confirmation(self):
        with patch.object(collection_watchdog.subprocess, 'Popen') as collection_start:
            collection_watchdog.start_collection_process()
        self.assertEqual(collection_start.call_args.kwargs['stdin'], subprocess.DEVNULL)

    def test_watchdog_prioritizes_required_login_over_heartbeat_timeout(self):
        with patch.object(collection_watchdog, 'read_alert_status', return_value={
            'status': 'alert', 'reason': 'login_required', 'details': 'login not confirmed',
        }), patch.object(collection_watchdog, 'read_collection_heartbeat') as read_heartbeat:
            failure = collection_watchdog.supervision_failure(_RunningProcess())
        self.assertEqual(failure, ('login_required', 'login not confirmed'))
        read_heartbeat.assert_not_called()

    def test_watchdog_does_not_restart_when_login_requires_operator(self):
        with patch.object(collection_watchdog, '_stop_requested', False), patch.object(
            collection_watchdog.signal, 'signal'
        ), patch.object(collection_watchdog, 'clear_collection_alert'), patch.object(
            collection_watchdog, 'write_collection_start_heartbeat'
        ), patch.object(
            collection_watchdog, 'start_collection_process', return_value=_FailedProcess()
        ) as collection_start, patch.object(
            collection_watchdog, 'supervision_failure',
            return_value=('login_required', 'login not confirmed'),
        ), patch.object(collection_watchdog, 'terminate_process_tree'), patch.object(
            collection_watchdog, 'record_collection_alert'
        ) as record_alert, patch.object(collection_watchdog.time, 'sleep') as retry_delay:
            exit_code = collection_watchdog.run_supervised_collection()
        self.assertEqual(exit_code, 1)
        self.assertEqual(collection_start.call_count, 1)
        retry_delay.assert_not_called()
        record_alert.assert_called_once_with('login_required', 'login not confirmed', 0)

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
