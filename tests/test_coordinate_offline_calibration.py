#!/usr/bin/env python
# -*- coding: utf-8 -*-

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import ANY, MagicMock, call, patch

import browser_service
import coordinate_service
import settings


FIXED_WINDOW_GEOMETRY = {
    "x": 10,
    "y": 8,
    "width": 120,
    "height": 90,
}
SCREEN_GEOMETRY = {
    "width": 160,
    "height": 120,
}


def _search_coordinate_config() -> dict:
    return {
        "input_x": 40,
        "input_y": 30,
        "button_x": 120,
        "button_y": 80,
        "window_geometry": dict(FIXED_WINDOW_GEOMETRY),
        "screen_geometry": dict(SCREEN_GEOMETRY),
    }


def _detail_coordinate_config() -> dict:
    return {
        "link_x": 20,
        "link_y": 25,
        "fwxx_menu_x": 40,
        "fwxx_menu_y": 45,
        "fee_menu_x": 60,
        "fee_menu_y": 65,
        "window_geometry": dict(FIXED_WINDOW_GEOMETRY),
        "screen_geometry": dict(SCREEN_GEOMETRY),
        "last_updated": "2026-08-12T12:00:00",
        "preserved_marker": "same-desktop",
    }


def _write_search_coordinate_config(config_path: Path, coordinate_config: dict) -> None:
    config_path.write_text(
        json.dumps(coordinate_config),
        encoding="utf-8",
    )


class TestManualSearchCoordinateCalibration(unittest.TestCase):
    def setUp(self) -> None:
        prompt_patch = patch.object(coordinate_service, "_show_calibration_prompt")
        self.calibration_prompt = prompt_patch.start()
        self.addCleanup(prompt_patch.stop)
        self.temporary_directory = TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.config_path = Path(self.temporary_directory.name) / "config.json"

        config_path_patch = patch.object(
            coordinate_service,
            "CONFIG_FILE",
            self.config_path,
        )
        config_path_patch.start()
        self.addCleanup(config_path_patch.stop)

        window_geometry_patch = patch.multiple(
            coordinate_service,
            BROWSER_WINDOW_X=FIXED_WINDOW_GEOMETRY["x"],
            BROWSER_WINDOW_Y=FIXED_WINDOW_GEOMETRY["y"],
            BROWSER_WINDOW_WIDTH=FIXED_WINDOW_GEOMETRY["width"],
            BROWSER_WINDOW_HEIGHT=FIXED_WINDOW_GEOMETRY["height"],
        )
        window_geometry_patch.start()
        self.addCleanup(window_geometry_patch.stop)

        screen_size_patch = patch.object(
            coordinate_service.pyautogui,
            "size",
            return_value=(SCREEN_GEOMETRY["width"], SCREEN_GEOMETRY["height"]),
        )
        screen_size_patch.start()
        self.addCleanup(screen_size_patch.stop)

    def test_search_targets_use_next_left_clicks_in_order(self):
        fixed_timestamp = "2026-08-13T12:00:00"
        with patch.object(
            coordinate_service.CoordinateService,
            "_countdown",
        ) as countdown, patch.object(
            coordinate_service,
            "position_foreground_window_for_coordinate_calibration",
        ) as position_window, patch.object(
            coordinate_service,
            "_capture_next_left_click_coordinate",
            side_effect=[(40, 30), (120, 80)],
        ) as capture_click, patch.object(
            coordinate_service,
            "datetime",
        ) as datetime_type, patch.object(
            coordinate_service,
            "write_json_atomic",
        ) as write_coordinates:
            datetime_type.now.return_value.isoformat.return_value = fixed_timestamp

            coordinates = (
                coordinate_service.CoordinateService
                .record_search_coordinates_from_pointer()
            )

        self.assertEqual(coordinates, (40, 30, 120, 80))
        countdown.assert_not_called()
        position_window.assert_called_once_with()
        self.assertEqual(
            capture_click.call_args_list,
            [
                call("申请号输入框", position_window.return_value),
                call("查询按钮", position_window.return_value),
            ],
        )
        write_coordinates.assert_called_once_with(
            self.config_path,
            {
                "input_x": 40,
                "input_y": 30,
                "button_x": 120,
                "button_y": 80,
                "window_geometry": FIXED_WINDOW_GEOMETRY,
                "screen_geometry": SCREEN_GEOMETRY,
                "last_updated": fixed_timestamp,
            },
        )

    def test_cancelling_preparation_does_not_start_recording_or_save(self):
        self.calibration_prompt.side_effect = (
            coordinate_service.CoordinateConfigurationError("已取消坐标校准")
        )
        with patch.object(
            coordinate_service, "position_foreground_window_for_coordinate_calibration",
        ) as position_window, patch.object(
            coordinate_service, "_capture_next_left_click_coordinate",
        ) as capture_click, patch.object(
            coordinate_service, "write_json_atomic",
        ) as write_coordinates:
            with self.assertRaisesRegex(
                coordinate_service.CoordinateConfigurationError, "已取消",
            ):
                coordinate_service.CoordinateService.record_search_coordinates_from_pointer()

        position_window.assert_not_called()
        capture_click.assert_not_called()
        write_coordinates.assert_not_called()

    def test_matching_pointer_targets_are_rejected_before_writing(self):
        with patch.object(
            coordinate_service.CoordinateService,
            "_countdown",
        ), patch.object(
            coordinate_service,
            "position_foreground_window_for_coordinate_calibration",
        ), patch.object(
            coordinate_service,
            "_capture_next_left_click_coordinate",
            side_effect=[(40, 30), (40, 30)],
        ), patch.object(
            coordinate_service,
            "write_json_atomic",
        ) as write_coordinates:
            with self.assertRaises(coordinate_service.CoordinateConfigurationError):
                (
                    coordinate_service.CoordinateService
                    .record_search_coordinates_from_pointer()
                )

        write_coordinates.assert_not_called()

    def test_atomic_write_failure_is_propagated(self):
        with patch.object(
            coordinate_service.CoordinateService,
            "_countdown",
        ), patch.object(
            coordinate_service,
            "position_foreground_window_for_coordinate_calibration",
        ), patch.object(
            coordinate_service,
            "_capture_next_left_click_coordinate",
            side_effect=[(40, 30), (120, 80)],
        ), patch.object(
            coordinate_service,
            "write_json_atomic",
            side_effect=OSError("坐标配置无法写入"),
        ):
            with self.assertRaisesRegex(OSError, "无法写入"):
                (
                    coordinate_service.CoordinateService
                    .record_search_coordinates_from_pointer()
                )

    def test_unsafe_pointer_targets_are_rejected_before_writing(self):
        unsafe_target_pairs = (
            ((0, 0), (120, 80)),
            ((40, 30), (SCREEN_GEOMETRY["width"], 80)),
        )
        for pointer_targets in unsafe_target_pairs:
            with self.subTest(pointer_targets=pointer_targets), patch.object(
                coordinate_service.CoordinateService,
                "_countdown",
            ), patch.object(
                coordinate_service,
                "position_foreground_window_for_coordinate_calibration",
            ), patch.object(
                coordinate_service,
                "_capture_next_left_click_coordinate",
                side_effect=pointer_targets,
            ), patch.object(
                coordinate_service,
                "write_json_atomic",
            ) as write_coordinates:
                with self.assertRaises(
                    coordinate_service.CoordinateConfigurationError
                ):
                    (
                        coordinate_service.CoordinateService
                        .record_search_coordinates_from_pointer()
                    )
                write_coordinates.assert_not_called()

    def test_browser_toolbar_target_is_rejected_before_writing(self):
        with patch.object(
            coordinate_service.CoordinateService,
            "_countdown",
        ), patch.object(
            coordinate_service,
            "position_foreground_window_for_coordinate_calibration",
        ), patch.object(
            coordinate_service,
            "_capture_next_left_click_coordinate",
            side_effect=[(40, 24), (120, 80)],
        ), patch.object(
            coordinate_service,
            "write_json_atomic",
        ) as write_coordinates:
            with self.assertRaisesRegex(
                coordinate_service.CoordinateConfigurationError,
                "浏览器.*工具栏|网页内容区域",
            ):
                (
                    coordinate_service.CoordinateService
                    .record_search_coordinates_from_pointer()
                )

        write_coordinates.assert_not_called()

    def test_pointer_values_follow_shared_integer_validation_without_coercion(self):
        for invalid_x in (True, "40", 40.5):
            with self.subTest(invalid_x=invalid_x), patch.object(
                coordinate_service,
                "position_foreground_window_for_coordinate_calibration",
            ), patch.object(
                coordinate_service,
                "_capture_next_left_click_coordinate",
                side_effect=[(invalid_x, 30), (120, 80)],
            ), patch.object(
                coordinate_service, "write_json_atomic",
            ) as write_coordinates:
                with self.assertRaisesRegex(
                    coordinate_service.CoordinateConfigurationError, "必须是整数",
                ):
                    coordinate_service.CoordinateService.record_search_coordinates_from_pointer()

            write_coordinates.assert_not_called()


