#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""连续失败熔断（CollectionFailureStreak）与 .env 载入（settings）的行为契约。"""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import collect_fees
import collection_health
import settings
from collection_health import CollectionFailureStreak, CollectionFailureStreakExceeded


class TestCollectionFailureStreak(unittest.TestCase):
    @patch.object(collection_health, 'WATCHDOG_FAILURE_THRESHOLD', 3)
    @patch.object(collection_health, 'record_collection_alert')
    def test_reaching_threshold_records_alert_and_raises(self, record_alert):
        streak = CollectionFailureStreak('费用信息采集')
        streak.record_failure()
        streak.record_failure()
        with self.assertRaises(CollectionFailureStreakExceeded) as raised:
            streak.record_failure()
        self.assertIn('费用信息采集', str(raised.exception))
        self.assertIn('连续失败 3 条', str(raised.exception))
        record_alert.assert_called_once()
        self.assertEqual(record_alert.call_args.args[0], 'consecutive_failures')

    @patch.object(collection_health, 'WATCHDOG_FAILURE_THRESHOLD', 3)
    @patch.object(collection_health, 'record_collection_alert')
    def test_success_resets_the_streak(self, record_alert):
        streak = CollectionFailureStreak('主采集')
        streak.record_failure()
        streak.record_failure()
        streak.record_success()
        self.assertEqual(streak.count, 0)
        streak.record_failure()
        streak.record_failure()
        record_alert.assert_not_called()

    def test_count_tracks_failures_for_heartbeat(self):
        streak = CollectionFailureStreak('主采集')
        streak.record_failure()
        streak.record_failure()
        self.assertEqual(streak.count, 2)


class TestFeeCollectionStopsOnStreak(unittest.TestCase):
    @patch.object(collect_fees, 'USE_MITM_PROXY', True)
    @patch(
        'collect_fees.run_fee_collection',
        side_effect=CollectionFailureStreakExceeded('费用信息采集 连续失败 20 条，已停止。'),
    )
    def test_cli_exits_with_code_3(self, _run_collection):
        exit_code = collect_fees.main([])
        self.assertEqual(exit_code, 3)


class TestEnvFileLoading(unittest.TestCase):
    def test_env_values_enter_environment(self):
        with TemporaryDirectory() as tmp_dir:
            env_file = Path(tmp_dir, '.env')
            env_file.write_text(
                '# 注释行\n'
                'PROBE_ENV_KEY=from-file\n'
                'PROBE_EMPTY_KEY=\n'
                '无等号的行\n',
                encoding='utf-8',
            )
            with patch.dict('os.environ', {}, clear=False):
                import os
                os.environ.pop('PROBE_ENV_KEY', None)
                os.environ.pop('PROBE_EMPTY_KEY', None)
                settings._load_env_file_into_environment(env_file)
                self.assertEqual(os.environ.get('PROBE_ENV_KEY'), 'from-file')
                # 'KEY=' 占位行不得把变量污染成空字符串
                self.assertNotIn('PROBE_EMPTY_KEY', os.environ)

    def test_explicit_process_environment_wins(self):
        with TemporaryDirectory() as tmp_dir:
            env_file = Path(tmp_dir, '.env')
            env_file.write_text('PROBE_ENV_KEY=from-file\n', encoding='utf-8')
            with patch.dict('os.environ', {'PROBE_ENV_KEY': 'from-process'}, clear=False):
                import os
                settings._load_env_file_into_environment(env_file)
                self.assertEqual(os.environ['PROBE_ENV_KEY'], 'from-process')

    def test_missing_env_file_is_noop(self):
        settings._load_env_file_into_environment(Path('/nonexistent/.env'))


if __name__ == '__main__':
    unittest.main()
