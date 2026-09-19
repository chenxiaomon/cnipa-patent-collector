#!/usr/bin/env python3
"""Command-level behavior for manual search coordinate recording."""

from __future__ import annotations

import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from unittest.mock import MagicMock, patch

import record_search_coordinates
from coordinate_service import CoordinateConfigurationError


class TestRecordSearchCoordinatesCommand(unittest.TestCase):
    CALIBRATION_URL = "https://example.invalid/cnipa"

    def setUp(self) -> None:
        browser_service_patch = patch.object(
            record_search_coordinates,
            "BrowserService",
            create=True,
        )
        self.browser_service = browser_service_patch.start()
        self.addCleanup(browser_service_patch.stop)

        cnipa_url_patch = patch.object(
            record_search_coordinates,
            "CNIPA_URL",
            self.CALIBRATION_URL,
            create=True,
        )
        cnipa_url_patch.start()
        self.addCleanup(cnipa_url_patch.stop)

        desktop_reservation_patch = patch.object(
            record_search_coordinates, "reserve_detail_collection_desktop", create=True,
        )
        self.desktop_reservation = desktop_reservation_patch.start()
        self.addCleanup(desktop_reservation_patch.stop)

        self.driver = MagicMock()
        self.browser_service.launch_and_login.return_value = self.driver

    def test_success_launches_collection_browser_before_recording_and_closes(self):
        stdout = StringIO()
        events = []
        self.browser_service.launch_and_login.side_effect = (
            lambda url: events.append(("launch", url)) or self.driver
        )
        self.browser_service.close_automation_browser.side_effect = (
            lambda driver: events.append(("close", driver))
        )
        with patch.object(
            record_search_coordinates.CoordinateService,
            "record_search_coordinates_from_pointer",
            side_effect=lambda: events.append(("record",)) or (40, 30, 120, 80),
        ) as record_coordinates, redirect_stdout(stdout):
            exit_code = record_search_coordinates.main()

        self.assertEqual(exit_code, 0)
        self.assertIn("已保存搜索页坐标", stdout.getvalue())
        self.assertEqual(
            events,
            [
                ("launch", self.CALIBRATION_URL),
                ("record",),
                ("close", self.driver),
            ],
        )
        record_coordinates.assert_called_once_with()
        self.browser_service.close_automation_browser.assert_called_once_with(
            self.driver
        )
        self.driver.quit.assert_not_called()

    def test_coordinate_error_returns_two(self):
        stderr = StringIO()
        with patch.object(
            record_search_coordinates.CoordinateService,
            "record_search_coordinates_from_pointer",
            side_effect=CoordinateConfigurationError("坐标无效"),
        ), redirect_stderr(stderr):
            exit_code = record_search_coordinates.main()

        self.assertEqual(exit_code, 2)
        self.assertIn("搜索页坐标校准失败", stderr.getvalue())
        self.browser_service.launch_and_login.assert_called_once_with(
            self.CALIBRATION_URL
        )
        self.browser_service.close_automation_browser.assert_called_once_with(
            self.driver
        )
        self.driver.quit.assert_not_called()

    def test_os_error_returns_two(self):
        stderr = StringIO()
        with patch.object(
            record_search_coordinates.CoordinateService,
            "record_search_coordinates_from_pointer",
            side_effect=OSError("屏幕无法读取"),
        ), redirect_stderr(stderr):
            exit_code = record_search_coordinates.main()

        self.assertEqual(exit_code, 2)
        self.assertIn("屏幕无法读取", stderr.getvalue())
        self.browser_service.launch_and_login.assert_called_once_with(
            self.CALIBRATION_URL
        )
        self.browser_service.close_automation_browser.assert_called_once_with(
            self.driver
        )
        self.driver.quit.assert_not_called()

    def test_browser_launch_error_returns_two_without_quitting_missing_driver(self):
        stderr = StringIO()
        self.browser_service.launch_and_login.side_effect = RuntimeError(
            "浏览器启动失败"
        )
        with patch.object(
            record_search_coordinates.CoordinateService,
            "record_search_coordinates_from_pointer",
        ) as record_coordinates, redirect_stderr(stderr):
            exit_code = record_search_coordinates.main()

        self.assertEqual(exit_code, 2)
        self.assertIn("浏览器启动失败", stderr.getvalue())
        record_coordinates.assert_not_called()
        self.browser_service.close_automation_browser.assert_not_called()
        self.driver.quit.assert_not_called()

    def test_busy_desktop_prevents_browser_startup(self):
        self.desktop_reservation.return_value.__enter__.side_effect = RuntimeError("desktop busy")
        with patch.object(
            record_search_coordinates.CoordinateService,
            "record_search_coordinates_from_pointer", return_value=(40, 30, 120, 80),
        ), redirect_stderr(StringIO()) as stderr, redirect_stdout(StringIO()):
            exit_code = record_search_coordinates.main()
        self.assertEqual(exit_code, 2)
        self.assertIn("desktop busy", stderr.getvalue())
        self.browser_service.launch_and_login.assert_not_called()

    def test_desktop_is_reserved_until_browser_is_closed(self):
        events = []
        self.desktop_reservation.return_value.__enter__.side_effect = lambda: events.append("reserve")
        self.desktop_reservation.return_value.__exit__.side_effect = lambda *args: events.append("release")
        self.browser_service.close_automation_browser.side_effect = lambda driver: events.append("close")
        with patch.object(
            record_search_coordinates.CoordinateService,
            "record_search_coordinates_from_pointer", return_value=(40, 30, 120, 80),
        ), redirect_stdout(StringIO()):
            self.assertEqual(record_search_coordinates.main(), 0)
        self.assertEqual(events, ["reserve", "close", "release"])


if __name__ == "__main__":
    unittest.main()