class TestManualDetailCoordinateCalibration(unittest.TestCase):
    OPERATIONS = (
        (
            "record_detail_link_coordinate_from_pointer",
            ("link_x", "link_y"),
            (10, 30),
        ),
        (
            "record_fwxx_menu_coordinate_from_pointer",
            ("fwxx_menu_x", "fwxx_menu_y"),
            (70, 80),
        ),
        (
            "record_fee_menu_coordinate_from_pointer",
            ("fee_menu_x", "fee_menu_y"),
            (90, 90),
        ),
    )

    def setUp(self) -> None:
        prompt_patch = patch.object(coordinate_service, "_show_calibration_prompt")
        prompt_patch.start()
        self.addCleanup(prompt_patch.stop)
        self.temporary_directory = TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.config_path = Path(self.temporary_directory.name) / "config_fwxx.json"

        config_path_patch = patch.object(
            coordinate_service,
            "CONFIG_FWXX_FILE",
            self.config_path,
        )
        config_path_patch.start()
        self.addCleanup(config_path_patch.stop)

        window_geometry_patch = patch.multiple(
            coordinate_service,
            BROWSER_WINDOW_X=FIXED_WINDOW_GEOMETRY["x"],
            BROWSER_WINDOW_Y=FIXED_WINDOW_GEOMETRY["y"],
            BROWSER_WINDOW_WIDTH=FIXED_WINDOW_GEOMETRY["width"],
            BROWSER_WINDOW_HEIGHT=FIXED_WINDOW_GEOMETRY["height"],
        )
        window_geometry_patch.start()
        self.addCleanup(window_geometry_patch.stop)

        screen_size_patch = patch.object(
            coordinate_service.pyautogui,
            "size",
            return_value=(SCREEN_GEOMETRY["width"], SCREEN_GEOMETRY["height"]),
        )
        screen_size_patch.start()
        self.addCleanup(screen_size_patch.stop)

    def test_each_target_records_pointer_and_preserves_matching_geometry_config(self):
        fixed_timestamp = "2026-08-13T13:00:00"
        for operation_name, coordinate_keys, pointer_target in self.OPERATIONS:
            with self.subTest(operation_name=operation_name):
                existing_config = _detail_coordinate_config()
                self.config_path.write_text(
                    json.dumps(existing_config),
                    encoding="utf-8",
                )
                with patch.object(
                    coordinate_service.CoordinateService,
                    "_countdown",
                ) as countdown, patch.object(
                    coordinate_service,
                    "position_foreground_window_for_coordinate_calibration",
                ) as position_window, patch.object(
                    coordinate_service,
                    "_capture_next_left_click_coordinate",
                    return_value=pointer_target,
                ) as capture_click, patch.object(
                    coordinate_service,
                    "datetime",
                ) as datetime_type, patch.object(
                    coordinate_service,
                    "write_json_atomic",
                ) as write_coordinates:
                    datetime_type.now.return_value.isoformat.return_value = fixed_timestamp

                    coordinates = getattr(
                        coordinate_service.CoordinateService,
                        operation_name,
                    )()

                self.assertEqual(coordinates, pointer_target)
                countdown.assert_not_called()
                position_window.assert_called_once_with()
                capture_click.assert_called_once_with(ANY, position_window.return_value)
                expected_config = dict(existing_config)
                expected_config.update({
                    coordinate_keys[0]: pointer_target[0],
                    coordinate_keys[1]: pointer_target[1],
                    "last_updated": fixed_timestamp,
                })
                write_coordinates.assert_called_once_with(
                    self.config_path,
                    expected_config,
                )

    def test_geometry_change_discards_stale_detail_coordinates(self):
        mismatched_configs = []
        window_mismatch = _detail_coordinate_config()
        window_mismatch["window_geometry"]["width"] += 1
        mismatched_configs.append(window_mismatch)
        screen_mismatch = _detail_coordinate_config()
        screen_mismatch["screen_geometry"]["height"] += 1
        mismatched_configs.append(screen_mismatch)

        for existing_config in mismatched_configs:
            with self.subTest(existing_config=existing_config):
                self.config_path.write_text(
                    json.dumps(existing_config),
                    encoding="utf-8",
                )
                with patch.object(
                    coordinate_service.CoordinateService,
                    "_countdown",
                ), patch.object(
                    coordinate_service,
                    "position_foreground_window_for_coordinate_calibration",
                ), patch.object(
                    coordinate_service,
                    "_capture_next_left_click_coordinate",
                    return_value=(35, 40),
                ), patch.object(
                    coordinate_service,
                    "datetime",
                ) as datetime_type, patch.object(
                    coordinate_service,
                    "write_json_atomic",
                ) as write_coordinates:
                    datetime_type.now.return_value.isoformat.return_value = (
                        "2026-08-13T14:00:00"
                    )

                    (
                        coordinate_service.CoordinateService
                        .record_detail_link_coordinate_from_pointer()
                    )

                write_coordinates.assert_called_once_with(
                    self.config_path,
                    {
                        "link_x": 35,
                        "link_y": 40,
                        "window_geometry": FIXED_WINDOW_GEOMETRY,
                        "screen_geometry": SCREEN_GEOMETRY,
                        "last_updated": "2026-08-13T14:00:00",
                    },
                )

    def test_unsafe_pointer_targets_are_rejected_before_writing(self):
        unsafe_targets = (
            (0, 0),
            (-1, 30),
            (FIXED_WINDOW_GEOMETRY["x"] - 1, 30),
            (
                FIXED_WINDOW_GEOMETRY["x"]
                + FIXED_WINDOW_GEOMETRY["width"],
                30,
            ),
            (SCREEN_GEOMETRY["width"], 30),
            (40, -1),
            (
                40,
                FIXED_WINDOW_GEOMETRY["y"]
                + FIXED_WINDOW_GEOMETRY["height"],
            ),
            (40, SCREEN_GEOMETRY["height"]),
        )
        for operation_name, _, _ in self.OPERATIONS:
            for pointer_target in unsafe_targets:
                with self.subTest(
                    operation_name=operation_name,
                    pointer_target=pointer_target,
                ), patch.object(
                    coordinate_service.CoordinateService,
                    "_countdown",
                ), patch.object(
                    coordinate_service,
                    "position_foreground_window_for_coordinate_calibration",
                ), patch.object(
                    coordinate_service,
                    "_capture_next_left_click_coordinate",
                    return_value=pointer_target,
                ), patch.object(
                    coordinate_service,
                    "write_json_atomic",
                ) as write_coordinates:
                    with self.assertRaises(
                        coordinate_service.CoordinateConfigurationError
                    ):
                        getattr(
                            coordinate_service.CoordinateService,
                            operation_name,
                        )()

                write_coordinates.assert_not_called()

    def test_atomic_write_failure_is_propagated(self):
        for operation_name, _, pointer_target in self.OPERATIONS:
            with self.subTest(operation_name=operation_name), patch.object(
                coordinate_service.CoordinateService,
                "_countdown",
            ), patch.object(
                coordinate_service,
                "position_foreground_window_for_coordinate_calibration",
            ), patch.object(
                coordinate_service,
                "_capture_next_left_click_coordinate",
                return_value=pointer_target,
            ), patch.object(
                coordinate_service,
                "write_json_atomic",
                side_effect=OSError("\u8be6\u60c5\u9875\u5750\u6807\u65e0\u6cd5\u5199\u5165"),
            ):
                with self.assertRaisesRegex(OSError, "\u65e0\u6cd5\u5199\u5165"):
                    getattr(
                        coordinate_service.CoordinateService,
                        operation_name,
                    )()


