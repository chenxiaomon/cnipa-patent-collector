#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
单元测试：detection_logger.py

测试日志记录和数据序列化
"""

import unittest
import json
import tempfile
import os
from detection_logger import DetectionRecord


class TestDetectionRecord(unittest.TestCase):
    """DetectionRecord 数据结构测试"""

    def test_record_creation(self):
        """创建基础记录"""
        record = DetectionRecord(
            application_no='CN201880002233',
            status_code=200,
            response_summary='success'
        )
        self.assertEqual(record.application_no, 'CN201880002233')
        self.assertEqual(record.status_code, 200)
        self.assertEqual(record.response_summary, 'success')

    def test_record_with_patent_fields(self):
        """创建包含专利字段的记录"""
        record = DetectionRecord(
            application_no='CN201880002233',
            status_code=200,
            zhuanlimc='一种新型装置',
            shenqingrxm='申请人名字',
            anjianywzt='驳回等复审请求'
        )
        self.assertEqual(record.zhuanlimc, '一种新型装置')
        self.assertEqual(record.shenqingrxm, '申请人名字')
        self.assertEqual(record.anjianywzt, '驳回等复审请求')

    def test_record_with_fwxx(self):
        """创建包含发文信息的记录"""
        fwxx_list = [
            {'date': '2023-01-01', 'type': '驳回'},
            {'date': '2023-02-01', 'type': '再审'}
        ]
        record = DetectionRecord(
            application_no='CN201880002233',
            status_code=200,
            fwxx_list=fwxx_list
        )
        self.assertEqual(len(record.fwxx_list), 2)
        self.assertEqual(record.fwxx_list[0]['type'], '驳回')

    def test_record_to_dict(self):
        """序列化为字典"""
        record = DetectionRecord(
            application_no='CN201880002233',
            status_code=200,
            zhuanlimc='测试专利'
        )
        record_dict = record.to_dict()
        self.assertIsInstance(record_dict, dict)
        self.assertEqual(record_dict['application_no'], 'CN201880002233')
        self.assertEqual(record_dict['status_code'], 200)
        self.assertEqual(record_dict['zhuanlimc'], '测试专利')

    def test_failed_record(self):
        """失败记录"""
        record = DetectionRecord(
            application_no='CN201880002233',
            status_code=0,
            response_summary='MITM timeout',
            error_message='8s timeout'
        )
        self.assertEqual(record.status_code, 0)
        self.assertEqual(record.error_message, '8s timeout')

    def test_record_with_response_time(self):
        """包含响应时间的记录"""
        record = DetectionRecord(
            application_no='CN201880002233',
            status_code=200,
            response_time_ms=3456.78
        )
        self.assertEqual(record.response_time_ms, 3456.78)

    def test_record_timestamp(self):
        """时间戳"""
        record = DetectionRecord(
            application_no='CN201880002233',
            status_code=200
        )
        self.assertIsNotNone(record.timestamp)
        # timestamp 应该是 ISO 格式
        self.assertIn('T', record.timestamp)

    def test_empty_patent_fields(self):
        """空专利字段"""
        record = DetectionRecord(
            application_no='CN201880002233',
            status_code=0  # 失败
        )
        self.assertIsNone(record.zhuanlimc)
        self.assertIsNone(record.shenqingrxm)
        self.assertIsNone(record.anjianywzt)


class TestDetectionRecordSerialization(unittest.TestCase):
    """序列化和反序列化测试"""

    def test_json_serializable(self):
        """记录可被序列化为 JSON"""
        record = DetectionRecord(
            application_no='CN201880002233',
            status_code=200,
            zhuanlimc='测试'
        )
        record_dict = record.to_dict()
        json_str = json.dumps(record_dict, ensure_ascii=False)
        self.assertIsInstance(json_str, str)

    def test_json_deserialization(self):
        """从 JSON 反序列化"""
        record_dict = {
            'application_no': 'CN201880002233',
            'status_code': 200,
            'zhuanlimc': '测试',
            'timestamp': '2026-05-11T12:00:00'
        }
        json_str = json.dumps(record_dict)
        loaded_dict = json.loads(json_str)

        record = DetectionRecord(
            application_no=loaded_dict['application_no'],
            status_code=loaded_dict['status_code'],
            zhuanlimc=loaded_dict.get('zhuanlimc')
        )
        self.assertEqual(record.application_no, 'CN201880002233')
        self.assertEqual(record.zhuanlimc, '测试')

    def test_unicode_support(self):
        """支持中文和 Unicode"""
        record = DetectionRecord(
            application_no='CN201880002233',
            status_code=200,
            zhuanlimc='一种新型装置',
            shenqingrxm='张三李四',
            response_summary='成功采集'
        )
        record_dict = record.to_dict()
        json_str = json.dumps(record_dict, ensure_ascii=False)
        loaded = json.loads(json_str)

        self.assertEqual(loaded['zhuanlimc'], '一种新型装置')
        self.assertEqual(loaded['shenqingrxm'], '张三李四')


if __name__ == '__main__':
    unittest.main()
