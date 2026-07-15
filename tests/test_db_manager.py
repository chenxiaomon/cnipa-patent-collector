#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""单元测试：SQLite 数据库管理器"""

import tempfile
import threading
import time
import unittest
import sqlite3
from contextlib import closing
from pathlib import Path

from db_manager import SYNC_CURSOR_FIELD, PatentsDB


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

    def test_summarize_record_import_reports_new_updated_and_time_range(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db = PatentsDB(Path(tmpdir) / "patents.db")
            db.upsert({"application_no": "2023000000001", "timestamp": "2026-01-01T00:00:00Z"})
            summary = db.summarize_record_import([
                {"application_no": "2023000000001", "timestamp": "2026-02-02T00:00:00Z"},
                {"application_no": "2023000000002", "timestamp": "2026-02-01T00:00:00Z"},
            ])
            self.assertEqual(summary["records"], 2)
            self.assertEqual(summary["new_applications"], 1)
            self.assertEqual(summary["updated_applications"], 1)
            self.assertEqual(summary["timestamp_from"], "2026-02-01T00:00:00Z")
            self.assertEqual(summary["timestamp_to"], "2026-02-02T00:00:00Z")

    def test_pending_records_are_not_processed_or_failed(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db = PatentsDB(Path(tmpdir) / "patents.db")
            db.upsert({"application_no": "2023000000001", "status_code": None})
            db.upsert({"application_no": "2023000000002", "status_code": 0})
            self.assertEqual(db.get_processed_app_nos(), {"2023000000002"})
            self.assertEqual(db.mark_unattempted_records_pending(), 1)
            stats = db.get_stats()
            self.assertEqual(stats["failed"], 1)
            self.assertEqual(stats["pending"], 1)

    def test_export_delta_uses_database_modification_time(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db = PatentsDB(Path(tmpdir) / "patents.db")
            db.upsert({"application_no": "2023000000001", "timestamp": None})
            first_pull = db.export_delta("1970-01-01T00:00:00Z")
            self.assertEqual([record["application_no"] for record in first_pull], ["2023000000001"])
            cursor = first_pull[0][SYNC_CURSOR_FIELD]

            time.sleep(0.002)
            db.update_fields("2023000000001", {"zhuanlimc": "补充已有记录"})
            second_pull = db.export_delta(cursor)
            self.assertEqual([record["application_no"] for record in second_pull], ["2023000000001"])
            self.assertEqual(second_pull[0]["zhuanlimc"], "补充已有记录")
            self.assertIsNone(second_pull[0]["timestamp"])
            self.assertGreater(second_pull[0][SYNC_CURSOR_FIELD], cursor)

    def test_database_initialization_backfills_legacy_sync_cursor(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "patents.db"
            db = PatentsDB(db_path)
            db.upsert({"application_no": "2023000000001", "timestamp": None})
            with closing(sqlite3.connect(db_path)) as conn:
                conn.execute(
                    "UPDATE patents SET updated_at=NULL WHERE application_no=?",
                    ("2023000000001",),
                )
                conn.commit()

            reopened = PatentsDB(db_path)
            records = reopened.export_delta("1970-01-01T00:00:00Z")
            self.assertEqual([record["application_no"] for record in records], ["2023000000001"])
            self.assertTrue(records[0][SYNC_CURSOR_FIELD])

    def test_successful_recollection_preserves_existing_enrichment(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db = PatentsDB(Path(tmpdir) / "patents.db")
            fwxx_list = [{"fawenmc": "驳回决定"}]
            db.upsert({
                "application_no": "2023000000001",
                "status_code": -1,
                "fwxx_list": fwxx_list,
                "bhsjtzs_xiazaisj": "2026-06-18",
                "error_message": "old timeout",
            })
            db.upsert({
                "application_no": "2023000000001",
                "status_code": 200,
                "zhuanlimc": "补采成功的专利",
                "fwxx_list": None,
                "bhsjtzs_xiazaisj": None,
                "error_message": None,
            })
            record = db.get_record("2023000000001")
            self.assertEqual(record["status_code"], 200)
            self.assertEqual(record["zhuanlimc"], "补采成功的专利")
            self.assertEqual(record["fwxx_list"], fwxx_list)
            self.assertEqual(record["bhsjtzs_xiazaisj"], "2026-06-18")
            self.assertIsNone(record["error_message"])


class TestSnapshotPreviousStatus(unittest.TestCase):
    """采集前状态快照（previous_status）"""

    def test_snapshot_copies_status_and_skips_blank(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db = PatentsDB(Path(tmpdir) / "patents.db")
            db.upsert({"application_no": "2023000000001", "anjianywzt": "驳回等复审请求"})
            db.upsert({"application_no": "2023000000002", "anjianywzt": "专利权维持"})
            db.upsert({"application_no": "2023000000003", "anjianywzt": ""})
            db.upsert({"application_no": "2023000000004"})

            count = db.snapshot_previous_status()

            self.assertEqual(count, 2)
            self.assertEqual(db.get_record("2023000000001")["previous_status"], "驳回等复审请求")
            self.assertEqual(db.get_record("2023000000002")["previous_status"], "专利权维持")
            self.assertIsNone(db.get_record("2023000000003")["previous_status"])
            self.assertIsNone(db.get_record("2023000000004")["previous_status"])


class TestConnectionLifecycle(unittest.TestCase):
    """数据库连接在操作结束后释放，兼容 Windows 文件锁。"""

    def test_connect_closes_connection_after_use(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db = PatentsDB(Path(tmpdir) / "patents.db")
            with db._connect() as connection:
                self.assertEqual(connection.execute("SELECT 1").fetchone()[0], 1)
            with self.assertRaises(sqlite3.ProgrammingError):
                connection.execute("SELECT 1")

    def test_failed_write_rolls_back_reused_connection(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db = PatentsDB(Path(tmpdir) / "patents.db")
            with self.assertRaises(RuntimeError):
                with db._connect() as conn:
                    conn.execute(
                        "INSERT INTO patents (application_no) VALUES (?)",
                        ("2023000000009",),
                    )
                    raise RuntimeError("simulated failure before commit")
            self.assertIsNone(db.get_record("2023000000009"))
            db.upsert({"application_no": "2023000000001"})
            self.assertEqual(db.count(), 1)

    def test_concurrent_upserts_across_threads(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db = PatentsDB(Path(tmpdir) / "patents.db")

            def upsert_batch_of_25(worker: int) -> None:
                for i in range(25):
                    db.upsert({"application_no": f"20230000{worker:02d}{i:03d}"})

            workers = [threading.Thread(target=upsert_batch_of_25, args=(w,)) for w in range(4)]
            for t in workers:
                t.start()
            for t in workers:
                t.join()
            self.assertEqual(db.count(), 100)


class TestFwxxCollectedAppNos(unittest.TestCase):
    """独立采集模式断点续传查询"""

    def test_returns_only_records_with_fwxx(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db = PatentsDB(Path(tmpdir) / "patents.db")
            db.upsert({
                "application_no": "2023000000001",
                "fwxx_list": [{"fawenmc": "驳回决定"}],
            })
            db.upsert({"application_no": "2023000000002"})
            self.assertEqual(db.fwxx_collected_app_nos(), {"2023000000001"})


class TestPatentsDBApplicantSplitting(unittest.TestCase):
    def test_company_views_split_comma_separated_applicants(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db = PatentsDB(Path(tmpdir) / "patents.db")
            db.upsert({
                "application_no": "202410000001",
                "status_code": 200,
                "shenqingrxm": "宁波奥克斯电气有限公司,奥克斯空调股份有限公司",
                "zhuanlilx": "发明",
                "anjianywzt": "驳回等复审请求",
                "timestamp": "2026-07-07T10:00:00",
            })

            company_rows = db.get_company_meta_rows(["驳回等复审请求"])
            companies = {row["name"]: row for row in company_rows}

            self.assertIn("宁波奥克斯电气有限公司", companies)
            self.assertIn("奥克斯空调股份有限公司", companies)
            self.assertNotIn("宁波奥克斯电气有限公司,奥克斯空调股份有限公司", companies)
            self.assertEqual(companies["宁波奥克斯电气有限公司"]["total_count"], 1)
            self.assertEqual(companies["奥克斯空调股份有限公司"]["total_count"], 1)

            applicants = dict(db.list_applicants())
            self.assertEqual(applicants["宁波奥克斯电气有限公司"], 1)
            self.assertEqual(applicants["奥克斯空调股份有限公司"], 1)

            filtered = db.query_filtered(applicants=["奥克斯空调股份有限公司"])
            self.assertEqual([record["application_no"] for record in filtered], ["202410000001"])

            summary_companies = {
                item["name"]: item["invention_count"]
                for item in db.get_summary()["rejection_companies"]
            }
            self.assertEqual(summary_companies["宁波奥克斯电气有限公司"], 1)
            self.assertEqual(summary_companies["奥克斯空调股份有限公司"], 1)


if __name__ == "__main__":
    unittest.main()