class TestPointerClickCapture(unittest.TestCase):
    def test_switching_click_is_released_before_target_click_is_captured(self):
        user32 = MagicMock()
        user32.MessageBoxW.return_value = 1
        user32.GetAsyncKeyState.side_effect = [0x8000, 0, 0x8000, 0]

        with patch.object(
            coordinate_service.sys,
            "platform",
            "win32",
        ), patch.object(
            coordinate_service.ctypes,
            "windll",
            SimpleNamespace(user32=user32),
            create=True,
        ), patch.object(
            coordinate_service.pyautogui,
            "position",
            return_value=(40, 50),
        ) as pointer_position, patch.object(
            coordinate_service.time,
            "sleep",
        ), patch.object(
            coordinate_service, "_validate_calibration_click",
            side_effect=lambda _window, _target: self.assertEqual(
                user32.GetAsyncKeyState.call_count, 3,
            ),
        ) as validate_click:
            target = coordinate_service._capture_next_left_click_coordinate(
                "搜索结果专利标题", 123,
            )

        self.assertEqual(target, (40, 50))
        validate_click.assert_called_once_with(123, (40, 50))
        pointer_position.assert_called_once_with()
        user32.MessageBoxW.assert_called_once()
        self.assertEqual(user32.GetAsyncKeyState.call_count, 4)

    def test_wrong_window_click_fails_before_returning_coordinate(self):
        user32 = MagicMock()
        user32.MessageBoxW.return_value = 1
        user32.GetAsyncKeyState.side_effect = [0, 0x8000]
        with patch.object(
            coordinate_service.sys, "platform", "win32",
        ), patch.object(
            coordinate_service.ctypes, "windll", SimpleNamespace(user32=user32),
            create=True,
        ), patch.object(
            coordinate_service.pyautogui, "position", return_value=(40, 50),
        ), patch.object(
            coordinate_service.time, "sleep",
        ), patch.object(
            coordinate_service, "_validate_calibration_click",
            side_effect=coordinate_service.CoordinateConfigurationError("点击不属于本次选定的窗口"),
        ):
            with self.assertRaisesRegex(
                coordinate_service.CoordinateConfigurationError, "点击不属于",
            ):
                coordinate_service._capture_next_left_click_coordinate("费用信息菜单", 123)

    def test_target_click_timeout_raises_clear_error(self):
        user32 = MagicMock()
        user32.MessageBoxW.return_value = 1
        user32.GetAsyncKeyState.return_value = 0

        with patch.object(
            coordinate_service.sys,
            "platform",
            "win32",
        ), patch.object(
            coordinate_service.ctypes,
            "windll",
            SimpleNamespace(user32=user32),
            create=True,
        ), patch.object(
            coordinate_service.time,
            "sleep",
        ), patch.object(
            coordinate_service.time,
            "monotonic",
            side_effect=[0.0, 61.0],
        ):
            with self.assertRaisesRegex(
                coordinate_service.CoordinateConfigurationError,
                "等待.*点击.*超时",
            ):
                coordinate_service._capture_next_left_click_coordinate(
                    "发文信息菜单", 123,
                )


