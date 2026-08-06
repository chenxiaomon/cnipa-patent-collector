#!/usr/bin/env python
# -*- coding: utf-8 -*-

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import agency_attempt
import collect_agency
import patent_mitm_scraper
from atomic_write import write_json_atomic


APPLICATION_NO = "2026104796018"
SQXX_RESPONSE = {
    "code": 200,
    "data": {
        "zhuluxmxx": {"zhuluxmxx": {"zhuanlisqh": APPLICATION_NO}},
        "dailijg": {
            "dailijgList": [
                {"dailijgdm": "浙江侨悦专利代理有限公司", "diyidlrxm": "陈泽元"}
            ]
        },
    },
}


def make_flow(response_payload=SQXX_RESPONSE):
    flow = MagicMock()
    flow.metadata = {}
    flow.request.pretty_url = (
        "https://cponline.cnipa.gov.cn/api/view/gn/sqxx?token=abc"
    )
    flow.response.content = json.dumps(
        response_payload,
        ensure_ascii=False,
    ).encode("utf-8")
    return flow


def attempt_marker(attempt_id: str, application_no: str = APPLICATION_NO) -> dict:
    return {
        "application_no": application_no,
        "attempt_id": attempt_id,
        "started_at": "2026-08-06T01:02:00Z",
    }


def acknowledgement(attempt_id: str) -> dict:
    return {
        "attempt_id": attempt_id,
        "captured_at": "2026-08-06T01:02:03Z",
        "daili_jg": "浙江侨悦专利代理有限公司",
        "daili_r": "陈泽元",
        "persistence_status": "updated",
    }


