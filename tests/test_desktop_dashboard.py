"""Desktop startup must bind to this checkout and one verified server lifetime."""

import http.client
import json
import os
import socket
import subprocess
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import Mock, patch

import desktop_dashboard
import web_dashboard


class DesktopConnectionTests(unittest.TestCase):
    def setUp(self):
        self.identity = {
            "application": "cnipa-patent-collector",
            "desktop_protocol": 2,
            "instance_id": "a" * 32,
            "project_directory": str(desktop_dashboard.BASE_DIR.resolve()),
            "pid": os.getpid(),
        }
        self.status_code = 200
        self.response_body = json.dumps(self.identity).encode()
        self.request_paths = []

    def start_status_server(self, port=0):
        test_case = self

        class DesktopStatusResponder(BaseHTTPRequestHandler):
            def do_GET(self):
                test_case.request_paths.append(self.path)
                self.send_response(test_case.status_code)
                if test_case.status_code == 302:
                    self.send_header("Location", "/untrusted-redirect")
                self.send_header("Content-Length", str(len(test_case.response_body)))
                self.end_headers()
                self.wfile.write(test_case.response_body)

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", port), DesktopStatusResponder)
        server_thread = threading.Thread(
            target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True,
        )
        server_thread.start()

        def close_server():
            server.shutdown()
            server.server_close()
            server_thread.join(timeout=5)

        self.addCleanup(close_server)
        return server.server_port

    def test_existing_project_service_is_reused_without_spawning_or_stopping(self):
        port = self.start_status_server()
        with patch.object(desktop_dashboard.subprocess, "Popen") as launch:
            connection = desktop_dashboard.connect_dashboard(port)
        self.assertEqual(connection.url, f"http://127.0.0.1:{port}/")
        self.assertEqual(connection.instance_id, self.identity["instance_id"])
        launch.assert_not_called()
        self.assertEqual(self.request_paths, ["/api/desktop-status"])
        self.assertEqual(desktop_dashboard.read_dashboard_identity(port), self.identity)

    def test_foreign_old_redirected_or_invalid_service_never_starts_another_server(self):
        port = self.start_status_server()
        foreign_identity = dict(self.identity, project_directory=str(desktop_dashboard.BASE_DIR / "other-checkout"))
        cases = [
            (200, json.dumps(foreign_identity).encode()),
            (200, json.dumps(dict(self.identity, application="another-application")).encode()),
            (200, json.dumps(dict(self.identity, desktop_protocol=1)).encode()),
            (200, json.dumps(dict(self.identity, instance_id=None)).encode()),
            (200, json.dumps(dict(self.identity, instance_id="a" * 31)).encode()),
            (200, json.dumps(dict(self.identity, instance_id="g" * 32)).encode()),
            (200, json.dumps(dict(self.identity, pid=True)).encode()),
            (200, json.dumps(dict(self.identity, pid=0)).encode()),
            (404, b"old dashboard without desktop protocol"),
            (302, json.dumps(self.identity).encode()),
            (200, b"not JSON"),
            (200, b"[]"),
            (200, b" " * 16385 + json.dumps(self.identity).encode()),
        ]
        for status_code, response_body in cases:
            with self.subTest(status_code=status_code, body=response_body[:80]):
                self.status_code = status_code
                self.response_body = response_body
                with patch.object(desktop_dashboard.subprocess, "Popen") as launch:
                    with self.assertRaises(RuntimeError):
                        desktop_dashboard.connect_dashboard(port)
                launch.assert_not_called()
        self.assertEqual(self.request_paths, ["/api/desktop-status"] * len(cases))

    def test_occupied_port_that_never_responds_is_not_treated_as_absent(self):
        listener = socket.socket()
        self.addCleanup(listener.close)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        with patch.object(desktop_dashboard.subprocess, "Popen") as launch:
            with self.assertRaises(RuntimeError):
                desktop_dashboard.connect_dashboard(listener.getsockname()[1])
        launch.assert_not_called()

    def test_refused_connection_starts_detached_server_then_verifies_http_identity(self):
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        child = Mock()

        def start_child(*args, **kwargs):
            self.start_status_server(port)
            return child

        with patch.object(desktop_dashboard.subprocess, "Popen", side_effect=start_child) as launch:
            connection = desktop_dashboard.connect_dashboard(port)
        self.assertEqual(connection.url, f"http://127.0.0.1:{port}/")
        self.assertEqual(connection.instance_id, self.identity["instance_id"])
        launch.assert_called_once()
        child.terminate.assert_not_called()
        child.kill.assert_not_called()
        self.assertEqual(self.request_paths, ["/api/desktop-status"])