class TestCalibrationPreparation(unittest.TestCase):
    def test_page_preparation_is_confirmed_before_selecting_the_target_window(self):
        preparation_events = []
        with patch.object(
            coordinate_service, "_show_calibration_prompt",
            side_effect=lambda message: preparation_events.append(("prepare", message)),
        ), patch.object(
            coordinate_service, "position_foreground_window_for_coordinate_calibration",
            side_effect=lambda: preparation_events.append(("select",)) or 123,
        ):
            selected_window = coordinate_service._prepare_calibration_window("打开发文信息详情页")

        self.assertEqual(selected_window, 123)
        self.assertEqual([event[0] for event in preparation_events], ["prepare", "select"])
        self.assertIn("打开发文信息详情页", preparation_events[0][1])
        self.assertIn("这些点击不会录入坐标", preparation_events[0][1])

    def test_cancel_button_stops_calibration(self):
        user32 = MagicMock()
        user32.MessageBoxW.return_value = 2
        with patch.object(
            coordinate_service.sys, "platform", "win32",
        ), patch.object(
            coordinate_service.ctypes, "windll", SimpleNamespace(user32=user32),
            create=True,
        ):
            with self.assertRaisesRegex(
                coordinate_service.CoordinateConfigurationError, "已取消",
            ):
                coordinate_service._show_calibration_prompt("准备页面")


