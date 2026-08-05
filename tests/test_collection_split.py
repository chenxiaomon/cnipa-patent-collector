#!/usr/bin/env python
# -*- coding: utf-8 -*-

import unittest
from unittest.mock import MagicMock, call, patch

import collect_fwxx


class TestFwxxCollectionBoundaries(unittest.TestCase):
    @patch("collect_fwxx.PatentsDB")
    def test_automatic_targets_only_include_missing_fwxx(self, db_class):
        db = db_class.return_value
        db.get_summary.return_value = {"rejection": 3564}
        db.fwxx_uncollected_app_nos.return_value = ["A", "B"]

        self.assertEqual(collect_fwxx.load_target_applications(), ["A", "B"])

        db.fwxx_uncollected_app_nos.assert_called_once_with()

    @patch("collect_fwxx.PatentsDB")
    def test_standalone_resume_uses_fwxx_completion(self, db_class):
        db = db_class.return_value
        db.fwxx_collected_app_nos.return_value = {"A"}

        self.assertEqual(collect_fwxx._load_standalone_collected(), {"A"})

        db.fwxx_collected_app_nos.assert_called_once_with()

    @patch("collect_fwxx.time.sleep")
    @patch("collect_fwxx.pyautogui.hotkey")
    @patch("collect_fwxx.poll_cache_for_key")
    @patch("collect_fwxx.clear_cache_key")
    @patch("collect_fwxx.InputService")
    @patch("collect_fwxx.is_browser_alive", return_value=True)
    @patch("collect_fwxx.CoordinateService")
    def test_one_application_only_touches_fwxx_menu_and_cache(
        self,
        _coordinate_service,
        _browser_alive,
        input_service,
        clear_cache,
        poll_cache,
        _hotkey,
        _sleep,
    ):
        driver = MagicMock()
        driver.page_source = ""
        driver.window_handles = ["search"]
        _coordinate_service.load_or_record_fee_menu_coordinates.return_value = (9, 10)

        def reveal_detail_tab(*_args, **_kwargs):
            if len(driver.window_handles) == 1:
                driver.window_handles.append("detail")

        input_service.move_and_click.side_effect = reveal_detail_tab
        poll_cache.return_value = {"fwxx_list": []}

        collected = collect_fwxx.collect_one_fwxx(
            driver,
            "A",
            input_x=1,
            input_y=2,
            button_x=3,
            button_y=4,
            link_x=5,
            link_y=6,
            fwxx_menu_x=7,
            fwxx_menu_y=8,
        )

        self.assertEqual(collected, {"fwxx_list": []})
        clear_cache.assert_called_once_with(collect_fwxx.PATENT_FWXX_CACHE_FILE, "A")
        poll_cache.assert_called_once_with(
            collect_fwxx.PATENT_FWXX_CACHE_FILE,
            "A",
            max_wait=collect_fwxx.FWXX_CACHE_POLL_TIMEOUT,
        )
        self.assertEqual(
            input_service.move_and_click.call_args_list,
            [
                call(5, 6, post_click_wait=collect_fwxx.FWXX_DETAIL_CLICK_WAIT),
                call(7, 8, post_click_wait=collect_fwxx.FWXX_MENU_CLICK_WAIT),
            ],
        )

    @patch("collect_fwxx.poll_cache_for_key")
    @patch("collect_fwxx.clear_cache_key", side_effect=OSError("denied"))
    @patch("collect_fwxx.InputService")
    @patch("collect_fwxx.is_browser_alive", return_value=True)
    def test_cache_cleanup_failure_stops_before_search(
        self,
        _browser_alive,
        input_service,
        _clear_cache,
        poll_cache,
    ):
        driver = MagicMock()
        driver.window_handles = ["search"]

        collected = collect_fwxx.collect_one_fwxx(
            driver,
            "A",
            input_x=1,
            input_y=2,
            button_x=3,
            button_y=4,
            link_x=5,
            link_y=6,
            fwxx_menu_x=7,
            fwxx_menu_y=8,
        )

        self.assertIsNone(collected)
        input_service.type_in_search.assert_not_called()
        input_service.move_and_click.assert_not_called()
        poll_cache.assert_not_called()

    @patch("collect_fwxx.PatentsDB")
    def test_fwxx_persistence_ignores_fee_fields(self, db_class):
        db = db_class.return_value
        db.get_record.return_value = {"application_no": "A"}

        persisted = collect_fwxx.persist_fwxx_fields(
            "A",
            {
                "fwxx_list": [],
                "bhsjtzs_data": {"name": "驳回决定"},
                "payable_fee_records": [{"amount": "100"}],
                "fee_snapshot_at": "2026-07-27T00:00:00Z",
            },
        )

        self.assertTrue(persisted)
        written_fields = db.update_fields.call_args.args[1]
        self.assertEqual(written_fields["fwxx_list"], [])
        self.assertEqual(written_fields["bhsjtzs_data"], {"name": "驳回决定"})
        self.assertIn("timestamp", written_fields)
        self.assertNotIn("payable_fee_records", written_fields)
        self.assertNotIn("fee_snapshot_at", written_fields)


if __name__ == "__main__":
    unittest.main()
