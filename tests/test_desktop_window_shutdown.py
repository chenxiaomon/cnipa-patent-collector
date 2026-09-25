"""Closing a native window must finish its bound backend without touching a replacement."""

import io
import json
import os
import socket
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import Mock, patch

import desktop_dashboard


class DesktopShutdownConnectionTests(unittest.TestCase):
    def setUp(self):
        self.instance_id = "a" * 32
        self.identity = {
            "application": "cnipa-patent-collector",
            "desktop_protocol": 2,
            "instance_id": self.instance_id,
            "project_directory": str(desktop_dashboard.BASE_DIR.resolve()),
            "pid": os.getpid(),
        }
        self.token_payload = {"token": "local-test-token"}
        self.token_status = 200
        self.shutdown_receipt = {"stopped": True, "instance_id": self.instance_id}
        self.shutdown_status = 200
        self.request_paths = []
        self.posted_shutdowns = []
        self.before_receipt = lambda: None
        self.after_receipt = lambda: None

    def start_shutdown_server(self):
        test_case = self

        class DesktopShutdownResponder(BaseHTTPRequestHandler):
            def write_reply(self, payload, status):
                reply_bytes = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Length", str(len(reply_bytes)))
                self.end_headers()
                self.wfile.write(reply_bytes)
                self.wfile.flush()

            def do_GET(self):
                test_case.request_paths.append(self.path)
                if self.path == "/api/desktop-status":
                    self.write_reply(test_case.identity, 200)
                elif self.path == "/api/operator-token":
                    self.write_reply(test_case.token_payload, test_case.token_status)
                else:
                    self.write_reply({"error": "unexpected request"}, 404)

            def do_POST(self):
                test_case.request_paths.append(self.path)
                test_case.posted_shutdowns.append((
                    self.headers.get("X-CNIPA-Token"),
                    json.loads(self.rfile.read(int(self.headers["Content-Length"]))),
                ))
                test_case.before_receipt()
                self.write_reply(test_case.shutdown_receipt, test_case.shutdown_status)
                test_case.after_receipt()

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), DesktopShutdownResponder)

        def serve_until_shutdown():
            try:
                server.serve_forever(poll_interval=0.01)
            finally:
                server.server_close()

        server_thread = threading.Thread(target=serve_until_shutdown, daemon=True)
        server_thread.start()

        def close_server():
            server.shutdown()
            server_thread.join(timeout=5)
            server.server_close()

        self.addCleanup(close_server)
        return server

    def test_shutdown_authenticates_binds_instance_and_waits_for_listener_to_close(self):
        server = self.start_shutdown_server()
        self.after_receipt = server.shutdown
        connection = desktop_dashboard.DashboardConnection(server.server_port, self.instance_id)

        connection.shutdown()

        self.assertEqual(self.request_paths[:3], [
            "/api/desktop-status", "/api/operator-token", "/api/desktop-shutdown",
        ])
        self.assertEqual(self.posted_shutdowns, [("local-test-token", {"instance_id": self.instance_id})])
        self.assertIsNone(desktop_dashboard.read_dashboard_identity(server.server_port))
        connection.shutdown()
        self.assertEqual(len(self.posted_shutdowns), 1)

    def test_absent_backend_already_counts_as_closed_without_spawning_a_replacement(self):
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        with patch.object(desktop_dashboard.subprocess, "Popen") as launch:
            desktop_dashboard.DashboardConnection(port, self.instance_id).shutdown()
        launch.assert_not_called()

    def test_old_window_does_not_request_token_or_shutdown_a_replacement_instance(self):
        server = self.start_shutdown_server()
        self.identity["instance_id"] = "b" * 32

        desktop_dashboard.DashboardConnection(server.server_port, self.instance_id).shutdown()

        self.assertEqual(self.request_paths, ["/api/desktop-status"])
        self.assertEqual(self.posted_shutdowns, [])
        self.assertEqual(desktop_dashboard.read_dashboard_identity(server.server_port), self.identity)

    def test_rejected_shutdown_preserves_backend_and_reports_failure(self):
        server = self.start_shutdown_server()
        connection = desktop_dashboard.DashboardConnection(server.server_port, self.instance_id)
        for status in (409, 500):
            with self.subTest(status=status):
                self.shutdown_status = status
                self.shutdown_receipt = {"error": "任务未退出"}
                with self.assertRaisesRegex(RuntimeError, "任务未退出"):
                    connection.shutdown()
                self.assertEqual(desktop_dashboard.read_dashboard_identity(server.server_port), self.identity)

    def test_maintenance_response_preserves_backend_and_requests_a_later_shutdown(self):
        server = self.start_shutdown_server()
        self.shutdown_status = 409
        self.shutdown_receipt = {"reason": "maintenance_running", "error": "数据库维护尚未结束"}

        with self.assertRaisesRegex(desktop_dashboard.DashboardMaintenanceBusy, "数据库维护尚未结束"):
            desktop_dashboard.DashboardConnection(server.server_port, self.instance_id).shutdown()

        self.assertEqual(desktop_dashboard.read_dashboard_identity(server.server_port), self.identity)
        self.assertEqual(len(self.posted_shutdowns), 1)

    def test_failed_receipt_after_instance_replacement_does_not_stop_the_new_backend(self):
        server = self.start_shutdown_server()
        self.shutdown_status = 409
        self.shutdown_receipt = {"error": "控制台已重新启动"}

        def replace_instance():
            self.identity["instance_id"] = "b" * 32

        self.before_receipt = replace_instance
        connection = desktop_dashboard.DashboardConnection(server.server_port, self.instance_id)
        connection.shutdown()
        connection.shutdown()

        self.assertEqual(self.posted_shutdowns, [("local-test-token", {"instance_id": self.instance_id})])
        self.assertEqual(desktop_dashboard.read_dashboard_identity(server.server_port), self.identity)

    def test_invalid_operator_credentials_never_send_shutdown(self):
        server = self.start_shutdown_server()
        connection = desktop_dashboard.DashboardConnection(server.server_port, self.instance_id)
        for token_payload in ({}, [], None, b"not JSON"):
            with self.subTest(token_payload=token_payload):
                self.token_payload = token_payload
                with self.assertRaises(RuntimeError):
                    connection.shutdown()
        self.assertEqual(self.posted_shutdowns, [])

    def test_invalid_or_unrelated_shutdown_receipt_is_not_accepted_as_success(self):
        server = self.start_shutdown_server()
        connection = desktop_dashboard.DashboardConnection(server.server_port, self.instance_id)
        receipts = (
            [], None, b"not JSON", {}, {"stopped": False, "instance_id": self.instance_id},
            {"stopped": True}, {"stopped": True, "instance_id": "b" * 32},
        )
        for receipt in receipts:
            with self.subTest(receipt=receipt):
                self.shutdown_receipt = receipt
                with self.assertRaises(RuntimeError):
                    connection.shutdown()
                self.assertEqual(desktop_dashboard.read_dashboard_identity(server.server_port), self.identity)

    def test_success_receipt_does_not_hide_a_backend_that_keeps_listening(self):
        server = self.start_shutdown_server()
        connection = desktop_dashboard.DashboardConnection(server.server_port, self.instance_id)
        with (
            patch.object(desktop_dashboard.time, "monotonic", side_effect=[0, 0, 6]),
            patch.object(desktop_dashboard.time, "sleep"),
        ):
            with self.assertRaisesRegex(RuntimeError, "端口尚未关闭"):
                connection.shutdown()
        self.assertEqual(desktop_dashboard.read_dashboard_identity(server.server_port), self.identity)