class TestForegroundWindowPositioning(unittest.TestCase):
    @staticmethod
    def _user32(
        window_title: str,
        desktop_windows: dict[int, tuple[str, str]] | None = None,
    ) -> MagicMock:
        user32 = MagicMock()
        user32.GetForegroundWindow.return_value = 123
        window_descriptions = {
            123: ("Chrome_WidgetWin_1", window_title),
            **(desktop_windows or {}),
        }

        def set_class_name(window_handle, output_buffer, _buffer_size):
            output_buffer.value = window_descriptions[window_handle][0]
            return len(output_buffer.value)

        def set_window_title(window_handle, output_buffer, _buffer_size):
            output_buffer.value = window_descriptions[window_handle][1]
            return len(output_buffer.value)

        def set_window_rect(_window_handle, rect_pointer):
            rect = rect_pointer._obj
            rect.left = FIXED_WINDOW_GEOMETRY["x"]
            rect.top = FIXED_WINDOW_GEOMETRY["y"]
            rect.right = rect.left + FIXED_WINDOW_GEOMETRY["width"]
            rect.bottom = rect.top + FIXED_WINDOW_GEOMETRY["height"]
            return 1

        user32.GetClassNameW.side_effect = set_class_name
        user32.GetWindowTextW.side_effect = set_window_title
        user32.SetWindowPos.return_value = 1
        user32.GetWindowRect.side_effect = set_window_rect
        return user32

    def test_foreground_cnipa_chrome_is_positioned_without_activation(self):
        user32 = self._user32("中国及多国专利审查信息查询 - Google Chrome")

        with patch.object(
            coordinate_service.sys,
            "platform",
            "win32",
        ), patch.object(
            coordinate_service.ctypes,
            "windll",
            SimpleNamespace(user32=user32),
            create=True,
        ), patch.multiple(
            coordinate_service,
            BROWSER_WINDOW_X=FIXED_WINDOW_GEOMETRY["x"],
            BROWSER_WINDOW_Y=FIXED_WINDOW_GEOMETRY["y"],
            BROWSER_WINDOW_WIDTH=FIXED_WINDOW_GEOMETRY["width"],
            BROWSER_WINDOW_HEIGHT=FIXED_WINDOW_GEOMETRY["height"],
        ):
            coordinate_service.position_foreground_window_for_coordinate_calibration()

        self.assertGreaterEqual(user32.GetForegroundWindow.call_count, 2)
        user32.ShowWindow.assert_not_called()
        user32.SetWindowPos.assert_called_once_with(
            123,
            None,
            FIXED_WINDOW_GEOMETRY["x"],
            FIXED_WINDOW_GEOMETRY["y"],
            FIXED_WINDOW_GEOMETRY["width"],
            FIXED_WINDOW_GEOMETRY["height"],
            0x0004 | 0x0010,
        )
        user32.GetWindowRect.assert_called_once()
        user32.EnumWindows.assert_not_called()
        user32.SetForegroundWindow.assert_not_called()

    def test_waits_for_operator_to_switch_from_dashboard_to_cnipa_chrome(self):
        user32 = self._user32(
            "CNIPA 采集控制台 - Google Chrome",
            {
                456: (
                    "Chrome_WidgetWin_1",
                    "中国及多国专利审查信息查询 - Google Chrome",
                ),
            },
        )
        foreground_handles = iter((123,))
        user32.GetForegroundWindow.side_effect = (
            lambda: next(foreground_handles, 456)
        )

        with patch.object(
            coordinate_service.sys,
            "platform",
            "win32",
        ), patch.object(
            coordinate_service.ctypes,
            "windll",
            SimpleNamespace(user32=user32),
            create=True,
        ), patch.multiple(
            coordinate_service,
            BROWSER_WINDOW_X=FIXED_WINDOW_GEOMETRY["x"],
            BROWSER_WINDOW_Y=FIXED_WINDOW_GEOMETRY["y"],
            BROWSER_WINDOW_WIDTH=FIXED_WINDOW_GEOMETRY["width"],
            BROWSER_WINDOW_HEIGHT=FIXED_WINDOW_GEOMETRY["height"],
        ), patch.object(
            coordinate_service.time,
            "monotonic",
            return_value=0.0,
        ), patch.object(
            coordinate_service.time,
            "sleep",
        ) as sleep:
            coordinate_service.position_foreground_window_for_coordinate_calibration()

        self.assertGreaterEqual(user32.GetForegroundWindow.call_count, 3)
        sleep.assert_called()
        user32.ShowWindow.assert_not_called()
        user32.SetWindowPos.assert_called_once_with(
            456,
            None,
            FIXED_WINDOW_GEOMETRY["x"],
            FIXED_WINDOW_GEOMETRY["y"],
            FIXED_WINDOW_GEOMETRY["width"],
            FIXED_WINDOW_GEOMETRY["height"],
            0x0004 | 0x0010,
        )
        user32.GetWindowRect.assert_called_once()
        user32.EnumWindows.assert_not_called()
        user32.SetForegroundWindow.assert_not_called()

    def test_cnipa_match_that_loses_focus_keeps_waiting_for_operator(self):
        user32 = self._user32(
            "CNIPA 采集控制台 - Google Chrome",
            {
                456: (
                    "Chrome_WidgetWin_1",
                    "中国及多国专利审查信息查询 - Google Chrome",
                ),
            },
        )
        foreground_handles = iter((123, 456, 123, 456))
        user32.GetForegroundWindow.side_effect = (
            lambda: next(foreground_handles, 456)
        )

        with patch.object(
            coordinate_service.sys,
            "platform",
            "win32",
        ), patch.object(
            coordinate_service.ctypes,
            "windll",
            SimpleNamespace(user32=user32),
            create=True,
        ), patch.multiple(
            coordinate_service,
            BROWSER_WINDOW_X=FIXED_WINDOW_GEOMETRY["x"],
            BROWSER_WINDOW_Y=FIXED_WINDOW_GEOMETRY["y"],
            BROWSER_WINDOW_WIDTH=FIXED_WINDOW_GEOMETRY["width"],
            BROWSER_WINDOW_HEIGHT=FIXED_WINDOW_GEOMETRY["height"],
        ), patch.object(
            coordinate_service.time,
            "monotonic",
            return_value=0.0,
        ), patch.object(
            coordinate_service.time,
            "sleep",
        ) as sleep:
            coordinate_service.position_foreground_window_for_coordinate_calibration()

        self.assertGreaterEqual(user32.GetForegroundWindow.call_count, 5)
        self.assertGreaterEqual(sleep.call_count, 2)
        user32.SetWindowPos.assert_called_with(
            456,
            None,
            FIXED_WINDOW_GEOMETRY["x"],
            FIXED_WINDOW_GEOMETRY["y"],
            FIXED_WINDOW_GEOMETRY["width"],
            FIXED_WINDOW_GEOMETRY["height"],
            0x0004 | 0x0010,
        )
        user32.GetWindowRect.assert_called()
        user32.EnumWindows.assert_not_called()
        user32.SetForegroundWindow.assert_not_called()
        user32.ShowWindow.assert_not_called()

    def test_manual_cnipa_switch_timeout_reports_clear_error(self):
        user32 = self._user32("CNIPA 采集控制台 - Google Chrome")
        monotonic_ticks = iter((0.0, 0.0))

        with patch.object(
            coordinate_service.sys,
            "platform",
            "win32",
        ), patch.object(
            coordinate_service.ctypes,
            "windll",
            SimpleNamespace(user32=user32),
            create=True,
        ), patch.object(
            coordinate_service.time,
            "monotonic",
            side_effect=lambda: next(monotonic_ticks, 1_000_000.0),
        ), patch.object(
            coordinate_service.time,
            "sleep",
        ):
            with self.assertRaises(
                coordinate_service.CoordinateConfigurationError,
            ) as raised:
                coordinate_service.position_foreground_window_for_coordinate_calibration()

        self.assertIn("超时", str(raised.exception))
        self.assertIn("人工切换", str(raised.exception))
        self.assertIn("CNIPA", str(raised.exception))
        user32.EnumWindows.assert_not_called()
        user32.SetForegroundWindow.assert_not_called()
        user32.ShowWindow.assert_not_called()
        user32.SetWindowPos.assert_not_called()


