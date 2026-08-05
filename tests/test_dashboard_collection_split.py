#!/usr/bin/env python3
"""Dashboard contracts for independent FWXX and fee collection jobs."""

from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path
from unittest import mock

import settings
import web_dashboard


class TestCollectionJobSpecs(unittest.TestCase):
    def setUp(self) -> None:
        self.python_patcher = mock.patch.object(
            web_dashboard,
            "resolve_task_python",
            return_value="python",
        )
        self.python_patcher.start()
        self.addCleanup(self.python_patcher.stop)

    def test_fwxx_actions_only_invoke_fwxx_collector(self):
        full_spec = web_dashboard.build_job_spec("collect_fwxx", {})
        test_spec = web_dashboard.build_job_spec("collect_fwxx", {"count": 5})
        app_spec = web_dashboard.build_job_spec(
            "collect_fwxx_app",
            {"app_no": "CN202411006597.0"},
        )

        self.assertEqual(full_spec["command"], ["python", "-u", "collect_fwxx.py"])
        self.assertEqual(
            test_spec["command"],
            ["python", "-u", "collect_fwxx.py", "--test", "5"],
        )
        self.assertEqual(
            app_spec["command"],
            ["python", "-u", "collect_fwxx.py", "--app", "2024110065970"],
        )
        self.assertIn("发文", full_spec["title"])
        self.assertNotIn("费用", full_spec["title"])
        self.assertNotIn("费用", app_spec["title"])

    def test_fee_actions_only_invoke_fee_collector(self):
        full_spec = web_dashboard.build_job_spec("collect_fees", {})
        test_spec = web_dashboard.build_job_spec("collect_fees", {"count": "7"})
        app_spec = web_dashboard.build_job_spec(
            "collect_fees_app",
            {"app_no": "CN202111504942.X"},
        )

        self.assertEqual(full_spec["command"], ["python", "-u", "collect_fees.py"])
        self.assertEqual(
            test_spec["command"],
            ["python", "-u", "collect_fees.py", "--test", "7"],
        )
        self.assertEqual(
            app_spec["command"],
            ["python", "-u", "collect_fees.py", "--app", "202111504942X"],
        )
        self.assertIn("费用", full_spec["title"])
        self.assertNotIn("发文", full_spec["title"])
        self.assertNotIn("发文", app_spec["title"])

    def test_fee_force_action_recollects_whole_dataset(self):
        force_spec = web_dashboard.build_job_spec("collect_fees", {"force": True})

        self.assertEqual(
            force_spec["command"],
            ["python", "-u", "collect_fees.py", "--force"],
        )
        self.assertIn("强制重采", force_spec["title"])

    def test_both_batch_actions_use_validated_normalized_request_files(self):
        raw_targets = "CN202411006597.0\nCN202111504942.X"
        request_path = Path("C:/tmp/manual_collection_request.txt")
        normalized_targets = ["2024110065970", "202111504942X"]
        with mock.patch.object(
            web_dashboard,
            "create_manual_fwxx_request",
            return_value=(request_path, normalized_targets),
        ) as create_request:
            fwxx_spec = web_dashboard.build_job_spec(
                "collect_fwxx_batch",
                {"app_nos": raw_targets},
            )
            fee_spec = web_dashboard.build_job_spec(
                "collect_fees_batch",
                {"app_nos": raw_targets},
            )

        self.assertEqual(create_request.call_count, 2)
        for request_call in create_request.call_args_list:
            self.assertEqual(request_call.args[0], raw_targets)
            self.assertEqual(request_call.kwargs["request_dir"], web_dashboard.FWXX_MANUAL_LIST_DIR)
            self.assertEqual(request_call.kwargs["max_app_nos"], web_dashboard.MAX_REQUEST_APP_NOS)
        for spec, script_name in (
            (fwxx_spec, "collect_fwxx.py"),
            (fee_spec, "collect_fees.py"),
        ):
            self.assertEqual(spec["command"][2], script_name)
            self.assertIn("--force", spec["command"])
            self.assertEqual(
                Path(spec["command"][spec["command"].index("--input") + 1]),
                request_path,
            )

    def test_fee_batch_rejects_an_invalid_target_before_start(self):
        with mock.patch.object(
            web_dashboard,
            "create_manual_fwxx_request",
            side_effect=ValueError("申请号格式不正确"),
        ) as create_request:
            with self.assertRaisesRegex(ValueError, "格式不正确"):
                web_dashboard.build_job_spec(
                    "collect_fees_batch",
                    {"app_nos": "CN202411006597.0\ninvalid"},
                )

        create_request.assert_called_once()


class TestDesktopCollectionExclusion(unittest.TestCase):
    def test_fee_actions_are_declared_as_desktop_browser_actions(self):
        self.assertTrue(
            {
                "collect_fees",
                "collect_fees_app",
                "collect_fees_batch",
            }.issubset(web_dashboard.DESKTOP_BROWSER_ACTIONS)
        )

    def test_active_desktop_job_blocks_a_different_collection_action(self):
        job_manager = web_dashboard.JobManager()
        active_job = web_dashboard.Job(
            id="active",
            action="main_full",
            title="主流程采集",
            command=["python", "main_automation.py"],
        )
        job_manager._jobs[active_job.id] = active_job

        with mock.patch.object(web_dashboard.subprocess, "Popen") as popen:
            with self.assertRaisesRegex(
                ValueError,
                "桌面浏览器.*主流程采集",
            ):
                job_manager.start("collect_fees", {})

        popen.assert_not_called()

    def test_stopping_fwxx_job_still_blocks_fee_collection(self):
        job_manager = web_dashboard.JobManager()
        active_job = web_dashboard.Job(
            id="stopping",
            action="collect_fwxx",
            title="发文信息采集",
            command=["python", "collect_fwxx.py"],
            status="stopping",
        )
        job_manager._jobs[active_job.id] = active_job

        with mock.patch.object(web_dashboard.subprocess, "Popen") as popen:
            with self.assertRaisesRegex(
                ValueError,
                "桌面浏览器.*发文信息采集",
            ):
                job_manager.start("collect_fees_app", {"app_no": "2024110065970"})

        popen.assert_not_called()