class TestAgencyAttemptMarker(unittest.TestCase):
    def test_conditional_clear_cannot_remove_a_newer_attempt(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            marker_path = Path(temporary_directory) / "current_agency_attempt.json"
            with patch.object(
                agency_attempt,
                "AGENCY_ATTEMPT_MARKER_FILE",
                marker_path,
            ):
                first_attempt = agency_attempt.begin_agency_attempt(APPLICATION_NO)
                second_attempt = agency_attempt.begin_agency_attempt(APPLICATION_NO)

                self.assertFalse(
                    agency_attempt.clear_matching_agency_attempt(
                        first_attempt["attempt_id"]
                    )
                )
                self.assertEqual(
                    agency_attempt.read_agency_attempt_marker(),
                    second_attempt,
                )
                self.assertTrue(
                    agency_attempt.clear_matching_agency_attempt(
                        second_attempt["attempt_id"]
                    )
                )
                self.assertIsNone(agency_attempt.read_agency_attempt_marker())


class TestMITMAgencyAttemptBinding(unittest.TestCase):
    def setUp(self):
        logger_patcher = patch("patent_mitm_scraper.DetectionLogger")
        self.logger_class = logger_patcher.start()
        self.addCleanup(logger_patcher.stop)
        self.database = self.logger_class.return_value._db

        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.cache_path = (
            Path(self.temporary_directory.name) / "patent_agency_cache.json"
        )
        cache_patcher = patch.object(
            patent_mitm_scraper,
            "PATENT_AGENCY_CACHE_FILE",
            self.cache_path,
        )
        cache_patcher.start()
        self.addCleanup(cache_patcher.stop)

        self.scraper = patent_mitm_scraper.PatentMITMScraper()

    def test_sqxx_request_binds_the_current_marker_snapshot(self):
        current_attempt = attempt_marker("attempt-current")
        flow = make_flow()

        with patch(
            "patent_mitm_scraper.read_agency_attempt_marker",
            return_value=current_attempt,
        ):
            self.scraper.request(flow)

        self.assertEqual(
            flow.metadata[patent_mitm_scraper._AGENCY_ATTEMPT_METADATA_KEY],
            current_attempt,
        )

    def test_matching_response_writes_exact_attempt_id(self):
        current_attempt = attempt_marker("attempt-current")
        flow = make_flow()
        flow.metadata[patent_mitm_scraper._AGENCY_ATTEMPT_METADATA_KEY] = (
            current_attempt
        )

        with patch(
            "patent_mitm_scraper.read_agency_attempt_marker",
            return_value=current_attempt,
        ):
            self.scraper._process_sqxx_response(flow)

        cached_ack = json.loads(self.cache_path.read_text(encoding="utf-8"))[
            APPLICATION_NO
        ]
        self.assertEqual(cached_ack["attempt_id"], "attempt-current")

    def test_response_application_number_mismatch_writes_no_ack(self):
        bound_attempt = attempt_marker("attempt-current", "2024110065970")
        flow = make_flow()
        flow.metadata[patent_mitm_scraper._AGENCY_ATTEMPT_METADATA_KEY] = (
            bound_attempt
        )

        with patch(
            "patent_mitm_scraper.read_agency_attempt_marker",
            return_value=bound_attempt,
        ):
            self.scraper._process_sqxx_response(flow)

        self.database.update_fields.assert_called_once()
        self.assertFalse(self.cache_path.exists())

    def test_late_old_response_cannot_overwrite_current_ack(self):
        old_attempt = attempt_marker("attempt-old")
        current_attempt = attempt_marker("attempt-current")
        write_json_atomic(
            self.cache_path,
            {APPLICATION_NO: acknowledgement("attempt-current")},
        )
        flow = make_flow()
        flow.metadata[patent_mitm_scraper._AGENCY_ATTEMPT_METADATA_KEY] = old_attempt

        with patch(
            "patent_mitm_scraper.read_agency_attempt_marker",
            return_value=current_attempt,
        ):
            self.scraper._process_sqxx_response(flow)

        cached_ack = json.loads(self.cache_path.read_text(encoding="utf-8"))[
            APPLICATION_NO
        ]
        self.assertEqual(cached_ack["attempt_id"], "attempt-current")


class TestCollectorAttemptTiming(unittest.TestCase):
    @patch("collect_agency.time.sleep")
    @patch("collect_agency.pyautogui.hotkey")
    @patch("collect_agency.clear_matching_agency_attempt")
    @patch("collect_agency.begin_agency_attempt")
    @patch("collect_agency.InputService")
    @patch("collect_agency.is_browser_alive", return_value=True)
    def test_ack_written_after_click_but_before_poll_is_accepted(
        self,
        _browser_alive,
        input_service,
        begin_attempt,
        _clear_attempt,
        close_tab,
        _sleep,
    ):
        current_attempt = attempt_marker("attempt-current", "2024110065970")
        begin_attempt.return_value = current_attempt
        driver = MagicMock()
        driver.window_handles = ["search"]
        driver.page_source = "搜索结果 CN202411006597.0"

        with tempfile.TemporaryDirectory() as temporary_directory:
            cache_path = Path(temporary_directory) / "patent_agency_cache.json"

            def write_ack_before_poll(*_args, **_kwargs):
                driver.window_handles.append("detail")
                write_json_atomic(
                    cache_path,
                    {"2024110065970": acknowledgement("attempt-current")},
                )

            def close_detail_tab(*_args, **_kwargs):
                driver.window_handles.remove("detail")

            input_service.move_and_click.side_effect = write_ack_before_poll
            close_tab.side_effect = close_detail_tab
            with patch.object(
                collect_agency,
                "PATENT_AGENCY_CACHE_FILE",
                cache_path,
            ):
                collected_ack = collect_agency.collect_one_agency(
                    driver,
                    "2024110065970",
                    1,
                    2,
                    3,
                    4,
                    5,
                    6,
                )

        self.assertEqual(collected_ack["attempt_id"], "attempt-current")
        self.assertFalse(
            collect_agency._is_agency_ack(
                acknowledgement("attempt-old"),
                expected_attempt_id="attempt-current",
            )
        )
