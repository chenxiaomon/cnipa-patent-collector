#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
单元测试：detection_logger.py

测试日志记录和数据序列化
"""

import glob
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from detection_logger import DetectionLogger, DetectionRecord


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


def _logger_in(tmpdir: str, name: str = 'log.jsonl') -> DetectionLogger:
    """在临时目录中构造 DetectionLogger（DB 也落在临时目录，不碰仓库数据）"""
    db_path = Path(tmpdir) / 'patents.db'
    with mock.patch('detection_logger.PATENTS_DB_FILE', db_path):
        return DetectionLogger(str(Path(tmpdir) / name))


class TestDetectionLoggerWritePath(unittest.TestCase):
    """DB + JSONL 双写路径"""

    def test_add_record_appends_jsonl_and_db(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            logger = _logger_in(tmpdir)
            logger.add_record(DetectionRecord(application_no='2023000000001', status_code=200))
            logger.add_record(DetectionRecord(application_no='2023000000002', status_code=0))
            lines = Path(logger.log_file).read_text(encoding='utf-8').splitlines()
            self.assertEqual(len(lines), 2)
            self.assertEqual(logger._db.count(), 2)

    def test_auto_backup_fires_on_interval_and_resets_counter(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            logger = _logger_in(tmpdir)
            logger.add_record(DetectionRecord(application_no='2023000000001', status_code=200))
            backup_pattern = str(Path(tmpdir) / 'log_backup_*.jsonl')

            logger._auto_backup(written=500)
            self.assertEqual(len(glob.glob(backup_pattern)), 1)
            self.assertEqual(logger._writes_since_backup, 0)

            logger._auto_backup(written=1)
            self.assertEqual(len(glob.glob(backup_pattern)), 1)
            self.assertEqual(logger._writes_since_backup, 1)

    def test_add_records_matches_looped_add_record(self):
        records = [
            DetectionRecord(application_no='2023000000001', status_code=200, zhuanlimc='专利一'),
            DetectionRecord(application_no='2023000000002', status_code=0, error_message='超时'),
            DetectionRecord(application_no='2023000000003', status_code=200,
                            fwxx_list=[{'fawenmc': '驳回决定'}]),
        ]
        with tempfile.TemporaryDirectory() as loop_dir, \
                tempfile.TemporaryDirectory() as batch_dir:
            looped = _logger_in(loop_dir)
            for r in records:
                looped.add_record(r)

            batched = _logger_in(batch_dir)
            self.assertEqual(batched.add_records(records), 3)

            def without_write_stamp(rows: list) -> list:
                # updated_at 由 DB 在写入时刻生成，两个 logger 必然不同，剔除后比较
                return [{k: v for k, v in row.items() if k != 'updated_at'} for row in rows]

            self.assertEqual(
                without_write_stamp(batched._db.get_all_records()),
                without_write_stamp(looped._db.get_all_records()),
            )
            self.assertEqual(
                Path(batched.log_file).read_text(encoding='utf-8'),
                Path(looped.log_file).read_text(encoding='utf-8'),
            )


if __name__ == '__main__':
    unittest.main()