class TestCalibrationClickWindowValidation(unittest.TestCase):
    def setUp(self) -> None:
        self.user32 = TestForegroundWindowPositioning._user32(
            "中国及多国专利审查信息查询 - Google Chrome"
        )
        self.user32.WindowFromPoint.return_value = 321
        self.user32.GetAncestor.return_value = 123
        user32_patch = patch.object(
            coordinate_service.ctypes, "windll", SimpleNamespace(user32=self.user32),
            create=True,
        )
        user32_patch.start()
        self.addCleanup(user32_patch.stop)
        geometry_patch = patch.multiple(
            coordinate_service,
            BROWSER_WINDOW_X=FIXED_WINDOW_GEOMETRY["x"],
            BROWSER_WINDOW_Y=FIXED_WINDOW_GEOMETRY["y"],
            BROWSER_WINDOW_WIDTH=FIXED_WINDOW_GEOMETRY["width"],
            BROWSER_WINDOW_HEIGHT=FIXED_WINDOW_GEOMETRY["height"],
        )
        geometry_patch.start()
        self.addCleanup(geometry_patch.stop)

    def test_click_is_bound_to_the_selected_cnipa_root_window(self):
        coordinate_service._validate_calibration_click(123, (40, 50))

        sampled_point = self.user32.WindowFromPoint.call_args.args[0]
        self.assertEqual((sampled_point.x, sampled_point.y), (40, 50))
        self.user32.GetAncestor.assert_called_once_with(321, 2)

    def test_another_window_at_the_same_screen_coordinate_is_rejected(self):
        self.user32.GetAncestor.return_value = 456
        with self.assertRaisesRegex(
            coordinate_service.CoordinateConfigurationError, "点击不属于",
        ):
            coordinate_service._validate_calibration_click(123, (40, 50))

    def test_selected_window_must_still_be_foreground(self):
        self.user32.GetForegroundWindow.return_value = 456
        with self.assertRaisesRegex(
            coordinate_service.CoordinateConfigurationError, "点击不属于",
        ):
            coordinate_service._validate_calibration_click(123, (40, 50))

    def test_switching_the_selected_window_to_another_tab_is_rejected(self):
        def write_unrelated_title(_window_handle, title_buffer, _buffer_size):
            title_buffer.value = "CNIPA 采集控制台 - Google Chrome"
            return len(title_buffer.value)

        self.user32.GetWindowTextW.side_effect = write_unrelated_title
        with self.assertRaisesRegex(
            coordinate_service.CoordinateConfigurationError, "不是 CNIPA 页面",
        ):
            coordinate_service._validate_calibration_click(123, (40, 50))

    def test_geometry_changes_after_preparation_are_rejected(self):
        with patch.object(coordinate_service, "BROWSER_WINDOW_WIDTH", 121):
            with self.assertRaisesRegex(
                coordinate_service.CoordinateConfigurationError, "位置或尺寸已改变",
            ):
                coordinate_service._validate_calibration_click(123, (40, 50))


