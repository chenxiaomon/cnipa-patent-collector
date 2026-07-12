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


if __name__ == '__main__':
    unittest.main()