class TestCollectionSummary(unittest.TestCase):
    def test_summary_exposes_fee_pending_records(self):
        fee_pending = [
            {
                "application_no": "2024110065970",
                "anjianywzt": "驳回等复审请求",
                "timestamp": "2026-07-27T01:02:03Z",
            }
        ]
        db_summary = {
            "unique_count": 1,
            "success": 1,
            "failed": 0,
            "pending": 0,
            "success_rate": 100.0,
            "rejection": 1,
            "fwxx_collected": 1,
            "rejection_fwxx_collected": 1,
            "fwxx_pending": 0,
            "fee_dataset_total": 1,
            "fee_dataset_collected": 0,
            "fee_dataset_pending": 1,
            "fee_dataset_unregistered": 0,
            "status_counts": [],
            "applicant_counts": [],
            "recent": [],
            "daily_counts": [],
            "fwxx_pending_list": [],
            "fee_dataset_pending_list": fee_pending,
            "rejection_companies": [],
        }

        with (
            mock.patch.object(web_dashboard._patents_db, "get_summary", return_value=db_summary),
            mock.patch.object(web_dashboard._patents_db, "get_processed_app_nos", return_value=set()),
            mock.patch.object(web_dashboard._patents_db, "list_requests", return_value=[]),
            mock.patch.object(web_dashboard, "safe_json_load", return_value={}),
            mock.patch.object(web_dashboard, "safe_read_text", return_value=""),
            mock.patch.object(web_dashboard, "count_lines", return_value=0),
            mock.patch.object(web_dashboard, "file_info", return_value={}),
            mock.patch.object(web_dashboard, "port_open", return_value=False),
        ):
            summary = web_dashboard.build_summary(web_dashboard.JobManager())

        self.assertEqual(summary["fee_dataset_pending_list"], fee_pending)
        self.assertEqual(summary["business"]["fee_dataset_pending"], 1)


class TestAppendUniqueLines(unittest.TestCase):
    def test_creates_file_and_skips_existing_lines(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            list_path = Path(tmpdir) / "search_list.txt"
            self.assertEqual(
                web_dashboard._append_unique_lines_atomic(list_path, ["B", "A"]), 2
            )
            # 已存在的行不重复追加，只计新增
            self.assertEqual(
                web_dashboard._append_unique_lines_atomic(list_path, ["A", "C"]), 1
            )
            lines = list_path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(sorted(lines), ["A", "B", "C"])


class TestCollectionDashboardPresentation(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        source = (settings.BASE_DIR / "web_dashboard.py").read_text(encoding="utf-8")
        cls.source = source
        syntax_tree = ast.parse(source)
        html_assignment = next(
            node
            for node in syntax_tree.body
            if isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id == "HTML"
                for target in node.targets
            )
        )
        cls.html = ast.literal_eval(html_assignment.value)
        cls.compact_html = re.sub(r"\s+", " ", cls.html)

    def test_page_has_peer_fwxx_and_fee_collection_controls(self):
        self.assertIn("<h2>发文信息采集</h2>", self.compact_html)
        self.assertIn("<h2>费用信息采集</h2>", self.compact_html)
        for control_id in (
            "fwxxTestBtn",
            "fwxxSingleBtn",
            "fwxxBatchBtn",
            "feeTestBtn",
            "feeSingleBtn",
            "feeBatchBtn",
            "feeForceBtn",
            "feeTargetsFileInput",
            "importFeeTargetsBtn",
            "enrollUnregisteredBtn",
        ):
            self.assertIn(f'id="{control_id}"', self.html)
        self.assertIn('data-action="collect_fwxx"', self.html)
        self.assertIn('data-action="collect_fees"', self.html)
        self.assertNotIn("指定专利批量采集发文及费用", self.html)

    def test_page_has_independent_pending_tables_and_rendering(self):
        self.assertIn('id="fwxxPendingRows"', self.html)
        self.assertIn('id="feePendingRows"', self.html)
        self.assertIn(
            "renderFwxxPending(data.fwxx_pending_list || []);",
            self.source,
        )
        self.assertIn(
            "renderFeePending(data.fee_dataset_pending_list || []);",
            self.source,
        )

    def test_enroll_unregistered_button_posts_to_fee_targets_api(self):
        self.assertRegex(
            self.source,
            r"(?s)#enrollUnregisteredBtn'.*?api\('/api/fee-targets/enroll-unregistered'",
        )

    def test_fee_buttons_submit_through_the_existing_job_api(self):
        self.assertRegex(
            self.source,
            r"(?s)#feeTestBtn'.*?startJob\('collect_fees',\s*\{ count: 5 \}\)",
        )
        self.assertRegex(
            self.source,
            r"(?s)#feeSingleBtn'.*?startJob\('collect_fees_app'",
        )
        self.assertRegex(
            self.source,
            r"(?s)#feeBatchBtn'.*?startJob\('collect_fees_batch'",
        )


if __name__ == "__main__":
    unittest.main()
