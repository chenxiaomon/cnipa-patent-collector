#!/usr/bin/env python3
"""Command-level behavior for manual detail coordinate recording."""

from __future__ import annotations

import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from unittest.mock import MagicMock, patch

import record_detail_coordinates
from coordinate_service import CoordinateConfigurationError


class TestRecordDetailCoordinatesCommand(unittest.TestCase):
    CALIBRATION_URL = "https://example.invalid/cnipa"
    RECORDERS = {
        "detail-link": "record_detail_link_coordinate_from_pointer",
        "fwxx-menu": "record_fwxx_menu_coordinate_from_pointer",
        "fee-menu": "record_fee_menu_coordinate_from_pointer",
    }

    def setUp(self) -> None:
        browser_service_patch = patch.object(
            record_detail_coordinates,
            "BrowserService",
            create=True,
        )
        self.browser_service = browser_service_patch.start()
        self.addCleanup(browser_service_patch.stop)

        cnipa_url_patch = patch.object(
            record_detail_coordinates,
            "CNIPA_URL",
            self.CALIBRATION_URL,
            create=True,
        )
        cnipa_url_patch.start()
        self.addCleanup(cnipa_url_patch.stop)

        desktop_reservation_patch = patch.object(
            record_detail_coordinates, "reserve_detail_collection_desktop", create=True,
        )
        self.desktop_reservation = desktop_reservation_patch.start()
        self.addCleanup(desktop_reservation_patch.stop)

        self.driver = MagicMock()
        self.browser_service.launch_and_login.return_value = self.driver

    def test_each_target_launches_collection_browser_before_recording_and_closes(self):
        for target, operation_name in self.RECORDERS.items():
            events = []
            driver = MagicMock()
            self.browser_service.reset_mock()
            self.browser_service.launch_and_login.side_effect = (
                lambda url: events.append(("launch", url)) or driver
            )
            self.browser_service.close_automation_browser.side_effect = (
                lambda active_driver: events.append(("close", active_driver))
            )
            with self.subTest(target=target), patch.object(
                record_detail_coordinates.CoordinateService,
                operation_name,
                side_effect=lambda: events.append(("record",)) or (40, 30),
            ) as record_coordinate, redirect_stdout(StringIO()) as stdout:
                exit_code = record_detail_coordinates.main([target])

            self.assertEqual(exit_code, 0)
            self.assertIn("\u5df2\u4fdd\u5b58\u8be6\u60c5\u9875\u5750\u6807", stdout.getvalue())
            self.assertEqual(
                events,
                [
                    ("launch", self.CALIBRATION_URL),
                    ("record",),
                    ("close", driver),
                ],
            )
            record_coordinate.assert_called_once_with()
            self.browser_service.close_automation_browser.assert_called_once_with(
                driver
            )
            driver.quit.assert_not_called()

    def test_coordinate_error_returns_two(self):
        stderr = StringIO()
        with patch.object(
            record_detail_coordinates.CoordinateService,
            "record_detail_link_coordinate_from_pointer",
            side_effect=CoordinateConfigurationError("\u5750\u6807\u65e0\u6548"),
        ), redirect_stderr(stderr):
            exit_code = record_detail_coordinates.main(["detail-link"])

        self.assertEqual(exit_code, 2)
        self.assertIn("\u8be6\u60c5\u9875\u5750\u6807\u6821\u51c6\u5931\u8d25", stderr.getvalue())
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
            record_detail_coordinates.CoordinateService,
            "record_fee_menu_coordinate_from_pointer",
            side_effect=OSError("\u5750\u6807\u914d\u7f6e\u65e0\u6cd5\u5199\u5165"),
        ), redirect_stderr(stderr):
            exit_code = record_detail_coordinates.main(["fee-menu"])

        self.assertEqual(exit_code, 2)
        self.assertIn("\u5750\u6807\u914d\u7f6e\u65e0\u6cd5\u5199\u5165", stderr.getvalue())
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
            "\u6d4f\u89c8\u5668\u542f\u52a8\u5931\u8d25"
        )
        with patch.object(
            record_detail_coordinates.CoordinateService,
            "record_detail_link_coordinate_from_pointer",
        ) as record_coordinate, redirect_stderr(stderr):
            exit_code = record_detail_coordinates.main(["detail-link"])

        self.assertEqual(exit_code, 2)
        self.assertIn("\u6d4f\u89c8\u5668\u542f\u52a8\u5931\u8d25", stderr.getvalue())
        record_coordinate.assert_not_called()
        self.browser_service.close_automation_browser.assert_not_called()
        self.driver.quit.assert_not_called()

    def test_busy_desktop_prevents_browser_startup(self):
        self.desktop_reservation.return_value.__enter__.side_effect = RuntimeError("desktop busy")
        with patch.object(
            record_detail_coordinates.CoordinateService,
            "record_fee_menu_coordinate_from_pointer", return_value=(40, 30),
        ), redirect_stderr(StringIO()) as stderr, redirect_stdout(StringIO()):
            exit_code = record_detail_coordinates.main(["fee-menu"])
        self.assertEqual(exit_code, 2)
        self.assertIn("desktop busy", stderr.getvalue())
        self.browser_service.launch_and_login.assert_not_called()

    def test_desktop_is_reserved_until_browser_is_closed(self):
        events = []
        self.desktop_reservation.return_value.__enter__.side_effect = lambda: events.append("reserve")
        self.desktop_reservation.return_value.__exit__.side_effect = lambda *args: events.append("release")
        self.browser_service.close_automation_browser.side_effect = lambda driver: events.append("close")
        with patch.object(
            record_detail_coordinates.CoordinateService,
            "record_fee_menu_coordinate_from_pointer", return_value=(40, 30),
        ), redirect_stdout(StringIO()):
            self.assertEqual(record_detail_coordinates.main(["fee-menu"]), 0)
        self.assertEqual(events, ["reserve", "close", "release"])


if __name__ == "__main__":
    unittest.main()
