#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""单元测试：/api/summary 的 TTL 缓存（summary_snapshot）"""

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import settings

# web_dashboard 在模块级实例化 PatentsDB(PATENTS_DB_FILE)，
# 必须在 import 前把路径指到临时目录，避免测试触碰仓库真实 DB
_TMP_DIR = tempfile.mkdtemp(prefix='cnipa_dashboard_test_')
settings.PATENTS_DB_FILE = Path(_TMP_DIR) / 'patents.db'

import web_dashboard  # noqa: E402


def tearDownModule():
    shutil.rmtree(_TMP_DIR, ignore_errors=True)


class TestSummarySnapshot(unittest.TestCase):
    """TTL 读穿缓存 + 显式失效"""

    def setUp(self):
        web_dashboard.discard_summary_snapshot()
        self.build_calls = 0

        def counting_build_summary(job_manager):
            self.build_calls += 1
            return {'build_calls': self.build_calls}

        patcher = mock.patch.object(web_dashboard, 'build_summary', counting_build_summary)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(web_dashboard.discard_summary_snapshot)

    def test_second_call_within_ttl_hits_cache(self):
        first = web_dashboard.summary_snapshot(None)
        second = web_dashboard.summary_snapshot(None)
        self.assertEqual(self.build_calls, 1)
        self.assertIs(first, second)  # 路由层靠 dict() 浅拷贝隔离 per-request 字段

    def test_expired_ttl_rebuilds(self):
        web_dashboard.summary_snapshot(None)
        web_dashboard._summary_snapshot_at -= web_dashboard.SUMMARY_SNAPSHOT_TTL_SECONDS + 1
        web_dashboard.summary_snapshot(None)
        self.assertEqual(self.build_calls, 2)

    def test_discard_forces_rebuild(self):
        web_dashboard.summary_snapshot(None)
        web_dashboard.discard_summary_snapshot()
        web_dashboard.summary_snapshot(None)
        self.assertEqual(self.build_calls, 2)


class TestManualFwxxBatchJob(unittest.TestCase):
    def test_builds_forced_batch_job_from_unique_request_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            request_dir = Path(tmpdir) / "manual_fwxx_lists"
            with mock.patch.object(web_dashboard, "FWXX_MANUAL_LIST_DIR", request_dir):
                spec = web_dashboard.build_job_spec(
                    "collect_fwxx_batch",
                    {"app_nos": "CN202411006597.0\nCN202111504942.X"},
                )

            self.assertEqual(spec["action"], "collect_fwxx_batch")
            self.assertIn("--force", spec["command"])
            input_index = spec["command"].index("--input") + 1
            request_path = Path(spec["command"][input_index])
            self.assertEqual(
                request_path.read_text(encoding="utf-8"),
                "2024110065970\n202111504942X\n",
            )
            self.assertIn("2 件", spec["title"])

    def test_rejects_invalid_batch_before_starting_process(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with mock.patch.object(
                web_dashboard,
                "FWXX_MANUAL_LIST_DIR",
                Path(tmpdir) / "manual_fwxx_lists",
            ):
                with self.assertRaisesRegex(ValueError, "格式不正确"):
                    web_dashboard.build_job_spec(
                        "collect_fwxx_batch",
                        {"app_nos": "CN202411006597.0\ninvalid"},
                    )


class TestJobLoginConfirmation(unittest.TestCase):
    def test_confirmation_signal_is_not_treated_as_verified_login(self):
        job = web_dashboard.Job('a1234567', 'main_full', 'collection', ['python'])
        job.append('[WAITING_FOR_LOGIN] pending')
        job.append('收到登录完成信号')
        self.assertTrue(job.to_dict()['waiting_for_login'])
        job.append('[LOGIN_CONFIRMED] confirmed')
        self.assertFalse(job.to_dict()['waiting_for_login'])

    def test_new_login_attempt_is_not_hidden_by_previous_confirmation(self):
        job = web_dashboard.Job('a1234567', 'main_full', 'collection', ['python'])
        job.append('[WAITING_FOR_LOGIN] first attempt')
        job.append('[LOGIN_CONFIRMED] first attempt')
        job.append('[WAITING_FOR_LOGIN] retry')
        self.assertTrue(job.to_dict()['waiting_for_login'])
        job.append('[LOGIN_REQUIRED] timed out')
        self.assertFalse(job.to_dict()['waiting_for_login'])

if __name__ == '__main__':
    unittest.main()
