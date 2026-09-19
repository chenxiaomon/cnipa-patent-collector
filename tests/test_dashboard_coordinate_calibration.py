#!/usr/bin/env python3
"""Dashboard contracts for safe offline coordinate calibration."""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

import web_dashboard


SEARCH_CONFIG = {
    "input_x": 10,
    "input_y": 20,
    "button_x": 30,
    "button_y": 40,
    "window_geometry": {"x": 0, "y": 0, "width": 1440, "height": 900},
    "screen_geometry": {"width": 1920, "height": 1080},
}
DETAIL_CONFIG = {
    "link_x": 50,
    "link_y": 60,
    "fwxx_menu_x": 70,
    "fwxx_menu_y": 80,
    "fee_menu_x": 90,
    "fee_menu_y": 100,
    "window_geometry": {"x": 0, "y": 0, "width": 1440, "height": 900},
    "screen_geometry": {"width": 1920, "height": 1080},
}


class TestCoordinateCalibrationJobSpecs(unittest.TestCase):
    def test_search_coordinate_job_uses_manual_pointer_recorder(self):
        with mock.patch.object(web_dashboard, "resolve_task_python", return_value="python"):
            job_spec = web_dashboard.build_job_spec("calibrate_search", {})

        self.assertEqual(
            job_spec["command"],
            ["python", "-u", "record_search_coordinates.py"],
        )
        self.assertIn("calibrate_search", web_dashboard.DESKTOP_BROWSER_ACTIONS)

    def test_detail_coordinate_jobs_use_manual_pointer_recorder(self):
        expected_targets = {
            "calibrate_detail_link": "detail-link",
            "calibrate_fwxx_menu": "fwxx-menu",
            "calibrate_fee_menu": "fee-menu",
        }

        with mock.patch.object(web_dashboard, "resolve_task_python", return_value="python"):
            for action, target in expected_targets.items():
                with self.subTest(action=action):
                    job_spec = web_dashboard.build_job_spec(action, {})
                    self.assertEqual(
                        job_spec["command"],
                        ["python", "-u", "record_detail_coordinates.py", target],
                    )
                    self.assertNotIn("--capture", job_spec["command"])
                    self.assertIn(action, web_dashboard.DESKTOP_BROWSER_ACTIONS)

    def test_calibration_does_not_wait_for_a_mitm_proxy(self):
        for action in (
            "calibrate_search", "calibrate_detail_link",
            "calibrate_fwxx_menu", "calibrate_fee_menu",
        ):
            with self.subTest(action=action):
                recorder_child = mock.Mock()
                recorder_child.stdin = None
                recorder_child.stdout = mock.Mock()
                recorder_child.poll.return_value = None
                recorder_jobs = web_dashboard.JobManager()

                with mock.patch.object(
                    web_dashboard.subprocess, "Popen", return_value=recorder_child,
                ) as popen, mock.patch.object(
                    web_dashboard.threading, "Thread",
                ), mock.patch.object(
                    web_dashboard, "port_open",
                ) as check_proxy, mock.patch.dict(
                    web_dashboard.os.environ,
                    {"USE_MITM_PROXY": "true", "USE_VIRTUAL_DISPLAY": "true"},
                ):
                    recorder_jobs.start(action, {})

                popen.assert_called_once()
                check_proxy.assert_not_called()
                child_environment = popen.call_args.kwargs["env"]
                self.assertEqual(child_environment["USE_MITM_PROXY"], "false")
                self.assertEqual(child_environment["USE_VIRTUAL_DISPLAY"], "false")
                self.assertEqual(
                    child_environment["CNIPA_LOGIN_WAIT_SECONDS"],
                    web_dashboard.DEFAULT_LOGIN_WAIT_SECONDS,
                )

    def test_calibration_excludes_collection_and_code_updates_in_both_directions(self):
        for calibration_action in (
            "calibrate_search", "calibrate_detail_link",
            "calibrate_fwxx_menu", "calibrate_fee_menu",
        ):
            for competing_action in ("main_full", "upgrade_code", "calibrate_search"):
                for active_action, requested_action in (
                    (calibration_action, competing_action),
                    (competing_action, calibration_action),
                ):
                    with self.subTest(active=active_action, requested=requested_action):
                        recorder_jobs = web_dashboard.JobManager()
                        active_job = web_dashboard.Job(
                            id="active-job", action=active_action,
                            title=active_action, command=["python"],
                        )
                        recorder_jobs._jobs[active_job.id] = active_job

                        with mock.patch.object(
                            web_dashboard, "build_job_spec",
                        ) as build_spec, mock.patch.object(
                            web_dashboard.subprocess, "Popen",
                        ) as popen:
                            with self.assertRaises(ValueError):
                                recorder_jobs.start(requested_action, {})

                        build_spec.assert_not_called()
                        popen.assert_not_called()


