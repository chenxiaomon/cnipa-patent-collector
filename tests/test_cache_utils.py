#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
单元测试：cache_utils.py

测试申请号规范化、验证等函数
"""

import multiprocessing
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, call, patch

from cache_utils import (
    is_supported_cn_application_no,
    normalize_app_no,
    parse_app_no_list,
    poll_cache_with_retry,
    read_json_cache,
    reserve_json_cache_updates,
    write_json_cache,
)


def _merge_cache_entry_in_process(
    cache_file,
    cache_key,
    attempting_update,
    entered_update,
    release_update,
):
    attempting_update.set()
    with reserve_json_cache_updates(cache_file):
        cache_entries = read_json_cache(cache_file)
        entered_update.set()
        if not release_update.wait(10):
            raise TimeoutError('test did not release cache update')
        cache_entries[cache_key] = {'source': cache_key}
        write_json_cache(cache_file, cache_entries)


def _reserve_cache_twice_in_process(cache_file, reservation_finished):
    with reserve_json_cache_updates(cache_file):
        with reserve_json_cache_updates(cache_file):
            write_json_cache(cache_file, {'nested': True})
    reservation_finished.set()


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


class TestJsonCacheUpdates(unittest.TestCase):
    def test_non_object_json_is_treated_as_an_invalid_cache(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            cache_file = Path(temporary_directory) / 'cache.json'
            cache_file.write_text('["unexpected"]', encoding='utf-8')

            self.assertEqual(read_json_cache(str(cache_file)), {})

    def test_read_modify_write_is_serialized_across_processes(self):
        process_context = multiprocessing.get_context('spawn')
        with tempfile.TemporaryDirectory() as temporary_directory:
            cache_file = Path(temporary_directory) / 'cache.json'
            write_json_cache(str(cache_file), {})
            first_attempting = process_context.Event()
            first_entered = process_context.Event()
            first_release = process_context.Event()
            second_attempting = process_context.Event()
            second_entered = process_context.Event()
            second_release = process_context.Event()
            first_writer = process_context.Process(
                target=_merge_cache_entry_in_process,
                args=(
                    str(cache_file),
                    'first',
                    first_attempting,
                    first_entered,
                    first_release,
                ),
            )
            second_writer = process_context.Process(
                target=_merge_cache_entry_in_process,
                args=(
                    str(cache_file),
                    'second',
                    second_attempting,
                    second_entered,
                    second_release,
                ),
            )
            try:
                first_writer.start()
                self.assertTrue(first_attempting.wait(5))
                self.assertTrue(first_entered.wait(5))
                second_writer.start()
                self.assertTrue(second_attempting.wait(5))
                self.assertFalse(second_entered.wait(0.2))

                first_release.set()
                self.assertTrue(second_entered.wait(5))
                second_release.set()
                first_writer.join(5)
                second_writer.join(5)
            finally:
                first_release.set()
                second_release.set()
                for writer in (first_writer, second_writer):
                    if writer.is_alive():
                        writer.terminate()
                    writer.join(5)

            self.assertEqual(first_writer.exitcode, 0)
            self.assertEqual(second_writer.exitcode, 0)
            self.assertEqual(
                read_json_cache(str(cache_file)),
                {
                    'first': {'source': 'first'},
                    'second': {'source': 'second'},
                },
            )

    def test_same_thread_can_reenter_cross_process_reservation(self):
        process_context = multiprocessing.get_context('spawn')
        with tempfile.TemporaryDirectory() as temporary_directory:
            cache_file = Path(temporary_directory) / 'cache.json'
            reservation_finished = process_context.Event()
            worker = process_context.Process(
                target=_reserve_cache_twice_in_process,
                args=(str(cache_file), reservation_finished),
            )
            try:
                worker.start()
                self.assertTrue(reservation_finished.wait(5))
                worker.join(5)
            finally:
                if worker.is_alive():
                    worker.terminate()
                worker.join(5)

            self.assertEqual(worker.exitcode, 0)
            self.assertEqual(read_json_cache(str(cache_file)), {'nested': True})


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
