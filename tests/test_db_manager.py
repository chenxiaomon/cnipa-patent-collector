#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""单元测试：SQLite 数据库管理器"""

import tempfile
import unittest
from pathlib import Path

from db_manager import PatentsDB


class TestPatentsDBUpdateFields(unittest.TestCase):
    """字段级更新测试"""

    def test_update_fields_serializes_json_fields(self):
        """update_fields 可直接写入 fwxx_list / bhsjtzs_data 这类 JSON 字段"""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = PatentsDB(Path(tmpdir) / "patents.db")
            db.upsert({
                "application_no": "202310411762X",
                "status_code": 200,
                "anjianywzt": "驳回等复审请求",
            })

            fwxx_list = [{"fawenmc": "驳回决定", "fawenrq": "2026-06-18"}]
            bhsjtzs_data = {"xiazaisj": "2026-06-18", "items": [{"name": "通知书"}]}

            db.update_fields("202310411762X", {
                "fwxx_list": fwxx_list,
                "bhsjtzs_data": bhsjtzs_data,
            })

            record = db.get_record("202310411762X")
            self.assertEqual(record["fwxx_list"], fwxx_list)
            self.assertEqual(record["bhsjtzs_data"], bhsjtzs_data)


if __name__ == "__main__":
    unittest.main()
