#!/usr/bin/env python
# -*- coding: utf-8 -*-

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import collect_fees
import collect_fwxx
import detail_attempt
import patent_mitm_scraper
from atomic_write import write_json_atomic
from cache_utils import read_json_cache
from tests.test_mitm_scraper import FWXX_RESPONSE, FYXX_RESPONSE, _json_body, _make_flow


APPLICATION_NO = "2026102909420"
OTHER_APPLICATION_NO = "2024100659780"


def sqxx_flow(application_no):
    return _make_flow(
        "https://cponline.cnipa.gov.cn/api/view/gn/sqxx?token=abc",
        body=_json_body({
            "code": 200,
            "data": {
                "zhuluxmxx": {"zhuluxmxx": {"zhuanlisqh": application_no}},
            },
        }),
    )


class TestDetailResponseTargetBinding(unittest.TestCase):
    def setUp(self):
        temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        self.temporary_path = Path(temporary_directory.name)
        for module, constant, filename in (
            (detail_attempt, "MARKER_FILE", "current_detail.json"),
            (detail_attempt, "PATENT_DETAIL_IDENTITY_CACHE_FILE", "identity.json"),
            (patent_mitm_scraper, "PATENT_FWXX_CACHE_FILE", "fwxx.json"),
            (patent_mitm_scraper, "PATENT_FEE_CACHE_FILE", "fees.json"),
            (collect_fwxx, "PATENT_FWXX_CACHE_FILE", "fwxx.json"),
            (collect_fees, "PATENT_FEE_CACHE_FILE", "fees.json"),
        ):
            patcher = patch.object(module, constant, self.temporary_path / filename)
            patcher.start()
            self.addCleanup(patcher.stop)
        for target, replacement in (
            ("patent_mitm_scraper.DetectionLogger", MagicMock()),
            ("patent_mitm_scraper.read_agency_attempt_marker", lambda: None),
        ):
            patcher = patch(target, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.scraper = patent_mitm_scraper.PatentMITMScraper()

    def confirm_identity(self, application_no):
        identity_flow = sqxx_flow(application_no)
        self.scraper.request(identity_flow)
        self.scraper._process_sqxx_response(identity_flow)

    def confirmed_application_no(self):
        flow = _make_flow("https://cponline.cnipa.gov.cn/api/view/gn/fyxx?token=abc")
        self.scraper.request(flow)
        return flow.metadata.get("cnipa_detail_target_application_no")

    def test_sqxx_identity_does_not_require_agency_fields(self):
        attempt = detail_attempt.begin_detail_attempt(APPLICATION_NO)

        self.confirm_identity(APPLICATION_NO)

        detail_attempt.wait_for_detail_identity(attempt)
        self.assertEqual(self.confirmed_application_no(), APPLICATION_NO)

    def test_wrong_official_identity_cannot_authorize_fee_or_fwxx_requests(self):
        attempt = detail_attempt.begin_detail_attempt(APPLICATION_NO)
        self.confirm_identity(OTHER_APPLICATION_NO)

        with self.assertRaisesRegex(detail_attempt.DetailCollectionFatalError, "申请号不匹配"):
            detail_attempt.wait_for_detail_identity(attempt)
        for endpoint, payload in (("fwxx", FWXX_RESPONSE), ("fyxx", FYXX_RESPONSE)):
            with self.subTest(endpoint=endpoint):
                flow = _make_flow(
                    f"https://cponline.cnipa.gov.cn/api/view/gn/{endpoint}?token=abc",
                    body=_json_body(payload),
                )
                self.scraper.request(flow)
                self.scraper.response(flow)
                self.assertNotIn("cnipa_detail_target_application_no", flow.metadata)
        self.assertFalse((self.temporary_path / "fwxx.json").exists())
        self.assertFalse((self.temporary_path / "fees.json").exists())

    def test_early_detail_responses_publish_only_after_identity_confirmation(self):
        attempt = detail_attempt.begin_detail_attempt(APPLICATION_NO)
        flows = [
            _make_flow(
                f"https://cponline.cnipa.gov.cn/api/view/gn/{endpoint}?token=abc",
                body=_json_body(payload),
            )
            for endpoint, payload in (("fwxx", FWXX_RESPONSE), ("fyxx", FYXX_RESPONSE))
        ]
        for flow in flows:
            self.scraper.request(flow)
            self.scraper.response(flow)

        self.assertFalse((self.temporary_path / "fwxx.json").exists())
        self.assertFalse((self.temporary_path / "fees.json").exists())

        self.confirm_identity(APPLICATION_NO)

        for filename in ("fwxx.json", "fees.json"):
            cached_fields = read_json_cache(str(self.temporary_path / filename))
            self.assertEqual(cached_fields[APPLICATION_NO]["detail_attempt_id"], attempt["attempt_id"])
        self.assertEqual(self.scraper._pending_detail_fields, {})

    def test_sqxx_and_detail_requests_and_responses_can_arrive_in_either_order(self):
        for request_order, response_order in (
            (("sqxx", "detail"), ("sqxx", "detail")),
            (("sqxx", "detail"), ("detail", "sqxx")),
            (("detail", "sqxx"), ("sqxx", "detail")),
            (("detail", "sqxx"), ("detail", "sqxx")),
        ):
            with self.subTest(request_order=request_order, response_order=response_order):
                attempt = detail_attempt.begin_detail_attempt(APPLICATION_NO)
                flows = {
                    "sqxx": sqxx_flow(APPLICATION_NO),
                    "detail": _make_flow(
                        "https://cponline.cnipa.gov.cn/api/view/gn/fyxx?token=abc",
                        body=_json_body(FYXX_RESPONSE),
                    ),
                }
                for endpoint in request_order:
                    self.scraper.request(flows[endpoint])
                for endpoint in response_order:
                    self.scraper.response(flows[endpoint])

                cached_fields = read_json_cache(str(self.temporary_path / "fees.json"))
                self.assertEqual(cached_fields[APPLICATION_NO]["detail_attempt_id"], attempt["attempt_id"])

    def test_wrong_identity_discards_early_responses_and_later_responses(self):
        detail_attempt.begin_detail_attempt(APPLICATION_NO)
        flow = _make_flow(
            "https://cponline.cnipa.gov.cn/api/view/gn/fyxx?token=abc",
            body=_json_body(FYXX_RESPONSE),
        )
        self.scraper.request(flow)
        self.scraper.response(flow)
        self.assertEqual(len(self.scraper._pending_detail_fields), 1)

        self.confirm_identity(OTHER_APPLICATION_NO)
        self.scraper.response(flow)

        self.assertEqual(self.scraper._pending_detail_fields, {})
        self.assertFalse((self.temporary_path / "fees.json").exists())

    def test_new_attempt_discards_previous_pending_responses(self):
        detail_attempt.begin_detail_attempt(APPLICATION_NO)
        old_identity_flow = sqxx_flow(APPLICATION_NO)
        old_fee_flow = _make_flow(
            "https://cponline.cnipa.gov.cn/api/view/gn/fyxx?token=abc",
            body=_json_body(FYXX_RESPONSE),
        )
        self.scraper.request(old_identity_flow)
        self.scraper.request(old_fee_flow)
        self.scraper.response(old_fee_flow)

        detail_attempt.begin_detail_attempt(APPLICATION_NO)
        self.confirm_identity(APPLICATION_NO)
        self.scraper.response(old_identity_flow)

        self.assertEqual(self.scraper._pending_detail_fields, {})
        self.assertFalse((self.temporary_path / "fees.json").exists())

    def test_old_response_cannot_overwrite_new_snapshot_for_same_application(self):
        detail_attempt.begin_detail_attempt(APPLICATION_NO)
        self.confirm_identity(APPLICATION_NO)
        old_fee_flow = _make_flow(
            "https://cponline.cnipa.gov.cn/api/view/gn/fyxx?token=abc",
            body=_json_body(FYXX_RESPONSE),
        )
        self.scraper.request(old_fee_flow)
        current_attempt = detail_attempt.begin_detail_attempt(APPLICATION_NO)
        self.confirm_identity(APPLICATION_NO)
        current_fee_flow = _make_flow(
            "https://cponline.cnipa.gov.cn/api/view/gn/fyxx?token=abc",
            body=_json_body({"code": 200, "data": {"yingjiaofei": {"svYingjfList": []}}}),
        )
        self.scraper.request(current_fee_flow)
        self.scraper.response(current_fee_flow)

        self.scraper.response(old_fee_flow)

        cached_fields = read_json_cache(str(self.temporary_path / "fees.json"))[APPLICATION_NO]
        self.assertEqual(cached_fields['detail_attempt_id'], current_attempt['attempt_id'])
        self.assertEqual(cached_fields['payable_fee_records'], [])

    def test_pending_responses_keep_only_latest_payload_per_endpoint(self):
        detail_attempt.begin_detail_attempt(APPLICATION_NO)
        for amount in range(4):
            flow = _make_flow(
                "https://cponline.cnipa.gov.cn/api/view/gn/fyxx?token=abc",
                body=_json_body({
                    "code": 200,
                    "data": {"yingjiaofei": {"svYingjfList": [{"yingjiaoje": amount}]}},
                }),
            )
            self.scraper.request(flow)
            self.scraper.response(flow)
        self.assertEqual(len(self.scraper._pending_detail_fields), 1)

        self.confirm_identity(APPLICATION_NO)

        cached_fields = read_json_cache(str(self.temporary_path / "fees.json"))[APPLICATION_NO]
        self.assertEqual(cached_fields['payable_fee_records'], [{"yingjiaoje": 3}])

    def test_delayed_detail_responses_cannot_write_into_a_later_attempt(self):
        detail_attempt.begin_detail_attempt(APPLICATION_NO)
        self.confirm_identity(APPLICATION_NO)
        flows = []
        for endpoint, payload in (("fwxx", FWXX_RESPONSE), ("fyxx", FYXX_RESPONSE)):
            flow = _make_flow(
                f"https://cponline.cnipa.gov.cn/api/view/gn/{endpoint}?token=abc",
                body=_json_body(payload),
            )
            self.scraper.request(flow)
            flows.append(flow)
        detail_attempt.begin_detail_attempt(OTHER_APPLICATION_NO)
        self.confirm_identity(OTHER_APPLICATION_NO)

        for flow in flows:
            self.scraper.response(flow)

        self.assertFalse((self.temporary_path / "fwxx.json").exists())
        self.assertFalse((self.temporary_path / "fees.json").exists())

    def test_late_sqxx_response_cannot_confirm_a_new_attempt_for_same_patent(self):
        detail_attempt.begin_detail_attempt(APPLICATION_NO)
        old_identity_flow = sqxx_flow(APPLICATION_NO)
        self.scraper.request(old_identity_flow)
        new_attempt = detail_attempt.begin_detail_attempt(APPLICATION_NO)

        self.scraper._process_sqxx_response(old_identity_flow)

        self.assertIsNone(self.confirmed_application_no())
        self.assertNotIn(
            new_attempt["attempt_id"],
            read_json_cache(str(detail_attempt.PATENT_DETAIL_IDENTITY_CACHE_FILE)),
        )

    def test_new_attempt_rejects_previous_confirmation_and_cached_fields(self):
        first_attempt = detail_attempt.begin_detail_attempt(APPLICATION_NO)
        self.confirm_identity(APPLICATION_NO)
        new_attempt = detail_attempt.begin_detail_attempt(APPLICATION_NO)

        detail_attempt.clear_matching_detail_attempt(first_attempt["attempt_id"])

        self.assertEqual(detail_attempt.read_detail_attempt_marker(), new_attempt)
        self.assertIsNone(self.confirmed_application_no())
        self.assertIn(first_attempt['attempt_id'], read_json_cache(
            str(detail_attempt.PATENT_DETAIL_IDENTITY_CACHE_FILE)
        ))
        self.assertFalse(detail_attempt.matches_detail_attempt(
            {"detail_attempt_id": first_attempt["attempt_id"], "fwxx_list": []},
            new_attempt["attempt_id"],
        ))

    def test_missing_sqxx_confirmation_stops_collection(self):
        attempt = detail_attempt.begin_detail_attempt(APPLICATION_NO)
        with patch.object(detail_attempt, "FWXX_CACHE_POLL_TIMEOUT", 0):
            with self.assertRaisesRegex(detail_attempt.DetailCollectionFatalError, "未收到"):
                detail_attempt.wait_for_detail_identity(attempt)

    def test_response_without_request_attempt_cannot_confirm_identity(self):
        detail_attempt.begin_detail_attempt(APPLICATION_NO)

        self.scraper._process_sqxx_response(sqxx_flow(APPLICATION_NO))

        self.assertIsNone(self.confirmed_application_no())

    def test_expired_or_future_attempt_cannot_reuse_saved_confirmation(self):
        attempt = detail_attempt.begin_detail_attempt(APPLICATION_NO)
        self.confirm_identity(APPLICATION_NO)
        for offset in (timedelta(minutes=-6), timedelta(minutes=1)):
            with self.subTest(offset=offset):
                changed_attempt = dict(attempt)
                changed_attempt['started_at'] = (
                    datetime.now(timezone.utc) + offset
                ).isoformat().replace('+00:00', 'Z')
                write_json_atomic(detail_attempt.MARKER_FILE, changed_attempt)

                self.assertIsNone(detail_attempt.read_detail_attempt_marker())
                self.assertIsNone(self.confirmed_application_no())

    def test_legacy_application_number_marker_cannot_confirm_identity(self):
        write_json_atomic(detail_attempt.MARKER_FILE, {
            'application_no': APPLICATION_NO,
            'written_at': datetime.now(timezone.utc).isoformat(),
        })

        self.assertIsNone(detail_attempt.read_detail_attempt_marker())

    def test_collectors_reject_wrong_case_even_when_search_input_contains_target(self):
        for collector_module, collect_one, coordinates in (
            (collect_fees, collect_fees.collect_one_fee, (1, 2, 3, 4, 5, 6)),
            (collect_fwxx, collect_fwxx.collect_one_fwxx, (1, 2, 3, 4, 5, 6, 7, 8)),
        ):
            with self.subTest(collector=collector_module.__name__):
                driver = MagicMock()
                driver.window_handles = ["search"]
                driver.page_source = (
                    f'<input value="{APPLICATION_NO}"><table><tr>'
                    f'<td>{OTHER_APPLICATION_NO}</td></tr></table>'
                )

                def open_wrong_detail(*_args, **_kwargs):
                    driver.window_handles.append("wrong-detail")
                    self.confirm_identity(OTHER_APPLICATION_NO)

                def close_wrong_detail():
                    driver.window_handles.remove("wrong-detail")

                driver.close.side_effect = close_wrong_detail
                with patch.object(collector_module, "is_browser_alive", return_value=True), patch.object(
                    collector_module, "InputService"
                ) as input_service, patch.object(collector_module.time, "sleep"), patch.object(
                    collector_module, "poll_cache_for_key"
                ) as poll_fields:
                    input_service.move_and_click.side_effect = open_wrong_detail
                    with self.assertRaisesRegex(detail_attempt.DetailCollectionFatalError, "申请号不匹配"):
                        collect_one(driver, APPLICATION_NO, *coordinates)

                self.assertEqual(input_service.move_and_click.call_count, 1)
                poll_fields.assert_not_called()
                self.assertEqual(driver.window_handles, ["search"])
                self.assertIsNone(detail_attempt.read_detail_attempt_marker())


if __name__ == "__main__":
    unittest.main()
