#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""example 配置的全 0 占位坐标不能被当成已录制的坐标。

data/config*.example.json 用 0 占位，直接拷成配置文件后，(0, 0) 会让 pyautogui
点击屏幕左上角；PYAUTOGUI_FAILSAFE 默认关闭，所以既不报错也永远搜不出结果。
"""

import unittest
from unittest.mock import patch

import coordinate_service


class TestPlaceholderCoordinatesTriggerRecording(unittest.TestCase):
    @patch("coordinate_service.CoordinateService._record_search_coordinates",
           return_value=(1, 2, 3, 4))
    @patch("coordinate_service.json.load", return_value={
        "input_x": 0, "input_y": 0, "button_x": 0, "button_y": 0,
        "last_updated": "YYYY-MM-DDTHH:MM:SS",
    })
    @patch("builtins.open")
    @patch("coordinate_service.os.path.exists", return_value=True)
    def test_placeholder_search_coordinates_are_rerecorded(
        self, _path_exists, _open_config, _load_json, record_coordinates,
    ):
        self.assertEqual(
            coordinate_service.CoordinateService.load_or_record_search_coordinates(),
            (1, 2, 3, 4),
        )
        record_coordinates.assert_called_once()

    @patch("coordinate_service.CoordinateService._record_detail_link_coordinates",
           return_value=(5, 6))
    @patch("coordinate_service.json.load", return_value={"link_x": 0, "link_y": 0})
    @patch("builtins.open")
    @patch("coordinate_service.os.path.exists", return_value=True)
    def test_placeholder_detail_link_coordinates_are_rerecorded(
        self, _path_exists, _open_config, _load_json, record_coordinates,
    ):
        self.assertEqual(
            coordinate_service.CoordinateService.load_or_record_detail_link_coordinates(),
            (5, 6),
        )
        record_coordinates.assert_called_once()

    @patch("coordinate_service.CoordinateService._record_fwxx_coordinates",
           return_value=(1, 2, 3, 4))
    @patch("coordinate_service.json.load", return_value={
        "link_x": 0, "link_y": 0, "fwxx_menu_x": 0, "fwxx_menu_y": 0,
    })
    @patch("builtins.open")
    @patch("coordinate_service.os.path.exists", return_value=True)
    def test_placeholder_fwxx_coordinates_are_rerecorded(
        self, _path_exists, _open_config, _load_json, record_coordinates,
    ):
        self.assertEqual(
            coordinate_service.CoordinateService.load_or_record_fwxx_coordinates(),
            (1, 2, 3, 4),
        )
        record_coordinates.assert_called_once()

    @patch("coordinate_service.CoordinateService._record_fee_menu_coordinates",
           return_value=(7, 8))
    @patch("coordinate_service.json.load", return_value={"fee_menu_x": 0, "fee_menu_y": 0})
    @patch("builtins.open")
    @patch("coordinate_service.os.path.exists", return_value=True)
    def test_placeholder_fee_menu_coordinates_are_rerecorded(
        self, _path_exists, _open_config, _load_json, record_coordinates,
    ):
        self.assertEqual(
            coordinate_service.CoordinateService.load_or_record_fee_menu_coordinates(),
            (7, 8),
        )
        record_coordinates.assert_called_once()


class TestRecordedCoordinatesAreKept(unittest.TestCase):
    @patch("coordinate_service.CoordinateService._record_search_coordinates")
    @patch("coordinate_service.json.load", return_value={
        "input_x": 366, "input_y": 242, "button_x": 722, "button_y": 368,
    })
    @patch("builtins.open")
    @patch("coordinate_service.os.path.exists", return_value=True)
    def test_real_coordinates_do_not_trigger_recording(
        self, _path_exists, _open_config, _load_json, record_coordinates,
    ):
        self.assertEqual(
            coordinate_service.CoordinateService.load_or_record_search_coordinates(),
            (366, 242, 722, 368),
        )
        record_coordinates.assert_not_called()

    @patch("coordinate_service.CoordinateService._record_fee_menu_coordinates")
    @patch("coordinate_service.json.load", return_value={"fee_menu_x": 0, "fee_menu_y": 415})
    @patch("builtins.open")
    @patch("coordinate_service.os.path.exists", return_value=True)
    def test_zero_on_one_axis_is_still_a_recorded_coordinate(
        self, _path_exists, _open_config, _load_json, record_coordinates,
    ):
        # 只有整组全 0 才是占位值；单个轴为 0 是屏幕边缘上的合法坐标
        self.assertEqual(
            coordinate_service.CoordinateService.load_or_record_fee_menu_coordinates(),
            (0, 415),
        )
        record_coordinates.assert_not_called()


if __name__ == '__main__':
    unittest.main()