class DesktopChildOwnershipTests(unittest.TestCase):
    def test_child_is_reaped_when_its_service_fails_identity_validation(self):
        child = Mock()
        child.poll.return_value = None
        with (
            patch.object(desktop_dashboard, "read_dashboard_identity", side_effect=[None, RuntimeError("wrong project")]),
            patch.object(desktop_dashboard.subprocess, "Popen", return_value=child),
        ):
            with self.assertRaisesRegex(RuntimeError, "wrong project") as raised:
                desktop_dashboard.connect_dashboard(8765)
        self.assertIn(str(desktop_dashboard.DASHBOARD_SERVICE_LOG_FILE), str(raised.exception))
        child.terminate.assert_called_once()
        child.wait.assert_called_once()
        child.kill.assert_not_called()

    def test_unresponsive_owned_child_is_killed_after_graceful_termination_timeout(self):
        child = Mock()
        child.poll.return_value = None
        child.wait.side_effect = [subprocess.TimeoutExpired("test desktop child", 3), 0]
        with (
            patch.object(desktop_dashboard, "read_dashboard_identity", side_effect=[None, RuntimeError("startup failed")]),
            patch.object(desktop_dashboard.subprocess, "Popen", return_value=child),
        ):
            with self.assertRaisesRegex(RuntimeError, "startup failed"):
                desktop_dashboard.connect_dashboard(8765)
        child.terminate.assert_called_once()
        child.kill.assert_called_once()
        self.assertEqual(child.wait.call_count, 2)

    def test_child_exit_reports_failure_without_signalling_another_process(self):
        child = Mock(returncode=7)
        child.poll.return_value = 7
        with (
            patch.object(desktop_dashboard, "read_dashboard_identity", return_value=None),
            patch.object(desktop_dashboard.subprocess, "Popen", return_value=child),
        ):
            with self.assertRaisesRegex(RuntimeError, "7"):
                desktop_dashboard.connect_dashboard(8765)
        child.terminate.assert_not_called()
        child.kill.assert_not_called()

    def test_startup_deadline_reaps_only_the_child_started_here(self):
        child = Mock()
        child.poll.return_value = None
        with (
            patch.object(desktop_dashboard, "read_dashboard_identity", return_value=None),
            patch.object(desktop_dashboard.subprocess, "Popen", return_value=child),
            patch.object(desktop_dashboard.time, "monotonic", side_effect=[0, 0, 21]),
            patch.object(desktop_dashboard.time, "sleep"),
        ):
            with self.assertRaisesRegex(RuntimeError, "超时"):
                desktop_dashboard.connect_dashboard(8765)
        child.terminate.assert_called_once()
        child.wait.assert_called_once()

    def test_concurrent_launcher_can_reuse_winning_service_without_terminating_it(self):
        child = Mock(returncode=1)
        child.poll.return_value = 1
        with (
            patch.object(desktop_dashboard, "read_dashboard_identity", side_effect=[None, {"pid": 12345, "instance_id": "a" * 32}]),
            patch.object(desktop_dashboard.subprocess, "Popen", return_value=child),
        ):
            connection = desktop_dashboard.connect_dashboard(8765)
        self.assertEqual(connection.url, "http://127.0.0.1:8765/")
        self.assertEqual(connection.instance_id, "a" * 32)
        child.terminate.assert_not_called()
        child.kill.assert_not_called()

    def test_child_lifetime_is_independent_of_desktop_terminal_on_each_platform(self):
        for platform in ("darwin", "win32"):
            with self.subTest(platform=platform):
                with (
                    patch.object(desktop_dashboard.sys, "platform", platform),
                    patch.object(desktop_dashboard.subprocess, "DETACHED_PROCESS", 8, create=True),
                    patch.object(desktop_dashboard.subprocess, "CREATE_NEW_PROCESS_GROUP", 512, create=True),
                    patch.object(desktop_dashboard, "read_dashboard_identity", side_effect=[None, {"pid": 12345, "instance_id": "a" * 32}]),
                    patch.object(desktop_dashboard.subprocess, "Popen") as launch,
                ):
                    desktop_dashboard.connect_dashboard(8765)
                launch_options = launch.call_args.kwargs
                self.assertEqual(launch_options["stdin"], subprocess.DEVNULL)
                self.assertEqual(launch_options["stdout"], subprocess.DEVNULL)
                self.assertEqual(launch_options["stderr"], subprocess.DEVNULL)
                self.assertTrue(launch_options["close_fds"])
                if platform == "win32":
                    self.assertEqual(launch_options["creationflags"] & 520, 520)
                    self.assertNotIn("start_new_session", launch_options)
                else:
                    self.assertTrue(launch_options["start_new_session"])
                    self.assertNotIn("creationflags", launch_options)


class DesktopStatusEndpointTests(unittest.TestCase):
    def start_dashboard_server(self, server_type=web_dashboard.DashboardHTTPServer):
        server = server_type(("127.0.0.1", 0), web_dashboard.DashboardHandler)
        server_thread = threading.Thread(
            target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True,
        )
        server_thread.start()

        def close_server():
            server.shutdown()
            server.server_close()
            server_thread.join(timeout=5)

        self.addCleanup(close_server)
        return server.server_port

    def test_loopback_endpoint_matches_current_project_and_running_pid(self):
        port = self.start_dashboard_server()
        identity = desktop_dashboard.read_dashboard_identity(port)
        self.assertEqual(identity["application"], "cnipa-patent-collector")
        self.assertEqual(identity["desktop_protocol"], 2)
        self.assertRegex(identity["instance_id"], r"^[0-9a-f]{32}$")
        self.assertEqual(identity["project_directory"], str(web_dashboard.BASE_DIR.resolve()))
        self.assertEqual(identity["pid"], os.getpid())

    def test_remote_peer_cannot_read_project_directory_or_pid(self):
        class RemotePeerServer(web_dashboard.DashboardHTTPServer):
            def get_request(self):
                connection, peer = super().get_request()
                return connection, ("192.0.2.25", peer[1])

        port = self.start_dashboard_server(RemotePeerServer)
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
        self.addCleanup(connection.close)
        connection.request("GET", "/api/desktop-status", headers={"X-Forwarded-For": "127.0.0.1"})
        response = connection.getresponse()
        self.assertEqual(response.status, 403)
        error_payload = json.loads(response.read())
        self.assertIn("error", error_payload)
        self.assertNotIn("project_directory", error_payload)
        self.assertNotIn("pid", error_payload)


if __name__ == "__main__":
    unittest.main()
