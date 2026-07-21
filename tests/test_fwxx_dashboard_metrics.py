#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Regression tests for FWXX summary metrics, filters, and dashboard copy."""

import ast
import re
import tempfile
import unittest
from pathlib import Path

import settings
from db_manager import PatentsDB


REJECTION_STATUS = "驳回等复审请求"


class TemporaryPatentsDBTestCase(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.db = PatentsDB(Path(self.temporary_directory.name) / "patents.db")


class TestFwxxSummaryMetrics(TemporaryPatentsDBTestCase):
    def test_rejection_fwxx_progress_is_separate_from_fee_detail_progress(self):
        self.db.upsert({
            "application_no": "2026000000001",
            "anjianywzt": REJECTION_STATUS,
            "fwxx_list": [{"tongzhismc": "驳回决定", "fawenr": "20260315"}],
        })
        self.db.upsert({
            "application_no": "2026000000002",
            "anjianywzt": REJECTION_STATUS,
            "payable_fee_records": [],
            "paid_fee_records": [],
            "fee_receipt_dispatch_records": [],
        })
        self.db.upsert({
            "application_no": "2026000000003",
            "anjianywzt": REJECTION_STATUS,
            "fwxx_list": [],
            "payable_fee_records": [],
            "paid_fee_records": [],
            "fee_receipt_dispatch_records": [],
        })
        self.db.upsert({
            "application_no": "2026000000004",
            "anjianywzt": "专利权维持",
            "fwxx_list": [{"tongzhismc": "缴费通知书", "fawenr": "20260401"}],
        })

        summary = self.db.get_summary()

        self.assertEqual(summary["rejection"], 3)
        self.assertEqual(summary["rejection_fwxx_collected"], 2)
        self.assertEqual(summary["fwxx_pending"], 1)
        self.assertEqual(summary["fwxx_collected"], 3)
        self.assertEqual(summary["detail_enrichment_completed"], 1)
        self.assertEqual(summary["detail_enrichment_pending"], 2)
        self.assertEqual(summary["fee_details_completed"], 2)
        self.assertEqual(summary["fee_details_pending"], 1)


class TestFwxxNoticeFilters(TemporaryPatentsDBTestCase):
    def test_notice_and_applicant_dimensions_are_combined_with_or(self):
        self.db.upsert({
            "application_no": "2026000000010",
            "timestamp": "2026-01-01T00:00:00Z",
            "shenqingrxm": "甲公司",
        })
        self.db.upsert({
            "application_no": "2026000000011",
            "timestamp": "2026-01-02T00:00:00Z",
            "shenqingrxm": "乙公司",
            "fwxx_list": [{"tongzhismc": "补正通知书", "fawenr": "20260315"}],
        })
        self.db.upsert({
            "application_no": "2026000000012",
            "timestamp": "2026-01-03T00:00:00Z",
            "shenqingrxm": "丙公司",
        })

        records = self.db.query_filtered(
            applicants=["甲公司"],
            notice_name_contains="补正",
        )

        self.assertEqual(
            [record["application_no"] for record in records],
            ["2026000000010", "2026000000011"],
        )

    def test_notice_name_and_date_must_match_the_same_fwxx_entry(self):
        self.db.upsert({
            "application_no": "2026000000011",
            "timestamp": "2026-01-01T00:00:00Z",
            "fwxx_list": [
                {"tongzhismc": "第一次审查意见通知书", "fawenr": "20260115"},
                {"tongzhismc": "驳回决定", "fawenr": "20260315"},
            ],
        })
        self.db.upsert({
            "application_no": "2026000000012",
            "timestamp": "2026-01-02T00:00:00Z",
            "fwxx_list": [
                {"fawenmc": "发明专利申请驳回决定", "fawenr": "2026-03-31"},
            ],
        })
        self.db.upsert({
            "application_no": "2026000000013",
            "timestamp": "2026-01-03T00:00:00Z",
            "fwxx_list": [
                {"tongzhismc": "驳回决定", "fawenr": "20250101"},
                {"tongzhismc": "手续合格通知书", "fawenr": "20260320"},
            ],
        })
        self.db.upsert({
            "application_no": "2026000000014",
            "timestamp": "2026-01-04T00:00:00Z",
            "fwxx_list": [
                {"tongzhismc": "驳回决定", "fawenr": "20260401"},
            ],
        })

        records = self.db.query_filtered(
            notice_name_contains="驳回",
            notice_from="2026-03-01",
            notice_to="2026-03-31",
        )

        self.assertEqual(
            [record["application_no"] for record in records],
            ["2026000000011", "2026000000012"],
        )

    def test_notice_date_range_uses_fawenr_inside_fwxx_list(self):
        self.db.upsert({
            "application_no": "2026000000021",
            "timestamp": "2026-01-01T00:00:00Z",
            "bhsjtzs_xiazaisj": "2025-01-01",
            "fwxx_list": [{"tongzhismc": "手续合格通知书", "fawenr": "20260301"}],
        })
        self.db.upsert({
            "application_no": "2026000000022",
            "timestamp": "2026-01-02T00:00:00Z",
            "bhsjtzs_xiazaisj": "2025-01-02",
            "fwxx_list": [{"tongzhismc": "缴费通知书", "fawenr": "20260331"}],
        })
        self.db.upsert({
            "application_no": "2026000000023",
            "timestamp": "2026-01-03T00:00:00Z",
            "bhsjtzs_xiazaisj": "2026-03-15",
            "fwxx_list": [{"tongzhismc": "驳回决定", "fawenr": "20260228"}],
        })

        records = self.db.query_filtered(
            notice_from="2026-03-01",
            notice_to="2026-03-31",
        )

        self.assertEqual(
            [record["application_no"] for record in records],
            ["2026000000021", "2026000000022"],
        )

    def test_empty_null_and_malformed_fwxx_lists_do_not_match_or_raise(self):
        malformed_fwxx_lists = [
            None,
            [],
            "驳回决定",
            {"tongzhismc": "驳回决定", "fawenr": "20260315"},
            [None, 7, "驳回决定", {"tongzhismc": None, "fawenr": "not-a-date"}],
        ]
        for offset, fwxx_list in enumerate(malformed_fwxx_lists, start=1):
            self.db.upsert({
                "application_no": f"20260000001{offset:02d}",
                "timestamp": f"2026-02-{offset:02d}T00:00:00Z",
                "fwxx_list": fwxx_list,
            })
        self.db.upsert({
            "application_no": "2026000000199",
            "timestamp": "2026-02-20T00:00:00Z",
            "fwxx_list": [{"tongzhismc": "发明专利申请驳回决定", "fawenr": "20260315"}],
        })

        records = self.db.query_filtered(
            notice_name_contains="驳回",
            notice_from="2026-03-01",
            notice_to="2026-03-31",
        )

        self.assertEqual(
            [record["application_no"] for record in records],
            ["2026000000199"],
        )

    def test_legacy_rejection_download_date_filter_remains_supported(self):
        self.db.upsert({
            "application_no": "2026000000201",
            "timestamp": "2026-01-01T00:00:00Z",
            "bhsjtzs_xiazaisj": "2026-03-15",
            "fwxx_list": [{"tongzhismc": "驳回决定", "fawenr": "20250101"}],
        })
        self.db.upsert({
            "application_no": "2026000000202",
            "timestamp": "2026-01-02T00:00:00Z",
            "bhsjtzs_xiazaisj": "2026-04-01",
            "fwxx_list": [{"tongzhismc": "驳回决定", "fawenr": "20260315"}],
        })

        records = self.db.query_filtered(
            rejection_from="2026-03-01",
            rejection_to="2026-03-31",
        )

        self.assertEqual(
            [record["application_no"] for record in records],
            ["2026000000201"],
        )


class TestFwxxDashboardPresentation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source = (settings.BASE_DIR / "web_dashboard.py").read_text(encoding="utf-8")
        cls.dashboard_source = source
        syntax_tree = ast.parse(source)
        html_assignment = next(
            node
            for node in syntax_tree.body
            if isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == "HTML" for target in node.targets)
        )
        cls.html = ast.literal_eval(html_assignment.value)
        cls.compact_html = re.sub(r"\s+", " ", cls.html)

    def test_primary_fwxx_ring_uses_rejection_scoped_fwxx_progress(self):
        self.assertIn("<span>发文采集率</span>", self.compact_html)
        self.assertIn(
            "const fwxxC = data.business.rejection_fwxx_collected;",
            self.dashboard_source,
        )
        self.assertIn("const fwxxP = data.business.fwxx_pending;", self.dashboard_source)
        self.assertNotIn(
            "const fwxxC = data.business.detail_enrichment_completed;",
            self.dashboard_source,
        )
        self.assertNotIn(
            "const fwxxP = data.business.detail_enrichment_pending;",
            self.dashboard_source,
        )

    def test_fee_detail_completeness_is_displayed_as_secondary_context(self):
        self.assertIn(
            '<span>费用资料完整（辅助）</span><strong id="feeDetailsComplete">—</strong>',
            self.compact_html,
        )
        self.assertIn(
            '<span>费用资料待补</span><strong id="feeDetailsPending">—</strong>',
            self.compact_html,
        )
        self.assertIn("费用信息不影响发文采集完整度", self.html)
        self.assertRegex(
            self.dashboard_source,
            r"set\('#feeDetailsComplete',\s*fmtNumber\(data\.business\.fee_details_completed\)\)",
        )
        self.assertRegex(
            self.dashboard_source,
            r"set\('#feeDetailsPending',\s*fmtNumber\(data\.business\.fee_details_pending\)\)",
        )

    def test_overview_pending_fwxx_metric_uses_fwxx_pending(self):
        self.assertIn('<em id="mFwxx">0 待补发文</em>', self.html)
        self.assertRegex(
            self.dashboard_source,
            r"set\('#mFwxx',\s*fmtNumber\(data\.business\.fwxx_pending\)\s*\+\s*' 待补发文'\)",
        )

    def test_notice_filters_are_wired_from_export_form_to_database_query(self):
        self.assertIn('id="exportNoticeName"', self.html)
        self.assertIn('id="exportNoticeFrom"', self.html)
        self.assertIn('id="exportNoticeTo"', self.html)
        self.assertRegex(
            self.dashboard_source,
            r"notice_name_contains:\s*\$\('#exportNoticeName'\)\.value(?:\.trim\(\))?",
        )
        self.assertRegex(
            self.dashboard_source,
            r"notice_from:\s*\$\('#exportNoticeFrom'\)\.value",
        )
        self.assertRegex(
            self.dashboard_source,
            r"notice_to:\s*\$\('#exportNoticeTo'\)\.value",
        )
        for payload_key in ("notice_name_contains", "notice_from", "notice_to"):
            self.assertIn(f'payload.get("{payload_key}")', self.dashboard_source)
        query_call = re.search(
            r"_patents_db\.query_filtered\((?P<arguments>.*?)\n\s*\)",
            self.dashboard_source,
            re.DOTALL,
        )
        self.assertIsNotNone(query_call)
        query_arguments = query_call.group("arguments")
        self.assertIn("notice_name_contains=", query_arguments)
        self.assertIn("notice_from=", query_arguments)
        self.assertIn("notice_to=", query_arguments)


if __name__ == "__main__":
    unittest.main()
