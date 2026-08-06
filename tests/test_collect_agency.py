#!/usr/bin/env python
# -*- coding: utf-8 -*-

import csv
import json
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import collect_agency


def agency_ack(
    daili_jg="新代理机构",
    persistence_status="updated",
    daili_r="代理人",
    attempt_id="attempt-current",
):
    return {
        "attempt_id": attempt_id,
        "captured_at": "2026-08-06T01:02:03Z",
        "daili_jg": daili_jg,
        "daili_r": daili_r,
        "persistence_status": persistence_status,
    }


class TestAgencyTargetLoading(unittest.TestCase):
    def test_explicit_file_keeps_all_unique_targets_without_resume_filter(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            request_file = Path(temporary_directory) / "targets.txt"
            request_file.write_text(
                "CN202411006597.0\n202111504942X\nCN202411006597.0\n",
                encoding="utf-8",
            )

            targets = collect_agency.load_requested_targets(
                input_file=str(request_file)
            )

        self.assertEqual(targets, ["2024110065970", "202111504942X"])

    def test_cli_has_no_force_or_automatic_dataset_mode(self):
        parser = collect_agency._build_argument_parser()
        destinations = {action.dest for action in parser._actions}

        self.assertNotIn("force", destinations)
        parsed = parser.parse_args(["--app", "2024110065970", "--test", "1"])
        self.assertEqual(parsed.app, "2024110065970")
        self.assertEqual(parsed.test, 1)


class TestAgencyDetailFlow(unittest.TestCase):
    @patch("collect_agency.time.sleep")
    @patch("collect_agency.pyautogui.hotkey")
    @patch("collect_agency.poll_cache_for_key")
    @patch("collect_agency.clear_matching_agency_attempt")
    @patch("collect_agency.begin_agency_attempt")
    @patch("collect_agency.InputService")
    @patch("collect_agency.CoordinateService")
    @patch("collect_agency.is_browser_alive", return_value=True)
    def test_detail_flow_never_loads_or_clicks_fwxx_or_fee_menus(
        self,
        _browser_alive,
        coordinate_service,
        input_service,
        begin_attempt,
        clear_attempt,
        poll_cache,
        close_tab,
        _sleep,
    ):
        driver = MagicMock()
        driver.page_source = "搜索结果：CN202411006597.0"
        driver.window_handles = ["search"]
        events = []

        def reveal_detail_tab(*_args, **_kwargs):
            events.append("click")
            driver.window_handles.append("detail")

        def close_detail_tab(*_args, **_kwargs):
            driver.window_handles.remove("detail")

        begin_attempt.side_effect = lambda _app_no: (
            events.append("begin")
            or {
                "application_no": "2024110065970",
                "attempt_id": "attempt-current",
                "started_at": "2026-08-06T01:02:00Z",
            }
        )
        input_service.move_and_click.side_effect = reveal_detail_tab
        close_tab.side_effect = close_detail_tab
        expected_ack = agency_ack()
        poll_cache.return_value = expected_ack

        collected = collect_agency.collect_one_agency(
            driver,
            "2024110065970",
            input_x=1,
            input_y=2,
            button_x=3,
            button_y=4,
            link_x=5,
            link_y=6,
        )

        self.assertEqual(collected, expected_ack)
        self.assertEqual(events, ["begin", "click"])
        begin_attempt.assert_called_once_with("2024110065970")
        clear_attempt.assert_called_once_with("attempt-current")
        poll_arguments = poll_cache.call_args
        self.assertEqual(poll_arguments.args[:2], (
            collect_agency.PATENT_AGENCY_CACHE_FILE,
            "2024110065970",
        ))
        self.assertEqual(
            poll_arguments.kwargs["max_wait"],
            collect_agency.FWXX_CACHE_POLL_TIMEOUT,
        )
        ack_validator = poll_arguments.kwargs["validate"]
        self.assertTrue(ack_validator(expected_ack))
        self.assertFalse(ack_validator(agency_ack(attempt_id="attempt-old")))
        input_service.move_and_click.assert_called_once_with(
            5,
            6,
            post_click_wait=collect_agency.FWXX_DETAIL_CLICK_WAIT,
        )
        coordinate_service.load_or_record_fwxx_coordinates.assert_not_called()
        coordinate_service.load_or_record_fee_menu_coordinates.assert_not_called()
        close_tab.assert_called_once_with("ctrl", "w")
        self.assertEqual(driver.switch_to.window.call_args_list[0], call("search"))

    @patch("collect_agency.poll_cache_for_key")
    @patch("collect_agency.clear_matching_agency_attempt")
    @patch("collect_agency.begin_agency_attempt", side_effect=OSError("denied"))
    @patch("collect_agency.InputService")
    @patch("collect_agency.is_browser_alive", return_value=True)
    def test_marker_publish_failure_stops_before_detail_click(
        self,
        _browser_alive,
        input_service,
        _begin_attempt,
        clear_attempt,
        poll_cache,
    ):
        driver = MagicMock()
        driver.window_handles = ["search"]
        driver.page_source = "2024110065970"

        collected = collect_agency.collect_one_agency(
            driver,
            "2024110065970",
            input_x=1,
            input_y=2,
            button_x=3,
            button_y=4,
            link_x=5,
            link_y=6,
        )

        self.assertIsNone(collected)
        input_service.type_in_search.assert_called_once()
        input_service.move_and_click.assert_not_called()
        poll_cache.assert_not_called()
        clear_attempt.assert_not_called()

    @patch("collect_agency.is_browser_alive", return_value=True)
    def test_multiple_initial_tabs_abort_the_batch(self, _browser_alive):
        driver = MagicMock()
        driver.window_handles = ["search", "stale-detail"]

        with self.assertRaises(collect_agency.AgencyCollectionFatalError):
            collect_agency.collect_one_agency(
                driver, "2024110065970", 1, 2, 3, 4, 5, 6
            )

    @patch("collect_agency.clear_matching_agency_attempt")
    @patch("collect_agency.begin_agency_attempt", return_value={
        "application_no": "2024110065970",
        "attempt_id": "attempt-current",
        "started_at": "2026-08-06T01:02:00Z",
    })
    @patch("collect_agency.InputService")
    @patch("collect_agency.is_browser_alive", return_value=True)
    def test_unconfirmed_search_result_aborts_before_detail_click(
        self,
        _browser_alive,
        input_service,
        begin_attempt,
        clear_attempt,
    ):
        driver = MagicMock()
        driver.window_handles = ["search"]
        driver.page_source = "仍然显示上一件 202111504942X"

        with self.assertRaises(collect_agency.AgencyCollectionFatalError):
            collect_agency.collect_one_agency(
                driver, "2024110065970", 1, 2, 3, 4, 5, 6
            )

        begin_attempt.assert_not_called()
        clear_attempt.assert_not_called()
        input_service.move_and_click.assert_not_called()

    @patch("collect_agency.clear_matching_agency_attempt")
    @patch("collect_agency.begin_agency_attempt", return_value={
        "application_no": "2024110065970",
        "attempt_id": "attempt-current",
        "started_at": "2026-08-06T01:02:00Z",
    })
    @patch("collect_agency.InputService")
    @patch("collect_agency.is_browser_alive", return_value=True)
    def test_detail_tab_not_opened_aborts_and_clears_attempt(
        self,
        _browser_alive,
        input_service,
        _begin_attempt,
        clear_attempt,
    ):
        driver = MagicMock()
        driver.window_handles = ["search"]
        driver.page_source = "2024110065970"

        with self.assertRaises(collect_agency.AgencyCollectionFatalError):
            collect_agency.collect_one_agency(
                driver, "2024110065970", 1, 2, 3, 4, 5, 6
            )

        input_service.move_and_click.assert_called_once()
        clear_attempt.assert_called_once_with("attempt-current")

    @patch("collect_agency.time.sleep")
    @patch("collect_agency.pyautogui.hotkey")
    @patch("collect_agency.poll_cache_for_key", return_value=agency_ack())
    @patch("collect_agency.clear_matching_agency_attempt")
    @patch("collect_agency.begin_agency_attempt", return_value={
        "application_no": "2024110065970",
        "attempt_id": "attempt-current",
        "started_at": "2026-08-06T01:02:00Z",
    })
    @patch("collect_agency.InputService")
    @patch("collect_agency.is_browser_alive", return_value=True)
    def test_failed_detail_close_aborts_the_batch(
        self,
        _browser_alive,
        input_service,
        _begin_attempt,
        _clear_attempt,
        _poll_cache,
        _close_tab,
        _sleep,
    ):
        driver = MagicMock()
        driver.window_handles = ["search"]
        driver.page_source = "2024110065970"
        input_service.move_and_click.side_effect = lambda *_args, **_kwargs: (
            driver.window_handles.append("detail")
        )

        with self.assertRaises(collect_agency.AgencyCollectionFatalError):
            collect_agency.collect_one_agency(
                driver, "2024110065970", 1, 2, 3, 4, 5, 6
            )


class TestAgencyClassificationAndReports(unittest.TestCase):
    def test_all_ack_classifications_preserve_old_and_official_agencies(self):
        cases = (
            ("changed", "旧机构", agency_ack("新机构", "updated")),
            ("unchanged", "同一机构", agency_ack("同一机构", "updated")),
            ("first_collected", None, agency_ack("首次机构", "updated")),
            ("official_empty", "旧机构", agency_ack(None, "official_empty", None)),
            ("unmatched", None, agency_ack("官方机构", "unmatched")),
            (
                "persistence_error",
                "旧机构",
                agency_ack("官方机构", "persistence_error"),
            ),
            ("timeout", "旧机构", None),
        )

        for expected_classification, old_agency, ack_payload in cases:
            with self.subTest(classification=expected_classification):
                report_record = collect_agency.classify_agency_ack(
                    "2024110065970",
                    old_agency,
                    ack_payload,
                )
                self.assertEqual(
                    report_record["classification"],
                    expected_classification,
                )
                self.assertEqual(report_record["old_daili_jg"], old_agency)
                if ack_payload is not None:
                    self.assertEqual(
                        report_record["official_daili_jg"],
                        ack_payload["daili_jg"],
                    )

    def test_json_and_csv_reports_are_atomically_replaced(self):
        records = [
            collect_agency.classify_agency_ack(
                "2024110065970",
                "旧机构",
                agency_ack("新机构", "updated"),
            )
        ]
        with tempfile.TemporaryDirectory() as temporary_directory:
            json_path = Path(temporary_directory) / "agency.json"
            csv_path = Path(temporary_directory) / "agency.csv"
            with (
                patch.object(
                    collect_agency,
                    "AGENCY_VERIFICATION_REPORT_JSON_FILE",
                    json_path,
                ),
                patch.object(
                    collect_agency,
                    "AGENCY_VERIFICATION_REPORT_CSV_FILE",
                    csv_path,
                ),
            ):
                collect_agency.write_verification_reports(records, target_count=1)

            json_report = json.loads(json_path.read_text(encoding="utf-8"))
            with csv_path.open(encoding="utf-8-sig", newline="") as csv_stream:
                csv_records = list(csv.DictReader(csv_stream))

            self.assertEqual(json_report["target_count"], 1)
            self.assertEqual(json_report["completed_count"], 1)
            self.assertEqual(json_report["counts"]["changed"], 1)
            self.assertEqual(json_report["records"], records)
            self.assertEqual(csv_records[0]["old_daili_jg"], "旧机构")
            self.assertEqual(csv_records[0]["official_daili_jg"], "新机构")
            self.assertFalse(Path(f"{json_path}.tmp").exists())
            self.assertFalse(Path(f"{csv_path}.tmp").exists())


class TestAgencyCollectionLoop(unittest.TestCase):
    @patch.object(collect_agency, "FWXX_ANTI_CRAWL_BATCH_SIZE", 99)
    @patch("collect_agency.write_verification_reports")
    @patch("collect_agency.countdown")
    @patch("collect_agency.collect_one_agency")
    @patch("collect_agency.PatentsDB")
    @patch("collect_agency.CoordinateService")
    @patch("collect_agency.BrowserService")
    def test_old_agency_is_read_before_each_detail_and_only_detail_link_is_loaded(
        self,
        browser_service,
        coordinate_service,
        db_class,
        collect_one_agency,
        _countdown,
        write_reports,
    ):
        events = []
        driver = browser_service.launch_and_login.return_value
        coordinate_service.load_or_record_search_coordinates.return_value = (
            1,
            2,
            3,
            4,
        )
        coordinate_service.load_or_record_detail_link_coordinates.return_value = (5, 6)
        db = db_class.return_value

        old_records = {
            "2024110065970": {"daili_jg": "旧机构"},
            "202111504942X": None,
        }

        def read_old_record(application_no):
            events.append(("read", application_no))
            return old_records[application_no]

        def collect_detail(**collection_arguments):
            application_no = collection_arguments["application_no"]
            events.append(("collect", application_no))
            return agency_ack("新机构", "updated")

        db.get_record.side_effect = read_old_record
        collect_one_agency.side_effect = collect_detail
        arguments = Namespace(
            input=None,
            app="2024110065970,202111504942X",
            test=None,
            url="https://example.invalid",
        )

        records = collect_agency._run_agency_collection(arguments)

        self.assertEqual(
            events,
            [
                ("read", "2024110065970"),
                ("collect", "2024110065970"),
                ("read", "202111504942X"),
                ("collect", "202111504942X"),
            ],
        )
        self.assertEqual(
            [record["classification"] for record in records],
            ["changed", "first_collected"],
        )
        coordinate_service.load_or_record_search_coordinates.assert_called_once_with()
        coordinate_service.load_or_record_detail_link_coordinates.assert_called_once_with()
        coordinate_service.load_or_record_fwxx_coordinates.assert_not_called()
        coordinate_service.load_or_record_fee_menu_coordinates.assert_not_called()
        db.update_fields.assert_not_called()
        self.assertEqual(write_reports.call_count, 3)
        driver.quit.assert_called_once_with()

    @patch("collect_agency._run_agency_collection")
    @patch("collect_agency.reserve_detail_collection_desktop")
    def test_entrypoint_holds_shared_desktop_lock_for_whole_collection(
        self,
        reserve_desktop,
        run_collection,
    ):
        reservation = MagicMock()
        reserve_desktop.return_value = reservation
        arguments = Namespace()

        collect_agency.run_agency_collection(arguments)

        reserve_desktop.assert_called_once_with("代理机构官方复核")
        reservation.__enter__.assert_called_once_with()
        run_collection.assert_called_once_with(arguments)
        reservation.__exit__.assert_called_once()


if __name__ == "__main__":
    unittest.main()
