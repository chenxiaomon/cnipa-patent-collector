#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
单元测试：数据验证和质量检查

测试采集数据的一致性、有效性等
"""

import unittest
import json
from pathlib import Path


class TestDetectionLogStructure(unittest.TestCase):
    """检查 detection_log.json 的结构"""

    @classmethod
    def setUpClass(cls):
        """加载日志文件"""
        log_file = Path('data/results/detection_log.json')
        if not log_file.exists():
            raise FileNotFoundError(f"找不到日志文件: {log_file}")

        with open(log_file, 'r', encoding='utf-8') as f:
            cls.log_data = json.load(f)

    def test_log_has_metadata(self):
        """日志包含 metadata"""
        self.assertIn('metadata', self.log_data)
        self.assertIsInstance(self.log_data['metadata'], dict)

    def test_log_has_records(self):
        """日志包含 records"""
        self.assertIn('records', self.log_data)
        self.assertIsInstance(self.log_data['records'], list)
        self.assertGreater(len(self.log_data['records']), 0)

    def test_metadata_structure(self):
        """metadata 结构完整"""
        metadata = self.log_data['metadata']
        required_fields = [
            'total_records',
            'successful',
            'failed',
            'success_rate_percent'
        ]
        for field in required_fields:
            self.assertIn(field, metadata, f"metadata 缺少字段: {field}")

    def test_record_structure(self):
        """记录结构完整"""
        record = self.log_data['records'][0]
        self.assertIn('application_no', record)
        self.assertIn('status_code', record)
        self.assertIn('timestamp', record)

    def test_success_rate_calculation(self):
        """成功率计算正确"""
        metadata = self.log_data['metadata']
        records = self.log_data['records']

        total = metadata['total_records']
        successful = metadata['successful']

        # 验证总数
        self.assertEqual(total, len(records))

        # 验证成功数
        actual_successful = sum(1 for r in records if r.get('status_code') == 200)
        self.assertEqual(successful, actual_successful)

    def test_no_duplicate_application_nos(self):
        """申请号无重复"""
        records = self.log_data['records']
        app_nos = [r['application_no'] for r in records]
        unique_app_nos = set(app_nos)

        self.assertEqual(len(app_nos), len(unique_app_nos),
                        f"发现 {len(app_nos) - len(unique_app_nos)} 个重复申请号")

    def test_valid_status_codes(self):
        """状态码有效"""
        records = self.log_data['records']
        valid_codes = {0, 200}  # 0=失败, 200=成功

        for record in records:
            status = record.get('status_code')
            self.assertIn(status, valid_codes,
                         f"{record['application_no']}: 无效的状态码 {status}")

    def test_anjianywzt_distribution(self):
        """anjianywzt 有真实分布"""
        records = self.log_data['records']
        anjianywzt_values = {}

        for record in records:
            value = record.get('anjianywzt', 'N/A')
            anjianywzt_values[value] = anjianywzt_values.get(value, 0) + 1

        # 应该有多个不同的值
        self.assertGreater(len(anjianywzt_values), 1,
                          "anjianywzt 分布过单一")

        # 应该有"驳回等复审请求"
        self.assertIn('驳回等复审请求', anjianywzt_values,
                     "缺少'驳回等复审请求'分类")

    def test_falvzt_unusable(self):
        """falvzt 全为 '--'（不可用）"""
        records = self.log_data['records']
        successful_records = [r for r in records if r.get('status_code') == 200]

        falvzt_values = set(r.get('falvzt') for r in successful_records)

        # 成功采集的记录中，falvzt 应该全为 '--' 或 None
        for value in falvzt_values:
            self.assertIn(value, ['--', None],
                         f"falvzt 包含非空值: {value}")

    def test_fwxx_coverage(self):
        """发文覆盖率统计"""
        records = self.log_data['records']

        huihe_records = [r for r in records
                        if r.get('anjianywzt') == '驳回等复审请求']
        huihe_with_fwxx = [r for r in huihe_records
                          if r.get('fwxx_list')]

        coverage = len(huihe_with_fwxx) / len(huihe_records) * 100

        # 覆盖率应该 > 90%
        self.assertGreater(coverage, 90,
                          f"发文覆盖率过低: {coverage:.2f}%")


class TestRecordCompletion(unittest.TestCase):
    """检查记录的完整性"""

    @classmethod
    def setUpClass(cls):
        """加载日志文件"""
        log_file = Path('data/results/detection_log.json')
        with open(log_file, 'r', encoding='utf-8') as f:
            cls.log_data = json.load(f)

    def test_successful_records_have_patent_fields(self):
        """成功的记录应该有专利字段"""
        records = self.log_data['records']
        successful = [r for r in records if r.get('status_code') == 200]

        for record in successful[:10]:  # 抽样检查前 10 条
            self.assertIsNotNone(record.get('zhuanlimc'),
                               f"{record['application_no']}: 缺少专利名称")
            self.assertIsNotNone(record.get('shenqingrxm'),
                               f"{record['application_no']}: 缺少申请人")

    def test_failed_records_marked(self):
        """失败的记录应该标记为 status_code=0"""
        records = self.log_data['records']
        failed = [r for r in records if r.get('status_code') == 0]

        self.assertGreater(len(failed), 0, "应该有失败的记录")

        for record in failed:
            self.assertEqual(record['status_code'], 0)


if __name__ == '__main__':
    unittest.main()
