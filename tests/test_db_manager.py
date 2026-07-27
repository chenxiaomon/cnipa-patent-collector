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

    def test_update_fields_round_trips_fee_records(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db = PatentsDB(Path(tmpdir) / "patents.db")
            payable = [{"feiyongzl": "实用新型专利第6年年费", "yingjiaofje": "1200"}]
            late_schedule = [{"jiaofeisj": "2026-02-04至2026-03-03", "zongji": "1320"}]
            paid = [{"yijiaofpjdm": "00010125", "yijiaofpjhm": "0026303067"}]
            receipts = [{"shoujufwsjh": "0026303067", "shoujufwfwrq": ""}]
            db.upsert({
                "application_no": "2026102909420",
                "status_code": 200,
                "payable_fee_records": payable,
                "late_fee_schedule_records": late_schedule,
                "fee_snapshot_at": "2026-07-18T08:30:00Z",
            })

            inserted = db.get_record("2026102909420")
            self.assertEqual(inserted["payable_fee_records"], payable)
            self.assertEqual(inserted["late_fee_schedule_records"], late_schedule)
            self.assertEqual(inserted["fee_snapshot_at"], "2026-07-18T08:30:00Z")

            db.update_fields("2026102909420", {
                "payable_fee_records": [],
                "late_fee_schedule_records": [],
                "paid_fee_records": paid,
                "fee_receipt_dispatch_records": receipts,
                "fee_snapshot_at": "2026-07-18T09:00:00Z",
            })

            record = db.get_record("2026102909420")
            self.assertEqual(record["payable_fee_records"], [])
            self.assertEqual(record["late_fee_schedule_records"], [])
            self.assertEqual(record["paid_fee_records"], paid)
            self.assertEqual(record["fee_receipt_dispatch_records"], receipts)
            self.assertEqual(record["fee_snapshot_at"], "2026-07-18T09:00:00Z")

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
            payable_fee_records = [{"feiyongzl": "恢复权利请求费"}]
            late_fee_schedule_records = [{"zongji": "1260"}]
            paid_fee_records = [{"yijiaofpjhm": "0026303067"}]
            fee_receipt_dispatch_records = [{"shoujufwsjh": "0026303067"}]
            db.upsert({
                "application_no": "2023000000001",
                "status_code": -1,
                "fwxx_list": fwxx_list,
                "bhsjtzs_xiazaisj": "2026-06-18",
                "payable_fee_records": payable_fee_records,
                "late_fee_schedule_records": late_fee_schedule_records,
                "paid_fee_records": paid_fee_records,
                "fee_receipt_dispatch_records": fee_receipt_dispatch_records,
                "fee_snapshot_at": "2026-07-18T08:30:00Z",
                "error_message": "old timeout",
            })
            db.upsert({
                "application_no": "2023000000001",
                "status_code": 200,
                "zhuanlimc": "补采成功的专利",
                "fwxx_list": None,
                "bhsjtzs_xiazaisj": None,
                "payable_fee_records": None,
                "late_fee_schedule_records": None,
                "paid_fee_records": None,
                "fee_receipt_dispatch_records": None,
                "fee_snapshot_at": None,
                "error_message": None,
            })
            record = db.get_record("2023000000001")
            self.assertEqual(record["status_code"], 200)
            self.assertEqual(record["zhuanlimc"], "补采成功的专利")
            self.assertEqual(record["fwxx_list"], fwxx_list)
            self.assertEqual(record["bhsjtzs_xiazaisj"], "2026-06-18")
            self.assertEqual(record["payable_fee_records"], payable_fee_records)
            self.assertEqual(record["late_fee_schedule_records"], late_fee_schedule_records)
            self.assertEqual(record["paid_fee_records"], paid_fee_records)
            self.assertEqual(
                record["fee_receipt_dispatch_records"],
                fee_receipt_dispatch_records,
            )
            self.assertEqual(record["fee_snapshot_at"], "2026-07-18T08:30:00Z")
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

    def test_initialization_adds_fee_columns_to_legacy_database(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "patents.db"
            with closing(sqlite3.connect(db_path)) as conn:
                conn.execute("""
                    CREATE TABLE patents (
                        application_no TEXT PRIMARY KEY,
                        status_code INTEGER,
                        timestamp TEXT,
                        updated_at TEXT,
                        anjianywzt TEXT,
                        fwxx_list TEXT,
                        shenqingrxm TEXT,
                        zhuanlilx TEXT
                    )
                """)
                conn.commit()

            PatentsDB(db_path)

            with closing(sqlite3.connect(db_path)) as conn:
                columns = {
                    row[1] for row in conn.execute("PRAGMA table_info(patents)").fetchall()
                }
            self.assertIn("paid_fee_records", columns)
            self.assertIn("fee_receipt_dispatch_records", columns)
            self.assertIn("payable_fee_records", columns)
            self.assertIn("late_fee_schedule_records", columns)
            self.assertIn("fee_snapshot_at", columns)

    def test_initialization_replaces_legacy_detail_pending_index_once(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "patents.db"
            with closing(sqlite3.connect(db_path)) as conn:
                conn.execute("""
                    CREATE TABLE patents (
                        application_no TEXT PRIMARY KEY,
                        status_code INTEGER,
                        timestamp TEXT,
                        updated_at TEXT,
                        anjianywzt TEXT,
                        fwxx_list TEXT,
                        paid_fee_records TEXT,
                        fee_receipt_dispatch_records TEXT,
                        shenqingrxm TEXT,
                        zhuanlilx TEXT
                    )
                """)
                conn.execute("""
                    CREATE INDEX idx_detail_enrichment_pending ON patents(anjianywzt)
                    WHERE fwxx_list IS NULL OR paid_fee_records IS NULL
                       OR fee_receipt_dispatch_records IS NULL
                """)
                conn.commit()

            PatentsDB(db_path)

            with closing(sqlite3.connect(db_path)) as conn:
                index_sql = conn.execute(
                    "SELECT sql FROM sqlite_master WHERE type='index' "
                    "AND name='idx_detail_enrichment_pending'"
                ).fetchone()[0]
                schema_version = conn.execute("PRAGMA schema_version").fetchone()[0]
            self.assertIn("fwxx_list IS NULL", index_sql)
            self.assertIn("payable_fee_records IS NULL", index_sql)
            self.assertIn("paid_fee_records IS NULL", index_sql)
            self.assertIn("fee_receipt_dispatch_records IS NULL", index_sql)
            self.assertNotIn("late_fee_schedule_records", index_sql)

            PatentsDB(db_path)
            with closing(sqlite3.connect(db_path)) as conn:
                self.assertEqual(
                    conn.execute("PRAGMA schema_version").fetchone()[0],
                    schema_version,
                )

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


class TestDetailEnrichmentProgress(unittest.TestCase):
    def test_only_complete_records_are_skipped_by_resume(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db = PatentsDB(Path(tmpdir) / "patents.db")
            complete_fields = {
                "fwxx_list": [],
                "payable_fee_records": [],
                "paid_fee_records": [],
                "fee_receipt_dispatch_records": [],
            }
            db.upsert({
                "application_no": "2023000000001",
                "anjianywzt": "驳回等复审请求",
                **complete_fields,
            })
            db.upsert({
                "application_no": "2023000000002",
                "anjianywzt": "驳回等复审请求",
                "fwxx_list": [{"tongzhismc": "驳回决定"}],
                "paid_fee_records": [],
                "fee_receipt_dispatch_records": [],
            })
            db.upsert({
                "application_no": "2023000000003",
                "anjianywzt": "专利权维持",
            })

            self.assertEqual(
                db.detail_enrichment_completed_app_nos(),
                {"2023000000001"},
            )
            self.assertEqual(
                db.detail_enrichment_pending_app_nos(),
                ["2023000000002"],
            )
            complete_record = db.get_record("2023000000001")
            self.assertIsNone(complete_record["late_fee_schedule_records"])
            self.assertIsNone(complete_record["fee_snapshot_at"])
            pending_record = db.get_record("2023000000002")
            self.assertIsNone(pending_record["payable_fee_records"])

            summary = db.get_summary()
            self.assertEqual(summary["detail_enrichment_completed"], 1)
            self.assertEqual(summary["detail_enrichment_pending"], 1)
            self.assertEqual(summary["fwxx_pending"], 0)
            self.assertEqual(summary["fwxx_pending_list"], [])
            self.assertEqual(
                [row["application_no"] for row in summary["detail_enrichment_pending_list"]],
                ["2023000000002"],
            )


class TestFeeDetailsProgress(unittest.TestCase):
    def test_pending_query_requires_rejection_status_and_any_missing_required_field(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db = PatentsDB(Path(tmpdir) / "patents.db")
            complete_fee_fields = {
                "payable_fee_records": [],
                "paid_fee_records": [],
                "fee_receipt_dispatch_records": [],
            }
            db.upsert({
                "application_no": "2023000000001",
                "anjianywzt": "\u9a73\u56de\u7b49\u590d\u5ba1\u8bf7\u6c42",
                **complete_fee_fields,
            })
            db.upsert({
                "application_no": "2023000000002",
                "anjianywzt": "\u9a73\u56de\u7b49\u590d\u5ba1\u8bf7\u6c42",
                "fwxx_list": [{"tongzhismc": "notice"}],
                "paid_fee_records": [],
                "fee_receipt_dispatch_records": [],
            })
            db.upsert({
                "application_no": "2023000000003",
                "anjianywzt": "active",
            })

            self.assertEqual(
                db.fee_details_pending_app_nos(),
                ["2023000000002"],
            )
            self.assertEqual(
                db.fee_details_pending_app_nos("active"),
                ["2023000000003"],
            )
            complete_record = db.get_record("2023000000001")
            self.assertIsNone(complete_record["late_fee_schedule_records"])
            self.assertIsNone(complete_record["fwxx_list"])

    def test_completed_query_accepts_empty_arrays_and_does_not_filter_status(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db = PatentsDB(Path(tmpdir) / "patents.db")
            db.upsert({
                "application_no": "2023000000001",
                "anjianywzt": "\u9a73\u56de\u7b49\u590d\u5ba1\u8bf7\u6c42",
                "payable_fee_records": [],
                "paid_fee_records": [],
                "fee_receipt_dispatch_records": [],
            })
            db.upsert({
                "application_no": "2023000000002",
                "anjianywzt": "active",
                "payable_fee_records": [{"feiyongzl": "annual-fee"}],
                "paid_fee_records": [],
                "fee_receipt_dispatch_records": [],
            })
            db.upsert({
                "application_no": "2023000000003",
                "anjianywzt": "\u9a73\u56de\u7b49\u590d\u5ba1\u8bf7\u6c42",
                "payable_fee_records": [],
                "paid_fee_records": [],
            })

            self.assertEqual(
                db.fee_details_completed_app_nos(),
                {"2023000000001", "2023000000002"},
            )

    def test_summary_lists_latest_twenty_fee_pending_rejection_records(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db = PatentsDB(Path(tmpdir) / "patents.db")
            for day in range(1, 23):
                db.upsert({
                    "application_no": f"2023000000{day:03d}",
                    "anjianywzt": "\u9a73\u56de\u7b49\u590d\u5ba1\u8bf7\u6c42",
                    "timestamp": f"2026-07-{day:02d}T08:00:00Z",
                })
            db.upsert({
                "application_no": "2023000000098",
                "anjianywzt": "active",
                "timestamp": "2026-07-31T08:00:00Z",
            })
            db.upsert({
                "application_no": "2023000000099",
                "anjianywzt": "\u9a73\u56de\u7b49\u590d\u5ba1\u8bf7\u6c42",
                "timestamp": "2026-07-30T08:00:00Z",
                "payable_fee_records": [],
                "paid_fee_records": [],
                "fee_receipt_dispatch_records": [],
            })

            pending_rows = db.get_summary()["fee_details_pending_list"]

            self.assertEqual(len(pending_rows), 20)
            self.assertEqual(
                [row["application_no"] for row in pending_rows],
                [f"2023000000{day:03d}" for day in range(22, 2, -1)],
            )


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
