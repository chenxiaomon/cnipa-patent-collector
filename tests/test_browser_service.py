#!/usr/bin/env python3
"""Browser lifecycle contracts owned by BrowserService."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from browser_service import BrowserService


class TestAutomationBrowserClosing(unittest.TestCase):
    def test_close_automation_browser_quits_exactly_once(self):
        driver = MagicMock()
        driver.keep_user_data_dir = False

        BrowserService.close_automation_browser(driver)

        driver.quit.assert_called_once_with()

    def test_successful_close_marks_uc_profile_cleanup_complete(self):
        driver = MagicMock()
        driver.keep_user_data_dir = False

        BrowserService.close_automation_browser(driver)

        self.assertTrue(driver.keep_user_data_dir)

    def test_failed_close_does_not_mark_uc_profile_cleanup_complete(self):
        driver = MagicMock()
        driver.keep_user_data_dir = False
        driver.quit.side_effect = OSError("browser close failed")

        with self.assertRaisesRegex(OSError, "browser close failed"):
            BrowserService.close_automation_browser(driver)

        driver.quit.assert_called_once_with()
        self.assertFalse(driver.keep_user_data_dir)


if __name__ == "__main__":
    unittest.main()
