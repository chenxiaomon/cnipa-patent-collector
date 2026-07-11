#!/usr/bin/env python3

import unittest
from unittest.mock import patch

import poll_master_alerts


class TestPollMasterAlerts(unittest.TestCase):
    def test_new_alert_is_forwarded_once(self):
        alert = {
            'status': 'alert',
            'reason': 'heartbeat_timeout',
            'details': 'stale',
            'timestamp': '2026-07-11T00:00:00Z',
        }
        with patch.object(poll_master_alerts, 'fetch_master_alert', return_value=alert), patch.object(
            poll_master_alerts, 'load_last_forwarded_timestamp', return_value=None
        ), patch.object(poll_master_alerts, 'send_serverchan_alert') as send_alert, patch.object(
            poll_master_alerts, 'save_forwarded_timestamp'
        ) as save_timestamp:
            forwarded = poll_master_alerts.poll_once('http://master:8765')
        self.assertTrue(forwarded)
        send_alert.assert_called_once_with(alert)
        save_timestamp.assert_called_once_with('2026-07-11T00:00:00Z')

    def test_duplicate_alert_is_not_forwarded(self):
        alert = {
            'status': 'alert',
            'reason': 'disk_space_low',
            'timestamp': '2026-07-11T00:00:00Z',
        }
        with patch.object(poll_master_alerts, 'fetch_master_alert', return_value=alert), patch.object(
            poll_master_alerts, 'load_last_forwarded_timestamp',
            return_value='2026-07-11T00:00:00Z',
        ), patch.object(poll_master_alerts, 'send_serverchan_alert') as send_alert:
            forwarded = poll_master_alerts.poll_once('http://master:8765')
        self.assertFalse(forwarded)
        send_alert.assert_not_called()
