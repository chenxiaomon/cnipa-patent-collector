#!/usr/bin/env python
# -*- coding: utf-8 -*-

import unittest
from unittest.mock import MagicMock, call, patch

import collect_fees


class TestFeeCollectionBoundaries(unittest.TestCase):
    @patch("collect_fees.PatentsDB")
    def test_automatic_targets_only_include_missing_fee_details(self, db_class):
        db = db_class.return_value
        db.get_summary.return_value = {"rejection": 3564}
        db.fee_details_pending_app_nos.return_value = ["A", "B"]

        self.assertEqual(collect_fees.load_target_applications(), ["A", "B"])

        db.fee_details_pending_app_nos.assert_called_once_with()
        db.detail_enrichment_pending_app_nos.assert_not_called()

    @patch("collect_fees.PatentsDB")
    def test_standalone_resume_uses_fee_completion(self, db_class):
        db = db_class.return_value
        db.fee_details_completed_app_nos.return_value = {"A"}

        self.assertEqual(collect_fees._load_standalone_collected(), {"A"})

        db.fee_details_completed_app_nos.assert_called_once_with()
        db.detail_enrichment_completed_app_nos.assert_not_called()

    @patch("collect_fees.time.sleep")
    @patch("collect_fees.pyautogui.hotkey")
    @patch("collect_fees.poll_cache_for_key")
    @patch("collect_fees.clear_cache_key")
    @patch("collect_fees.InputService")
    @patch("collect_fees.CoordinateService")
    @patch("collect_fees.is_browser_alive", return_value=True)
    def test_one_application_only_touches_fee_menu_and_cache(
        self,
        _browser_alive,
        coordinate_service,
        input_service,
        clear_cache,
        poll_cache,
        _hotkey,
        _sleep,
    ):
        driver = MagicMock()
        driver.page_source = ""
        driver.window_handles = ["search"]
        coordinate_service.load_or_record_fee_menu_coordinates.return_value = (7, 8)

        def reveal_detail_tab(*_args, **_kwargs):
            if len(driver.window_handles) == 1:
                driver.window_handles.append("detail")

        input_service.move_and_click.side_effect = reveal_detail_tab
        fee_fields = {
            "payable_fee_records": [],
            "paid_fee_records": [],
            "fee_receipt_dispatch_records": [],
            "fee_snapshot_at": "2026-07-27T00:00:00Z",
        }
        poll_cache.return_value = fee_fields

        collected = collect_fees.collect_one_fee(
            driver,
            "A",
            input_x=1,
            input_y=2,
            button_x=3,
            button_y=4,
            link_x=5,
            link_y=6,
        )

        self.assertEqual(collected, fee_fields)
        clear_cache.assert_called_once_with(collect_fees.PATENT_FEE_CACHE_FILE, "A")
        poll_cache.assert_called_once_with(
            collect_fees.PATENT_FEE_CACHE_FILE,
            "A",
            max_wait=collect_fees.FWXX_CACHE_POLL_TIMEOUT,
        )
        self.assertEqual(
            input_service.move_and_click.call_args_list,
            [
                call(5, 6, post_click_wait=collect_fees.FWXX_DETAIL_CLICK_WAIT),
                call(7, 8, post_click_wait=collect_fees.FWXX_MENU_CLICK_WAIT),
            ],
        )

    @patch("collect_fees.poll_cache_for_key")
    @patch("collect_fees.clear_cache_key", side_effect=OSError("denied"))
    @patch("collect_fees.InputService")
    @patch("collect_fees.is_browser_alive", return_value=True)
    def test_cache_cleanup_failure_stops_before_search(
        self,
        _browser_alive,
        input_service,
        _clear_cache,
        poll_cache,
    ):
        driver = MagicMock()
        driver.window_handles = ["search"]

        collected = collect_fees.collect_one_fee(
            driver,
            "A",
            input_x=1,
            input_y=2,
            button_x=3,
            button_y=4,
            link_x=5,
            link_y=6,
        )

        self.assertIsNone(collected)
        input_service.type_in_search.assert_not_called()
        input_service.move_and_click.assert_not_called()
        poll_cache.assert_not_called()

    @patch("collect_fees.PatentsDB")
    def test_fee_persistence_ignores_fwxx_fields_and_status_timestamp(self, db_class):
        db = db_class.return_value
        db.get_record.return_value = {"application_no": "A"}

        persisted = collect_fees.persist_fee_fields(
            "A",
            {
                "fwxx_list": [{"name": "notice"}],
                "payable_fee_records": [],
                "late_fee_schedule_records": [],
                "paid_fee_records": [],
                "fee_receipt_dispatch_records": [],
                "fee_snapshot_at": "2026-07-27T00:00:00Z",
                "timestamp": "must-not-be-written",
            },
        )

        self.assertTrue(persisted)
        written_fields = db.update_fields.call_args.args[1]
        self.assertEqual(
            set(written_fields),
            {
                "payable_fee_records",
                "late_fee_schedule_records",
                "paid_fee_records",
                "fee_receipt_dispatch_records",
                "fee_snapshot_at",
            },
        )
        self.assertNotIn("fwxx_list", written_fields)
        self.assertNotIn("timestamp", written_fields)


if __name__ == "__main__":
    unittest.main()
