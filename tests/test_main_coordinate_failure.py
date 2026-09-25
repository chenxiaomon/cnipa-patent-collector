"""A rejected coordinate configuration leaves one resumable batch for the operator."""

import runpy
import sys
import unittest
from contextlib import ExitStack, nullcontext
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

import browser_service
import browser_utils
import collection_checkpoint
import collection_health
import coordinate_service
import desktop_collection_lock
import detection_logger
import main_collection_targets
import settings


class TestMainCoordinateFailure(unittest.TestCase):
    def setUp(self):
        patch_stack = ExitStack()
        self.addCleanup(patch_stack.close)
        temporary_directory = patch_stack.enter_context(TemporaryDirectory())
        temporary_root = Path(temporary_directory)
        self.checkpoint_file = temporary_root / 'resume.txt'
        collection_logger = MagicMock()
        collection_logger.log_file = str(temporary_root / 'records.jsonl')
        collection_logger.get_stats.return_value = {'total': 0}
        self.coordinate_loader = patch_stack.enter_context(patch.object(
            coordinate_service.CoordinateService, 'load_search_coordinates',
        ))
        self.browser_start = patch_stack.enter_context(patch.object(
            browser_service.BrowserService, 'launch_and_login',
        ))
        for startup_patch in (
            patch.object(sys, 'argv', ['main_automation.py']),
            patch.object(settings, 'MAIN_COLLECTION_CHECKPOINT_FILE', self.checkpoint_file),
            patch.object(collection_checkpoint, 'COLLECTION_BATCHES_DIR', temporary_root / 'batches'),
            patch.object(collection_health, 'ALERT_STATUS_FILE', temporary_root / 'alert.json'),
            patch.object(collection_health, 'WATCHDOG_EVENTS_FILE', temporary_root / 'events.jsonl'),
            patch.object(collection_health, 'COLLECTION_HEARTBEAT_FILE', temporary_root / 'heartbeat.json'),
            patch.object(detection_logger, 'DetectionLogger', return_value=collection_logger),
            patch.object(main_collection_targets, 'select_main_collection_targets', return_value=['A', 'B']),
            patch.object(desktop_collection_lock, 'reserve_detail_collection_desktop', return_value=nullcontext()),
            patch.object(browser_utils, 'raise_system_exit_on_sigterm'),
            patch.object(browser_service, 'stop_virtual_display'),
        ):
            patch_stack.enter_context(startup_patch)

    def test_cli_reports_calibration_reason_after_preserving_pending_targets(self):
        calibration_message = '浏览器窗口几何不匹配；请重新运行坐标校准'
        self.coordinate_loader.side_effect = coordinate_service.CoordinateConfigurationError(calibration_message)

        with self.assertRaises(SystemExit) as stopped:
            runpy.run_path(str(settings.BASE_DIR / 'main_automation.py'), run_name='__main__')

        self.assertEqual(stopped.exception.code, 2)
        self.browser_start.assert_not_called()
        self.assertEqual(self.checkpoint_file.read_text(encoding='utf-8'), 'A\nB\n')
        batches = collection_checkpoint.list_collection_batches()
        self.assertEqual(len(batches), 1)
        interrupted_batch = collection_checkpoint.read_collection_batch(batches[0]['id'])
        self.assertEqual((interrupted_batch['status'], interrupted_batch['remaining']), ('interrupted', 2))
        self.assertEqual([item['attempt_count'] for item in interrupted_batch['items']], [0, 0])
        self.assertEqual(interrupted_batch['runs'][0]['stop_reason'], calibration_message)
        calibration_alert = collection_health.read_alert_status()
        self.assertEqual(calibration_alert['reason'], 'coordinate_calibration_required')
        self.assertEqual(calibration_alert['details'], calibration_message)

    def test_other_value_error_does_not_report_coordinate_alert(self):
        self.coordinate_loader.side_effect = ValueError('unrelated startup failure')

        with self.assertRaises(SystemExit) as stopped:
            runpy.run_path(str(settings.BASE_DIR / 'main_automation.py'), run_name='__main__')

        self.assertEqual(stopped.exception.code, 2)
        self.assertEqual(collection_health.read_alert_status()['status'], 'unknown')
        self.browser_start.assert_not_called()


if __name__ == '__main__':
    unittest.main()