class ClosingEvent:
    def __init__(self):
        self.callbacks = []

    def __iadd__(self, callback):
        self.callbacks.append(callback)
        return self

    def emit(self):
        return all(callback() is not False for callback in self.callbacks)


class DesktopWindowShutdownTests(unittest.TestCase):
    def setUp(self):
        self.connection = Mock(url="http://127.0.0.1:8765/")
        self.closing_event = ClosingEvent()
        self.window = SimpleNamespace(
            events=SimpleNamespace(closing=self.closing_event),
            create_confirmation_dialog=Mock(),
        )
        self.webview = SimpleNamespace(
            settings={}, create_window=Mock(return_value=self.window), start=Mock(),
        )
        self.stderr = io.StringIO()
        for patcher in (
            patch.dict("sys.modules", {"webview": self.webview}),
            patch.object(desktop_dashboard.sys, "argv", ["desktop_dashboard.py"]),
            patch.object(desktop_dashboard.sys, "platform", "darwin"),
            patch.object(desktop_dashboard.sys, "stderr", self.stderr),
            patch.object(desktop_dashboard, "connect_dashboard", return_value=self.connection),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_window_close_finishes_backend_before_allowing_the_window_to_disappear(self):
        def close_window(**start_options):
            self.assertTrue(self.closing_event.emit())
            self.connection.shutdown.assert_called_once()

        self.webview.start.side_effect = close_window

        self.assertEqual(desktop_dashboard.main(), 0)

        self.assertEqual(self.connection.shutdown.call_count, 2)
        self.window.create_confirmation_dialog.assert_not_called()

    def test_cmd_q_without_a_closing_event_still_finishes_backend(self):
        self.assertEqual(desktop_dashboard.main(), 0)
        self.connection.shutdown.assert_called_once()

    def test_cmd_q_during_maintenance_waits_then_finishes_backend_without_a_visible_window(self):
        self.connection.shutdown.side_effect = [
            desktop_dashboard.DashboardMaintenanceBusy("数据库维护尚未结束"),
            desktop_dashboard.DashboardMaintenanceBusy("数据库维护尚未结束"),
            None,
        ]

        with patch.object(desktop_dashboard.time, "sleep") as wait_for_maintenance:
            self.assertEqual(desktop_dashboard.main(), 0)

        self.assertEqual(self.connection.shutdown.call_count, 3)
        self.assertEqual(wait_for_maintenance.call_count, 2)
        self.assertTrue(all(wait.args == (1,) for wait in wait_for_maintenance.call_args_list))
        self.window.create_confirmation_dialog.assert_not_called()
        self.assertEqual(self.stderr.getvalue(), "")

    def test_failed_shutdown_keeps_window_open_and_allows_operator_to_retry(self):
        self.connection.shutdown.side_effect = [RuntimeError("采集仍在运行"), None, None]
        confirmation_finished = threading.Event()
        self.window.create_confirmation_dialog.side_effect = lambda *arguments: confirmation_finished.set()

        def retry_window_close(**start_options):
            self.assertFalse(self.closing_event.emit())
            self.assertTrue(confirmation_finished.wait(2), "shutdown failure did not display its explanation")
            self.window.create_confirmation_dialog.assert_called_once_with("暂时无法退出", "采集仍在运行")
            self.assertTrue(self.closing_event.emit())

        self.webview.start.side_effect = retry_window_close

        self.assertEqual(desktop_dashboard.main(), 0)
        self.assertEqual(self.connection.shutdown.call_count, 3)
        self.assertIn("尚未退出", self.stderr.getvalue())

    def test_failed_closing_returns_before_a_confirmation_dialog_is_dismissed(self):
        self.connection.shutdown.side_effect = [RuntimeError("采集仍在运行"), None]
        confirmation_opened = threading.Event()
        dismiss_confirmation = threading.Event()
        confirmation_finished = threading.Event()
        closing_returned = threading.Event()
        close_decisions = []

        def wait_for_confirmation(title, message):
            confirmation_opened.set()
            try:
                dismiss_confirmation.wait(5)
            finally:
                confirmation_finished.set()

        self.window.create_confirmation_dialog.side_effect = wait_for_confirmation

        def request_window_close():
            try:
                close_decisions.append(self.closing_event.emit())
            finally:
                closing_returned.set()

        def keep_ui_responsive(**start_options):
            closing_thread = threading.Thread(target=request_window_close, daemon=True)
            closing_thread.start()
            try:
                self.assertTrue(confirmation_opened.wait(2), "shutdown failure did not open a dialog")
                self.assertTrue(closing_returned.wait(1), "closing blocked while its dialog waited for the UI")
                self.assertEqual(close_decisions, [False])
                self.assertFalse(confirmation_finished.is_set())
            finally:
                dismiss_confirmation.set()
                closing_thread.join(timeout=2)
                self.assertTrue(confirmation_finished.wait(2), "confirmation dialog did not finish")
                self.assertFalse(closing_thread.is_alive())

        self.webview.start.side_effect = keep_ui_responsive

        self.assertEqual(desktop_dashboard.main(), 0, self.stderr.getvalue())
        self.window.create_confirmation_dialog.assert_called_once_with("暂时无法退出", "采集仍在运行")

    def test_backend_shutdown_failure_after_event_loop_exit_is_reported(self):
        self.connection.shutdown.side_effect = RuntimeError("采集仍在运行")

        self.assertEqual(desktop_dashboard.main(), 1)

        self.assertIn("采集仍在运行", self.stderr.getvalue())

    def test_failed_native_window_creation_also_finishes_connected_backend(self):
        self.webview.create_window.side_effect = RuntimeError("window unavailable")

        self.assertEqual(desktop_dashboard.main(), 1)

        self.connection.shutdown.assert_called_once()
        self.webview.start.assert_not_called()
        self.assertIn("window unavailable", self.stderr.getvalue())

    def test_native_event_loop_failure_also_finishes_connected_backend(self):
        self.webview.start.side_effect = RuntimeError("event loop unavailable")

        self.assertEqual(desktop_dashboard.main(), 1)

        self.connection.shutdown.assert_called_once()
        self.assertIn("event loop unavailable", self.stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
