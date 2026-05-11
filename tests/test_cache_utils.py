#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
单元测试：cache_utils.py

测试申请号规范化、验证等函数
"""

import unittest
from cache_utils import normalize_app_no


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
        # normalize_app_no 不移除空格，所以返回 '   '
        self.assertEqual(result, '   ')

    def test_none_input(self):
        """None 输入返回 None"""
        result = normalize_app_no(None)
        self.assertIsNone(result)


if __name__ == '__main__':
    unittest.main()
