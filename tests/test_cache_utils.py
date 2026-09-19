#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
单元测试：cache_utils.py

测试申请号规范化、验证等函数
"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, call, patch

from cache_utils import (
    is_supported_cn_application_no,
    normalize_app_no,
    parse_app_no_list,
    poll_cache_for_key,
    poll_cache_with_retry,
    read_json_cache,
    write_json_cache,
)


class TestNormalizeAppNo(unittest.TestCase):
    """申请号规范化测试

    实现说明：移除 CN 前缀和点号，返回纯数字+字母（如 X）
    示例：CN202310869634.X → 202310869634X
    """

    def test_valid_app_no(self):
        """正常申请号（移除 CN）"""
        result = normalize_app_no('CN201880002233')
        self.assertEqual(result, '201880002233')

    def test_uppercase_conversion(self):
        """小写转大写"""
        result = normalize_app_no('cn201880002233')
        self.assertEqual(result, '201880002233')

    def test_with_dot_and_x(self):
        """去除点号（如 X 校验字符）"""
        result = normalize_app_no('CN202310869634.X')
        self.assertEqual(result, '202310869634X')

    def test_empty_string(self):
        """空字符串返回 None"""
        result = normalize_app_no('')
        self.assertIsNone(result)

    def test_various_formats(self):
        """各种格式的申请号"""
        test_cases = [
            ('CN201880002233', '201880002233'),
            ('cn201880002233', '201880002233'),
            ('CN202310869634.X', '202310869634X'),
            ('CN202211273995X', '202211273995X'),
        ]
        for input_val, expected in test_cases:
            with self.subTest(input=input_val):
                result = normalize_app_no(input_val)
                self.assertEqual(result, expected)


class TestApplicationNoValidation(unittest.TestCase):
    """申请号验证测试"""

    def test_valid_cn_format(self):
        """有效的中国申请号格式（移除 CN）"""
        test_cases = [
            ('CN201880002233', '201880002233'),
            ('CN202380004567', '202380004567'),
            ('CN202211273995X', '202211273995X'),
            ('CN201980000001', '201980000001'),
        ]
        for app, expected in test_cases:
            with self.subTest(app=app):
                result = normalize_app_no(app)
                self.assertEqual(result, expected)

    def test_supported_cn_application_numbers_are_accepted(self):
        for app_no in (
            '100010220',
            '2023108921437',
            '202311437336X',
            'CN202411006597.0',
        ):
            with self.subTest(app_no=app_no):
                self.assertTrue(is_supported_cn_application_no(app_no))

    def test_pct_and_malformed_application_numbers_are_rejected(self):
        for app_no in (
            'PCT/2025/134239',
            '2023CN108921437',
            '123.45.67.8',
        ):
            with self.subTest(app_no=app_no):
                self.assertFalse(is_supported_cn_application_no(app_no))

    def test_special_characters(self):
        """包含特殊字符的申请号"""
        # X 是有效的校验字符，.也会被移除
        result = normalize_app_no('CN202211273995.X')
        self.assertEqual(result, '202211273995X')

    def test_year_range(self):
        """各个年份的申请号"""
        test_cases = [
            ('CN201880002233', '201880002233'),  # 2018 年
            ('CN202380004567', '202380004567'),  # 2023 年
            ('CN202480001111', '202480001111'),  # 2024 年
            ('CN202580002222', '202580002222'),  # 2025 年
        ]
        for app, expected in test_cases:
            with self.subTest(app=app):
                result = normalize_app_no(app)
                self.assertEqual(result, expected)


class TestEdgeCases(unittest.TestCase):
    """边界情况测试"""

    def test_max_length_exceeded(self):
        """超过最大长度（仍然返回结果）"""
        # normalize_app_no 不做长度验证，直接返回
        result = normalize_app_no('CN20188000223300')
        self.assertEqual(result, '20188000223300')

    def test_min_length_not_met(self):
        """未达到最小长度（仍然返回结果）"""
        result = normalize_app_no('CN2018')
        self.assertEqual(result, '2018')

    def test_only_spaces(self):
        """只有空格（保留空格）"""
        result = normalize_app_no('   ')
        self.assertIsNone(result)

    def test_strips_surrounding_spaces(self):
        result = normalize_app_no('  CN202411006597.0  ')
        self.assertEqual(result, '2024110065970')

    def test_none_input(self):
        """None 输入返回 None"""
        result = normalize_app_no(None)
        self.assertIsNone(result)


class TestParseAppNoList(unittest.TestCase):
    def test_normalizes_pasted_list(self):
        text = """申请号
CN202411006597.0
CN202110795062.6
CN202111504942.X
"""
        self.assertEqual(
            parse_app_no_list(text),
            ['2024110065970', '2021107950626', '202111504942X'],
        )

    def test_deduplicates_and_accepts_common_separators(self):
        text = 'CN202411006597.0, 2024110065970；cn202111504942.x'
        self.assertEqual(parse_app_no_list(text), ['2024110065970', '202111504942X'])

    def test_filters_pct_from_mixed_application_numbers(self):
        text = (
            'CN202411006597.0\n'
            'PCT/2025/134239\n'
            '100010220, 202311437336X'
        )
        self.assertEqual(
            parse_app_no_list(text),
            ['2024110065970', '100010220', '202311437336X'],
        )


