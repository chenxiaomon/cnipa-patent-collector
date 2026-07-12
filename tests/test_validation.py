#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
单元测试：数据验证和质量检查

测试采集数据的一致性、有效性等（读取 detection_log.jsonl）
"""

import unittest
from detection_logger import DetectionLogger
from machine_identity import MASTER_ROLE, read_machine_role


def _load_log_data():
    """通过 DetectionLogger 加载记录，返回与旧格式兼容的 dict"""
    logger = DetectionLogger()
    records = logger._load_records()
    stats = logger.get_stats()
    return {
        'metadata': {
            'total_records': stats['total'],
            'successful': stats['success'],
            'failed': stats['failed'],
            'pending': stats['pending'],
            'success_rate_percent': round(
                100 * stats['success'] / max(1, stats['success'] + stats['failed']), 2
            ),
        },
        'records': records,
    }


class TestDetectionLogStructure(unittest.TestCase):
    """检查 detection_log.jsonl 的结构"""

    @classmethod
    def setUpClass(cls):
        cls.log_data = _load_log_data()
        if not cls.log_data['records']:
            raise unittest.SkipTest("生产 DB 为空，跳过数据质量断言（CI 环境预期行为）")

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
        for field in ['total_records', 'successful', 'failed', 'pending', 'success_rate_percent']:
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
        self.assertEqual(metadata['total_records'], len(records))
        actual_successful = sum(1 for r in records if r.get('status_code') == 200)
        self.assertEqual(metadata['successful'], actual_successful)

    def test_no_duplicate_application_nos(self):
        """申请号无重复"""
        app_nos = [r['application_no'] for r in self.log_data['records']]
        self.assertEqual(len(app_nos), len(set(app_nos)),
                         f"发现 {len(app_nos) - len(set(app_nos))} 个重复申请号")

    def test_valid_status_codes(self):
        """状态码有效"""
        role = read_machine_role()
        valid_codes = {-1, 0, 200}
        if role != MASTER_ROLE:
            valid_codes.add(None)
        for record in self.log_data['records']:
            self.assertIn(record.get('status_code'), valid_codes,
                          f"{record['application_no']}: 无效的状态码 {record.get('status_code')}")
        if role == MASTER_ROLE:
            null_count = sum(1 for record in self.log_data['records'] if record.get('status_code') is None)
            self.assertEqual(null_count, 0, 'master 必须先执行 normalize_pending_status.py --apply')

    def test_anjianywzt_distribution(self):
        """anjianywzt 有真实分布"""
        values = {}
        for r in self.log_data['records']:
            v = r.get('anjianywzt', 'N/A')
            values[v] = values.get(v, 0) + 1
        self.assertGreater(len(values), 1, "anjianywzt 分布过单一")
        self.assertIn('驳回等复审请求', values, "缺少'驳回等复审请求'分类")

    def test_falvzt_unusable(self):
        """falvzt 全为 '--'（不可用）"""
        successful = [r for r in self.log_data['records'] if r.get('status_code') == 200]
        for value in set(r.get('falvzt') for r in successful):
            self.assertIn(value, ['--', None], f"falvzt 包含非空值: {value}")

    def test_fwxx_coverage(self):
        """发文覆盖率统计（应 > 90%）"""
        records = self.log_data['records']
        huihe = [r for r in records if r.get('anjianywzt') == '驳回等复审请求']
        if not huihe:
            self.skipTest("无驳回案件记录，跳过发文覆盖率断言")
        with_fwxx = [r for r in huihe if r.get('fwxx_list')]
        coverage = len(with_fwxx) / len(huihe) * 100
        self.assertGreater(coverage, 90, f"发文覆盖率过低: {coverage:.2f}%")


class TestRecordCompletion(unittest.TestCase):
    """检查记录的完整性"""

    @classmethod
    def setUpClass(cls):
        cls.log_data = _load_log_data()
        if not cls.log_data['records']:
            raise unittest.SkipTest("生产 DB 为空，跳过记录完整性断言（CI 环境预期行为）")

    def test_successful_records_have_patent_fields(self):
        """成功的记录应该有专利字段"""
        successful = [r for r in self.log_data['records'] if r.get('status_code') == 200]
        for record in successful[:10]:
            self.assertIsNotNone(record.get('zhuanlimc'),
                                 f"{record['application_no']}: 缺少专利名称")
            self.assertIsNotNone(record.get('shenqingrxm'),
                                 f"{record['application_no']}: 缺少申请人")

    def test_failed_records_marked(self):
        """失败的记录应该标记为 status_code=0"""
        failed = [r for r in self.log_data['records'] if r.get('status_code') == 0]
        self.assertGreater(len(failed), 0, "应该有失败的记录")
        for record in failed:
            self.assertEqual(record['status_code'], 0)


if __name__ == '__main__':
    unittest.main()
