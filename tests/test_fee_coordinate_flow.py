#!/usr/bin/env python
# -*- coding: utf-8 -*-

import io
from argparse import Namespace
from contextlib import redirect_stderr

import unittest
from unittest.mock import MagicMock, call, patch

import collect_fees
import coordinate_service

import desktop_collection_lock

class TestFeeCoordinateFlow(unittest.TestCase):
    @patch("collect_fees.time.sleep")
    @patch("collect_fees.pyautogui.hotkey")
    @patch("collect_fees.poll_cache_for_key")
    @patch("collect_fees.clear_cache_key")
    @patch("collect_fees.InputService")
    @patch("collect_fees.CoordinateService")
    @patch("collect_fees.is_browser_alive", return_value=True)
    def test_fee_menu_coordinate_is_loaded_after_detail_page_opens(
        self,
        _browser_alive,
        coordinate_service,
        input_service,
        _clear_cache,
        poll_cache,
        _hotkey,
        _sleep,
    ):
        driver = MagicMock()
        driver.page_source = ""
        driver.window_handles = ["search"]

        def reveal_detail_tab(*_args, **_kwargs):
            if len(driver.window_handles) == 1:
                driver.window_handles.append("detail")

        input_service.move_and_click.side_effect = reveal_detail_tab
        def load_fee_menu_coordinates():
            driver.switch_to.window.assert_called_with("detail")
            return 7, 8

        coordinate_service.load_or_record_fee_menu_coordinates.side_effect = (
            load_fee_menu_coordinates
        )
        poll_cache.return_value = {
            "detail_attempt_id": "attempt-current",
            "payable_fee_records": [],
            "paid_fee_records": [],
            "fee_receipt_dispatch_records": [],
        }
        driver.close.side_effect = lambda: driver.window_handles.remove("detail")

        with patch("collect_fees.begin_detail_attempt", return_value={
            "application_no": "A", "attempt_id": "attempt-current",
        }), patch("collect_fees.wait_for_detail_identity"), patch(
            "collect_fees.clear_matching_detail_attempt"
        ):
            collect_fees.collect_one_fee(
                driver,
                "A",
                input_x=1,
                input_y=2,
                button_x=3,
                button_y=4,
                link_x=5,
                link_y=6,
            )

        coordinate_service.load_or_record_fee_menu_coordinates.assert_called_once_with()
        self.assertEqual(
            input_service.move_and_click.call_args_list,
            [
                call(5, 6, post_click_wait=collect_fees.FWXX_DETAIL_CLICK_WAIT),
                call(7, 8, post_click_wait=collect_fees.FWXX_MENU_CLICK_WAIT),
            ],
        )

    @patch("collect_fees._run_fee_collection")
    @patch("collect_fees.reserve_detail_collection_desktop")
    def test_fee_entrypoint_reserves_desktop_for_entire_collection(
        self,
        reserve_desktop,
        run_collection,
    ):
        reservation = MagicMock()
        reserve_desktop.return_value = reservation
        arguments = Namespace()

        collect_fees.run_fee_collection(arguments)

        reserve_desktop.assert_called_once_with(
            "\u8d39\u7528\u4fe1\u606f\u91c7\u96c6"
        )
        reservation.__enter__.assert_called_once_with()
        run_collection.assert_called_once_with(arguments)
        reservation.__exit__.assert_called_once()

    @patch("collect_fees.PatentsDB")
    @patch("collect_fees.DetectionLogger")
    @patch("collect_fees.is_browser_alive", return_value=True)
    @patch("collect_fees.collect_one_fee", return_value=None)
    @patch("collect_fees.countdown")
    @patch("collect_fees.CoordinateService")
    @patch("collect_fees.BrowserService")
    @patch("collect_fees.load_fee_dataset_targets", return_value=["A"])
    def test_search_page_only_loads_search_and_detail_link_coordinates(
        self,
        _load_targets,
        browser_service,
        coordinate_service,
        _countdown,
        collect_one_fee,
        _browser_alive,
        _logger,
        _db_class,
    ):
        driver = browser_service.launch_and_login.return_value
        coordinate_service.load_or_record_search_coordinates.return_value = (
            1,
            2,
            3,
            4,
        )
        coordinate_service.load_or_record_detail_link_coordinates.return_value = (
            5,
            6,
        )
        arguments = Namespace(
            test=None,
            input=None,
            app=None,
            force=False,
            url="https://example.invalid",
        )

        collect_fees._run_fee_collection(arguments)

        coordinate_service.load_or_record_search_coordinates.assert_called_once_with()
        coordinate_service.load_or_record_detail_link_coordinates.assert_called_once_with()
        coordinate_service.load_or_record_fwxx_coordinates.assert_not_called()
        coordinate_service.load_or_record_fee_menu_coordinates.assert_not_called()
        collect_one_fee.assert_called_once_with(
            driver=driver,
            application_no="A",
            input_x=1,
            input_y=2,
            button_x=3,
            button_y=4,
            link_x=5,
            link_y=6,
        )

    @patch.object(collect_fees, "USE_MITM_PROXY", True)
    @patch("collect_fees.run_fee_collection")
    def test_cli_reports_desktop_contention_with_nonzero_exit(
        self,
        run_collection,
    ):
        run_collection.side_effect = (
            desktop_collection_lock.DetailCollectionDesktopBusyError(
                "desktop busy"
            )
        )
        standard_error = io.StringIO()

        with redirect_stderr(standard_error):
            exit_code = collect_fees.main([])

        self.assertEqual(exit_code, 2)
        self.assertIn("desktop busy", standard_error.getvalue())



    @patch("coordinate_service.CoordinateService._record_detail_link_coordinates")
    @patch("coordinate_service.json.load", return_value={"link_x": 5, "link_y": 6})
    @patch("builtins.open")
    @patch("coordinate_service.os.path.exists", return_value=True)
    def test_detail_link_loader_does_not_require_menu_coordinates(
        self,
        _path_exists,
        _open_config,
        _load_json,
        record_coordinates,
    ):
        self.assertEqual(
            coordinate_service.CoordinateService.load_or_record_detail_link_coordinates(),
            (5, 6),
        )

        record_coordinates.assert_not_called()

    @patch("coordinate_service.write_json_atomic")
    @patch(
        "coordinate_service.pyautogui.position",
        side_effect=[(5, 6), (7, 8)],
    )
    @patch("coordinate_service.CoordinateService._countdown")
    @patch(
        "coordinate_service.json.load",
        return_value={"fee_menu_x": 9, "fee_menu_y": 10},
    )
    @patch("builtins.open")
    @patch("coordinate_service.os.path.exists", return_value=True)
    def test_recording_fwxx_coordinates_preserves_fee_menu_coordinates(
        self,
        _path_exists,
        _open_config,
        _load_json,
        _countdown,
        _mouse_position,
        write_coordinates,
    ):
        recorded = (
            coordinate_service.CoordinateService.load_or_record_fwxx_coordinates()
        )

        self.assertEqual(recorded, (5, 6, 7, 8))
        saved_coordinates = write_coordinates.call_args.args[1]
        self.assertEqual(saved_coordinates["fee_menu_x"], 9)
        self.assertEqual(saved_coordinates["fee_menu_y"], 10)
        self.assertEqual(saved_coordinates["link_x"], 5)
        self.assertEqual(saved_coordinates["link_y"], 6)
        self.assertEqual(saved_coordinates["fwxx_menu_x"], 7)
        self.assertEqual(saved_coordinates["fwxx_menu_y"], 8)

if __name__ == "__main__":
    unittest.main()
