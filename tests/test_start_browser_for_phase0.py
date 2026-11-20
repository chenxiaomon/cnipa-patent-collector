#!/usr/bin/env python3
"""Phase 0 browsing is only useful when CNIPA traffic passes through the main MITM proxy."""

import unittest
from unittest import mock

import start_browser_for_phase0
import web_dashboard


class TestPhase0BrowserProxy(unittest.TestCase):
    def test_cli_refuses_to_open_browser_without_proxy_routing(self):
        with mock.patch.object(start_browser_for_phase0, "USE_MITM_PROXY", False), mock.patch.object(
            start_browser_for_phase0, "raise_system_exit_on_sigterm"
        ), mock.patch.object(
            start_browser_for_phase0, "check_mitm_proxy", return_value=True
        ) as check_proxy, mock.patch.object(
            start_browser_for_phase0.BrowserService, "launch_and_login",
            side_effect=RuntimeError("browser must not launch without proxy routing"),
        ) as launch_browser:
            with self.assertRaises(SystemExit) as stopped:
                start_browser_for_phase0.run_phase0_browser_session()

        self.assertEqual(stopped.exception.code, 1)
        check_proxy.assert_not_called()
        launch_browser.assert_not_called()

    def test_cli_opens_browser_when_proxy_routing_is_enabled(self):
        driver = mock.MagicMock()
        type(driver).window_handles = mock.PropertyMock(side_effect=RuntimeError("browser closed"))
        with mock.patch.object(start_browser_for_phase0, "USE_MITM_PROXY", True), mock.patch.object(
            start_browser_for_phase0, "raise_system_exit_on_sigterm"
        ), mock.patch.object(
            start_browser_for_phase0, "check_mitm_proxy", return_value=True
        ), mock.patch.object(
            start_browser_for_phase0.BrowserService, "launch_and_login", return_value=driver
        ) as launch_browser:
            start_browser_for_phase0.run_phase0_browser_session()

        launch_browser.assert_called_once_with(start_browser_for_phase0.CNIPA_URL)
        driver.quit.assert_called_once_with()

    def test_dashboard_launches_phase0_browser_through_proxy(self):
        with mock.patch.object(web_dashboard, "resolve_task_python", return_value="python"):
            spec = web_dashboard.build_job_spec("phase0_browser", {})

        self.assertEqual(spec["command"], ["python", "-u", "start_browser_for_phase0.py"])
        self.assertEqual(spec["env"]["USE_MITM_PROXY"], "true")


if __name__ == "__main__":
    unittest.main()