class TestCacheSnapshots(unittest.TestCase):
    def test_missing_and_malformed_cache_remain_empty(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_file = Path(tmpdir) / 'cache.json'
            self.assertEqual(read_json_cache(cache_file), {})
            cache_file.write_text('{', encoding='utf-8')
            self.assertEqual(read_json_cache(cache_file), {})

    @unittest.skipUnless(os.name == 'nt', 'Windows read handles prevent snapshot replacement')
    def test_publisher_can_replace_snapshot_while_reader_decodes_json(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_file = Path(tmpdir) / 'cache.json'
            cache_file.write_text('{"response": "first"}', encoding='utf-8')
            replacement = Path(tmpdir) / 'replacement.json'
            replacement.write_text('{"response": "second"}', encoding='utf-8')
            decode_json = json.loads

            def publish_during_decode(snapshot, **kwargs):
                os.replace(replacement, cache_file)
                return decode_json(snapshot, **kwargs)

            with patch('cache_utils.json.loads', side_effect=publish_during_decode):
                self.assertEqual(read_json_cache(cache_file), {'response': 'first'})
            self.assertEqual(read_json_cache(cache_file), {'response': 'second'})

    def test_reader_recovers_from_transient_windows_access_error(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_file = Path(tmpdir) / 'cache.json'
            cache_file.write_text('{"response": "received"}', encoding='utf-8')
            sharing_error = PermissionError('snapshot is being replaced')
            sharing_error.winerror = 5
            with cache_file.open(encoding='utf-8') as stream:
                with patch('builtins.open', side_effect=[sharing_error, stream]):
                    self.assertEqual(read_json_cache(cache_file), {'response': 'received'})

    def test_persistent_reader_access_error_is_reported(self):
        sharing_error = PermissionError('cache remains inaccessible')
        sharing_error.winerror = 32
        with (
            patch('builtins.open', side_effect=sharing_error),
            patch('time.monotonic', side_effect=[0.0, 2.0]),
        ):
            with self.assertRaises(PermissionError):
                read_json_cache('cache.json')

    @unittest.skipUnless(os.name == 'nt', 'Windows CRT omits winerror on a sharing violation')
    def test_reader_recovers_from_windows_crt_access_error(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_file = Path(tmpdir) / 'cache.json'
            cache_file.write_text('{"response": "received"}', encoding='utf-8')
            with cache_file.open(encoding='utf-8') as stream:
                with patch('builtins.open', side_effect=[PermissionError(13, 'Permission denied'), stream]):
                    self.assertEqual(read_json_cache(cache_file), {'response': 'received'})

    def test_polling_unchanged_cache_does_not_repeat_full_reads(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_file = Path(tmpdir) / 'cache.json'
            write_json_cache(cache_file, {'other': {'title': 'existing'}})
            sleeps = []

            def publish_on_fourth_poll(interval):
                sleeps.append(interval)
                if len(sleeps) == 4:
                    write_json_cache(cache_file, {'target': {'title': 'received'}})

            with (
                patch('cache_utils.read_json_cache', wraps=read_json_cache) as read_cache,
                patch('cache_utils.time.sleep', side_effect=publish_on_fourth_poll),
            ):
                captured = poll_cache_for_key(cache_file, 'target', max_wait=2)
            self.assertEqual(captured, {'title': 'received'})
            self.assertEqual(read_cache.call_count, 2)

    def test_polling_finds_cache_created_after_wait_begins(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_file = Path(tmpdir) / 'cache.json'
            with patch('cache_utils.time.sleep', side_effect=lambda interval: write_json_cache(cache_file, {'target': 42})):
                self.assertEqual(poll_cache_for_key(cache_file, 'target', max_wait=2), 42)

    def test_polling_revalidates_unchanged_snapshot(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_file = Path(tmpdir) / 'cache.json'
            write_json_cache(cache_file, {'target': 42})
            validate = Mock(side_effect=[False, True])
            with patch('cache_utils.time.sleep'):
                self.assertEqual(poll_cache_for_key(cache_file, 'target', max_wait=2, validate=validate), 42)


    def test_polling_detects_same_size_replacement_with_preserved_timestamp(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_file = Path(tmpdir) / 'cache.json'
            cache_file.write_text('{"target": 41}', encoding='utf-8')
            original_stat = cache_file.stat()

            def publish_replacement(interval):
                replacement = Path(tmpdir) / 'replacement.json'
                replacement.write_text('{"target": 42}', encoding='utf-8')
                os.utime(replacement, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
                os.replace(replacement, cache_file)

            with patch('cache_utils.time.sleep', side_effect=publish_replacement):
                captured = poll_cache_for_key(cache_file, 'target', max_wait=2, validate=lambda value: value == 42)
            self.assertEqual(captured, 42)

    def test_unchanged_cache_without_target_still_times_out(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_file = Path(tmpdir) / 'cache.json'
            write_json_cache(cache_file, {'other': 42})
            self.assertIsNone(poll_cache_for_key(cache_file, 'target', max_wait=0.03, interval=0.005))


class TestPollCacheWithRetry(unittest.TestCase):
    def test_calls_on_retry_before_next_attempt(self):
        on_retry = Mock()
        with patch('cache_utils.poll_cache_for_key', side_effect=[None, {'ok': True}]):
            result, attempts = poll_cache_with_retry(
                'cache.json',
                '2024110065970',
                base_wait=1,
                interval=0.1,
                max_attempts=3,
                on_retry=on_retry,
            )

        self.assertEqual(result, {'ok': True})
        self.assertEqual(attempts, 2)
        on_retry.assert_called_once_with(1)

    def test_calls_on_retry_for_each_retry_window(self):
        on_retry = Mock()
        with patch('cache_utils.poll_cache_for_key', return_value=None):
            result, attempts = poll_cache_with_retry(
                'cache.json',
                '2024110065970',
                base_wait=1,
                interval=0.1,
                max_attempts=3,
                on_retry=on_retry,
            )

        self.assertIsNone(result)
        self.assertEqual(attempts, 3)
        on_retry.assert_has_calls([call(1), call(2)])


if __name__ == '__main__':
    unittest.main()
