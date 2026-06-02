#!/usr/bin/env python
# -*- coding: utf-8 -*-

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from retry_failed import is_failed_record, retry_app_nos, write_failed_retry_list


class TestFailedRetryRecords(unittest.TestCase):
    def test_status_zero_is_failed(self):
        self.assertTrue(is_failed_record({'status_code': 0, 'zhuanlimc': None}))

    def test_success_with_title_is_not_failed(self):
        self.assertFalse(is_failed_record({'status_code': 200, 'zhuanlimc': '一种装置'}))

    def test_success_without_title_is_failed(self):
        self.assertTrue(is_failed_record({'status_code': 200, 'zhuanlimc': ''}))

    def test_retry_app_nos_normalizes_and_deduplicates(self):
        failure_records = [
            {'application_no': 'CN202411006597.0'},
            {'application_no': '2024110065970'},
            {'application_no': 'CN202111504942.X'},
        ]
        self.assertEqual(retry_app_nos(failure_records), ['2024110065970', '202111504942X'])

    def test_write_failed_retry_list(self):
        with TemporaryDirectory() as temp_dir:
            retry_path = Path(temp_dir) / 'retry_failed.txt'
            with patch('retry_failed.RETRY_FAILED_FILE', retry_path):
                write_failed_retry_list(['2024110065970', '202111504942X'])
            self.assertEqual(
                retry_path.read_text(encoding='utf-8'),
                '2024110065970\n202111504942X\n',
            )


if __name__ == '__main__':
    unittest.main()