class TestCoordinateConfigurationApi(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        temporary_path = Path(self.temporary_directory.name)
        self.search_path = temporary_path / "config.json"
        self.detail_path = temporary_path / "config_fwxx.json"
        self.search_path.write_text(json.dumps(SEARCH_CONFIG), encoding="utf-8")
        self.detail_path.write_text(json.dumps(DETAIL_CONFIG), encoding="utf-8")

        self.patches = (
            mock.patch.object(web_dashboard, "CONFIG_FILE", self.search_path),
            mock.patch.object(web_dashboard, "CONFIG_FWXX_FILE", self.detail_path),
            mock.patch.object(web_dashboard, "api_token_matches", return_value=True),
        )
        for active_patch in self.patches:
            active_patch.start()
            self.addCleanup(active_patch.stop)

        web_dashboard.DashboardHandler.job_manager = web_dashboard.JobManager()
        self.server = web_dashboard.ThreadingHTTPServer(
            ("127.0.0.1", 0), web_dashboard.DashboardHandler
        )
        self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.server_thread.start()
        self.addCleanup(self._stop_server)
        self.base_url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def _stop_server(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.server_thread.join(timeout=5)

    def _request_json(
        self,
        path: str,
        *,
        payload: dict | None = None,
    ) -> tuple[int, dict]:
        request = urllib.request.Request(
            self.base_url + path,
            data=None if payload is None else json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="GET" if payload is None else "POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read().decode("utf-8"))

    def test_direct_coordinate_write_is_disabled(self):
        status, response = self._request_json(
            "/api/config",
            payload={
                "search_text": "{}",
                "detail_text": "{}",
            },
        )

        self.assertEqual(status, 405)
        self.assertIn("\u5750\u6807\u6821\u51c6", response["error"])
        self.assertEqual(json.loads(self.search_path.read_text(encoding="utf-8")), SEARCH_CONFIG)
        self.assertEqual(json.loads(self.detail_path.read_text(encoding="utf-8")), DETAIL_CONFIG)

    def test_coordinate_reset_is_disabled(self):
        status, response = self._request_json("/api/config/reset", payload={})

        self.assertEqual(status, 405)
        self.assertIn("\u5750\u6807\u6821\u51c6", response["error"])
        self.assertEqual(json.loads(self.search_path.read_text(encoding="utf-8")), SEARCH_CONFIG)
        self.assertEqual(json.loads(self.detail_path.read_text(encoding="utf-8")), DETAIL_CONFIG)


class TestCoordinateConfigurationPage(unittest.TestCase):
    def test_page_exposes_local_calibration_without_destructive_edit_controls(self):
        for action in (
            "calibrate_search",
            "calibrate_detail_link",
            "calibrate_fwxx_menu",
            "calibrate_fee_menu",
        ):
            self.assertIn(f'data-action="{action}"', web_dashboard.HTML)

        self.assertNotIn('id="saveConfig"', web_dashboard.HTML)
        self.assertNotIn('id="resetConfig"', web_dashboard.HTML)
        self.assertNotIn("\u4e0b\u6b21\u5bf9\u5e94\u91c7\u96c6\u4f1a\u91cd\u65b0\u8bb0\u5f55", web_dashboard.JS)
        self.assertIn('id="configText" class="codebox" readonly', web_dashboard.HTML)
        self.assertIn('id="fwxxConfigText" class="codebox" readonly', web_dashboard.HTML)
        self.assertNotIn("$('#saveConfig')", web_dashboard.JS)
        self.assertNotIn("$('#resetConfig')", web_dashboard.JS)
