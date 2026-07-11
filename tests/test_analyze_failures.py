#!/usr/bin/env python3

import unittest

from analyze_failures import failure_distribution, retry_class


class TestAnalyzeFailures(unittest.TestCase):
    def test_distribution_only_counts_status_zero(self):
        distribution = failure_distribution([
            {'status_code': 0, 'timestamp': '2026-07-01T01:00:00Z', 'error_message': 'MITM timeout'},
            {'status_code': 200, 'timestamp': '2026-07-01T02:00:00Z'},
            {'status_code': -1, 'timestamp': '2026-07-01T03:00:00Z'},
        ])
        self.assertEqual(distribution['total'], 1)
        self.assertEqual(distribution['by_date']['2026-07-01'], 1)
        self.assertEqual(distribution['by_retry_class']['建议重采'], 1)

    def test_permanent_reason_classification(self):
        self.assertEqual(retry_class('申请尚未公开'), '建议永久失败')