class TestStrictSearchCoordinateLoading(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.config_path = Path(self.temporary_directory.name) / "config.json"

        config_path_patch = patch.object(
            coordinate_service,
            "CONFIG_FILE",
            self.config_path,
        )
        config_path_patch.start()
        self.addCleanup(config_path_patch.stop)

        window_geometry_patch = patch.multiple(
            coordinate_service,
            BROWSER_WINDOW_X=FIXED_WINDOW_GEOMETRY["x"],
            BROWSER_WINDOW_Y=FIXED_WINDOW_GEOMETRY["y"],
            BROWSER_WINDOW_WIDTH=FIXED_WINDOW_GEOMETRY["width"],
            BROWSER_WINDOW_HEIGHT=FIXED_WINDOW_GEOMETRY["height"],
            create=True,
        )
        window_geometry_patch.start()
        self.addCleanup(window_geometry_patch.stop)

        screen_size_patch = patch.object(
            coordinate_service.pyautogui,
            "size",
            return_value=(SCREEN_GEOMETRY["width"], SCREEN_GEOMETRY["height"]),
        )
        self.screen_size = screen_size_patch.start()
        self.addCleanup(screen_size_patch.stop)

        record_coordinates_patch = patch.object(
            coordinate_service.CoordinateService,
            "_record_search_coordinates",
        )
        self.record_coordinates = record_coordinates_patch.start()
        self.addCleanup(record_coordinates_patch.stop)

    def test_missing_config_raises_clear_error_without_online_recording(self):
        with self.assertRaisesRegex(
            coordinate_service.CoordinateConfigurationError,
            r"(?:\u4e0d\u5b58\u5728|\u7f3a\u5931).*\u5750\u6807\u6821\u51c6",
        ):
            coordinate_service.CoordinateService.load_search_coordinates()

        self.record_coordinates.assert_not_called()

    def test_all_zero_coordinates_raise_clear_error_without_online_recording(self):
        coordinate_config = _search_coordinate_config()
        coordinate_config.update({
            "input_x": 0,
            "input_y": 0,
            "button_x": 0,
            "button_y": 0,
        })
        _write_search_coordinate_config(self.config_path, coordinate_config)

        with self.assertRaisesRegex(
            coordinate_service.CoordinateConfigurationError,
            r"(?:\u5168.*0|\u5360\u4f4d).*\u5750\u6807\u6821\u51c6",
        ):
            coordinate_service.CoordinateService.load_search_coordinates()

        self.record_coordinates.assert_not_called()

    def test_empty_config_reports_missing_coordinates_before_geometry_mismatch(self):
        _write_search_coordinate_config(self.config_path, {})

        with self.assertRaisesRegex(
            coordinate_service.CoordinateConfigurationError,
            r"(?:\u7f3a\u5931|\u5360\u4f4d).*\u5750\u6807\u6821\u51c6",
        ) as raised:
            coordinate_service.CoordinateService.load_search_coordinates()

        self.assertNotIn("\u4e0d\u5339\u914d", str(raised.exception))
        self.record_coordinates.assert_not_called()

    def test_matching_search_targets_are_rejected_before_collection(self):
        coordinate_config = _search_coordinate_config()
        coordinate_config["button_x"] = coordinate_config["input_x"]
        coordinate_config["button_y"] = coordinate_config["input_y"]
        _write_search_coordinate_config(self.config_path, coordinate_config)

        with self.assertRaisesRegex(
            coordinate_service.CoordinateConfigurationError,
            r"(?:\u4e0d\u80fd\u76f8\u540c|\u5fc5\u987b\u4e0d\u540c)",
        ):
            coordinate_service.CoordinateService.load_search_coordinates()

        self.record_coordinates.assert_not_called()

    def test_window_geometry_mismatch_raises_clear_error_without_online_recording(self):
        coordinate_config = _search_coordinate_config()
        coordinate_config["window_geometry"]["width"] += 1
        _write_search_coordinate_config(self.config_path, coordinate_config)

        with self.assertRaisesRegex(
            coordinate_service.CoordinateConfigurationError,
            r"\u7a97\u53e3.*\u4e0d\u5339\u914d",
        ):
            coordinate_service.CoordinateService.load_search_coordinates()

        self.record_coordinates.assert_not_called()

    def test_screen_geometry_mismatch_raises_clear_error_without_online_recording(self):
        coordinate_config = _search_coordinate_config()
        coordinate_config["screen_geometry"]["height"] += 1
        _write_search_coordinate_config(self.config_path, coordinate_config)

        with self.assertRaisesRegex(
            coordinate_service.CoordinateConfigurationError,
            r"\u5c4f\u5e55.*\u4e0d\u5339\u914d",
        ):
            coordinate_service.CoordinateService.load_search_coordinates()

        self.record_coordinates.assert_not_called()

    def test_valid_geometry_bound_config_returns_search_coordinates(self):
        _write_search_coordinate_config(
            self.config_path,
            _search_coordinate_config(),
        )

        coordinates = coordinate_service.CoordinateService.load_search_coordinates()

        self.assertEqual(coordinates, (40, 30, 120, 80))
        self.record_coordinates.assert_not_called()

    def test_shared_field_rules_reject_each_placeholder_pair_and_non_integer(self):
        invalid_fields = (
            {"input_x": 0, "input_y": 0},
            {"button_x": 0, "button_y": 0},
            {"input_x": True},
            {"button_y": "80"},
            {"button_x": 120.5},
        )
        for malformed_coordinates in invalid_fields:
            with self.subTest(malformed_coordinates=malformed_coordinates):
                coordinate_config = _search_coordinate_config()
                coordinate_config.update(malformed_coordinates)
                _write_search_coordinate_config(self.config_path, coordinate_config)

                with self.assertRaisesRegex(
                    coordinate_service.CoordinateConfigurationError,
                    "占位值|必须是整数",
                ):
                    coordinate_service.CoordinateService.load_search_coordinates()

        self.record_coordinates.assert_not_called()


class TestStrictDetailCoordinateLoading(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.config_path = Path(self.temporary_directory.name) / "config_fwxx.json"

        config_path_patch = patch.object(
            coordinate_service,
            "CONFIG_FWXX_FILE",
            self.config_path,
        )
        config_path_patch.start()
        self.addCleanup(config_path_patch.stop)

        window_geometry_patch = patch.multiple(
            coordinate_service,
            BROWSER_WINDOW_X=FIXED_WINDOW_GEOMETRY["x"],
            BROWSER_WINDOW_Y=FIXED_WINDOW_GEOMETRY["y"],
            BROWSER_WINDOW_WIDTH=FIXED_WINDOW_GEOMETRY["width"],
            BROWSER_WINDOW_HEIGHT=FIXED_WINDOW_GEOMETRY["height"],
        )
        window_geometry_patch.start()
        self.addCleanup(window_geometry_patch.stop)

        screen_size_patch = patch.object(
            coordinate_service.pyautogui,
            "size",
            return_value=(SCREEN_GEOMETRY["width"], SCREEN_GEOMETRY["height"]),
        )
        screen_size_patch.start()
        self.addCleanup(screen_size_patch.stop)

    def test_valid_geometry_bound_config_returns_independent_targets(self):
        self.config_path.write_text(
            json.dumps(_detail_coordinate_config()),
            encoding="utf-8",
        )

        self.assertEqual(
            coordinate_service.CoordinateService.load_detail_link_coordinates(),
            (20, 25),
        )
        self.assertEqual(
            coordinate_service.CoordinateService.load_fwxx_menu_coordinates(),
            (40, 45),
        )
        self.assertEqual(
            coordinate_service.CoordinateService.load_fee_menu_coordinates(),
            (60, 65),
        )

    def test_loading_validates_only_coordinates_required_by_the_operation(self):
        coordinate_config = _detail_coordinate_config()
        coordinate_config.update({"fwxx_menu_x": True, "fee_menu_y": "invalid"})
        self.config_path.write_text(json.dumps(coordinate_config), encoding="utf-8")

        self.assertEqual(
            coordinate_service.CoordinateService.load_detail_link_coordinates(),
            (20, 25),
        )
        with self.assertRaisesRegex(
            coordinate_service.CoordinateConfigurationError, "必须是整数",
        ):
            coordinate_service.CoordinateService.load_fwxx_menu_coordinates()
        with self.assertRaisesRegex(
            coordinate_service.CoordinateConfigurationError, "必须是整数",
        ):
            coordinate_service.CoordinateService.load_fee_menu_coordinates()

    def test_zero_x_is_valid_at_the_fixed_window_screen_edge(self):
        coordinate_config = _detail_coordinate_config()
        coordinate_config["link_x"] = 0
        coordinate_config["window_geometry"]["x"] = 0
        self.config_path.write_text(json.dumps(coordinate_config), encoding="utf-8")

        with patch.object(coordinate_service, "BROWSER_WINDOW_X", 0):
            self.assertEqual(
                coordinate_service.CoordinateService.load_detail_link_coordinates(),
                (0, 25),
            )

    def test_toolbar_detail_link_is_rejected_before_collection(self):
        coordinate_config = _detail_coordinate_config()
        coordinate_config["link_y"] = 24
        self.config_path.write_text(
            json.dumps(coordinate_config),
            encoding="utf-8",
        )

        with self.assertRaisesRegex(
            coordinate_service.CoordinateConfigurationError,
            "浏览器.*工具栏|网页内容区域",
        ):
            coordinate_service.CoordinateService.load_detail_link_coordinates()

    def test_bookmarks_bar_target_is_rejected_for_collection_window(self):
        coordinate_config = _detail_coordinate_config()
        coordinate_config.update({
            "link_y": 100,
            "window_geometry": {
                "x": 0,
                "y": 0,
                "width": 1440,
                "height": 900,
            },
        })
        self.config_path.write_text(
            json.dumps(coordinate_config),
            encoding="utf-8",
        )

        with patch.multiple(
            coordinate_service,
            BROWSER_WINDOW_X=0,
            BROWSER_WINDOW_Y=0,
            BROWSER_WINDOW_WIDTH=1440,
            BROWSER_WINDOW_HEIGHT=900,
        ):
            with self.assertRaisesRegex(
                coordinate_service.CoordinateConfigurationError,
                "浏览器.*工具栏|网页内容区域",
            ):
                coordinate_service.CoordinateService.load_detail_link_coordinates()

    def test_chrome_ui_bottom_edge_is_rejected_for_collection_window(self):
        screen_geometry = {"width": 1920, "height": 1080}
        window_geometry = {"x": 0, "y": 0, "width": 1440, "height": 900}

        for link_y in (120, 124):
            with self.subTest(link_y=link_y):
                coordinate_config = _detail_coordinate_config()
                coordinate_config.update({
                    "link_x": 132,
                    "link_y": link_y,
                    "window_geometry": window_geometry,
                    "screen_geometry": screen_geometry,
                })
                self.config_path.write_text(
                    json.dumps(coordinate_config),
                    encoding="utf-8",
                )

                with patch.multiple(
                    coordinate_service,
                    BROWSER_WINDOW_X=0,
                    BROWSER_WINDOW_Y=0,
                    BROWSER_WINDOW_WIDTH=1440,
                    BROWSER_WINDOW_HEIGHT=900,
                ), patch.object(
                    coordinate_service.pyautogui,
                    "size",
                    return_value=(1920, 1080),
                ):
                    with self.assertRaisesRegex(
                        coordinate_service.CoordinateConfigurationError,
                        "浏览器.*工具栏|网页内容区域",
                    ):
                        (
                            coordinate_service.CoordinateService
                            .load_detail_link_coordinates()
                        )

    def test_target_outside_fixed_browser_window_is_rejected(self):
        coordinate_config = _detail_coordinate_config()
        coordinate_config["link_x"] = (
            FIXED_WINDOW_GEOMETRY["x"] + FIXED_WINDOW_GEOMETRY["width"]
        )
        self.config_path.write_text(
            json.dumps(coordinate_config),
            encoding="utf-8",
        )

        with self.assertRaisesRegex(
            coordinate_service.CoordinateConfigurationError,
            "浏览器窗口",
        ):
            coordinate_service.CoordinateService.load_detail_link_coordinates()


class TestFixedBrowserWindowGeometry(unittest.TestCase):
    def test_window_geometry_settings_are_explicit_integers(self):
        geometry_values = (
            settings.BROWSER_WINDOW_X,
            settings.BROWSER_WINDOW_Y,
            settings.BROWSER_WINDOW_WIDTH,
            settings.BROWSER_WINDOW_HEIGHT,
        )

        self.assertTrue(all(isinstance(value, int) for value in geometry_values))
        self.assertGreaterEqual(settings.BROWSER_WINDOW_X, 0)
        self.assertGreaterEqual(settings.BROWSER_WINDOW_Y, 0)
        self.assertGreater(settings.BROWSER_WINDOW_WIDTH, 0)
        self.assertGreater(settings.BROWSER_WINDOW_HEIGHT, 0)

    def test_launch_sets_fixed_window_geometry_before_navigation(self):
        driver = MagicMock()
        driver.get_window_rect.return_value = FIXED_WINDOW_GEOMETRY.copy()

        with patch.multiple(
            browser_service,
            BROWSER_WINDOW_X=FIXED_WINDOW_GEOMETRY["x"],
            BROWSER_WINDOW_Y=FIXED_WINDOW_GEOMETRY["y"],
            BROWSER_WINDOW_WIDTH=FIXED_WINDOW_GEOMETRY["width"],
            BROWSER_WINDOW_HEIGHT=FIXED_WINDOW_GEOMETRY["height"],
            create=True,
        ):
            with patch(
                "browser_service.create_driver_with_retry",
                return_value=driver,
            ):
                with patch("browser_service.time.sleep"):
                    with patch.object(BrowserService := browser_service.BrowserService, "_do_login"):
                        BrowserService.launch_and_login(
                            "https://example.invalid",
                            page_load_wait=0,
                        )

        expected_window_call = call.set_window_rect(**FIXED_WINDOW_GEOMETRY)
        navigation_call = call.get("https://example.invalid")
        driver.set_window_rect.assert_called_once_with(**FIXED_WINDOW_GEOMETRY)
        self.assertLess(
            driver.method_calls.index(expected_window_call),
            driver.method_calls.index(navigation_call),
        )
        driver.get_window_rect.assert_called_once_with()

    def test_launch_rejects_unapplied_window_geometry_before_navigation(self):
        driver = MagicMock()
        mismatched_geometry = FIXED_WINDOW_GEOMETRY.copy()
        mismatched_geometry["width"] += 1
        driver.get_window_rect.return_value = mismatched_geometry

        with patch.multiple(
            browser_service,
            BROWSER_WINDOW_X=FIXED_WINDOW_GEOMETRY["x"],
            BROWSER_WINDOW_Y=FIXED_WINDOW_GEOMETRY["y"],
            BROWSER_WINDOW_WIDTH=FIXED_WINDOW_GEOMETRY["width"],
            BROWSER_WINDOW_HEIGHT=FIXED_WINDOW_GEOMETRY["height"],
        ), patch(
            "browser_service.create_driver_with_retry",
            return_value=driver,
        ), patch.object(browser_service.BrowserService, "_do_login"):
            with self.assertRaisesRegex(RuntimeError, "浏览器窗口.*不匹配"):
                browser_service.BrowserService.launch_and_login(
                    "https://example.invalid",
                    page_load_wait=0,
                )

        driver.get.assert_not_called()
        driver.quit.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
