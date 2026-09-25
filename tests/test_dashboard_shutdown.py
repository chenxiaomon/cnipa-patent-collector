"""Desktop exit drains owned jobs and cannot target another server instance."""

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

import web_dashboard


class DashboardShutdownFixture(unittest.TestCase):
    def setUp(self):
        temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        self.log_directory = Path(temporary_directory.name)
        log_patch = patch.object(web_dashboard, "DASHBOARD_JOB_LOG_DIR", self.log_directory)
        log_patch.start()
        self.addCleanup(log_patch.stop)
        self.job_manager = web_dashboard.JobManager()
        self.addCleanup(self.stop_remaining_jobs)

    def stop_remaining_jobs(self):
        for job in self.job_manager._jobs.values():
            self.job_manager.stop(job.id)
        for job in self.job_manager._jobs.values():
            if job._termination_thread is not None:
                job._termination_thread.join(timeout=15)
            self.assertTrue(job._completed.wait(15), "test subprocess did not finish")

    def start_waiting_job(self, action, finalization_delay=0):
        shutdown_order = self.log_directory / "shutdown-order.txt"
        script = f"""
import signal
import time
from pathlib import Path

running = True
def stop(signum, frame):
    global running
    print('TERM_RECEIVED', flush=True)
    running = False

signal.signal(signal.SIGTERM, stop)
print('READY', flush=True)
try:
    while running:
        time.sleep(0.01)
finally:
    time.sleep({finalization_delay!r})
    with Path({str(shutdown_order)!r}).open('a', encoding='utf-8') as stream:
        stream.write({action!r} + '\\n')
    print('FINALIZED', flush=True)
"""
        with patch.object(web_dashboard, "build_job_spec", return_value={
            "action": action, "title": action, "command": [sys.executable, "-u", "-c", script],
        }):
            job = self.job_manager.start(action, {})
        deadline = time.monotonic() + 5
        while not any(line.endswith("READY") for line in job.to_dict(True)["logs"]):
            self.assertLess(time.monotonic(), deadline, "test subprocess did not become ready")
            time.sleep(0.01)
        return job


class DashboardShutdownTests(DashboardShutdownFixture):
    @unittest.skipIf(os.name == "nt", "POSIX descendants retain inherited stdout after root exit")
    def test_root_exit_does_not_hide_descendant_owning_stdout(self):
        child_script = """
import os, signal, sys, time
running = True
def stop(signum, frame):
    global running
    running = False
signal.signal(signal.SIGTERM, stop)
while os.getppid() == int(sys.argv[1]):
    time.sleep(0.01)
print('ROOT_EXITED', flush=True)
try:
    while running:
        time.sleep(0.01)
finally:
    print('DESCENDANT_FINALIZED', flush=True)
"""
        root_script = (
            "import os, subprocess, sys; "
            f"subprocess.Popen([sys.executable, '-u', '-c', {child_script!r}, str(os.getpid())])"
        )
        with patch.object(web_dashboard, "build_job_spec", return_value={
            "action": "log_test", "title": "已退出根进程", "command": [sys.executable, "-u", "-c", root_script],
        }):
            job = self.job_manager.start("log_test", {})
        deadline = time.monotonic() + 5
        while not any(line.endswith("ROOT_EXITED") for line in job.to_dict(True)["logs"]):
            self.assertLess(time.monotonic(), deadline, "root process did not exit")
            time.sleep(0.01)
        self.assertFalse(job._completed.is_set())
        self.assertIsNone(job.process.returncode)

        self.job_manager.shutdown_jobs()

        self.assertTrue(job._completed.is_set())
        self.assertEqual(job.status, "stopped")
        self.assertIn("DESCENDANT_FINALIZED", (self.log_directory / job.log_filename).read_text(encoding="utf-8"))

    def test_completed_history_keeps_job_until_termination_thread_finishes(self):
        job = self.start_waiting_job("log_test")
        termination_finished = threading.Event()
        release_termination = threading.Event()
        original_terminate = web_dashboard.terminate_process_tree

        def hold_termination_thread(child):
            original_terminate(child)
            termination_finished.set()
            release_termination.wait(5)

        with patch.object(web_dashboard, "terminate_process_tree", side_effect=hold_termination_thread):
            self.job_manager.stop(job.id)
            try:
                self.assertTrue(termination_finished.wait(5))
                self.assertTrue(job._completed.wait(5))
                with patch.object(web_dashboard, "MAX_COMPLETED_JOBS", 0):
                    visible_ids = {entry["id"] for entry in self.job_manager.list_jobs()}
                self.assertIn(job.id, visible_ids)
                self.assertIs(self.job_manager.get_job(job.id), job)
            finally:
                release_termination.set()
        self.job_manager.shutdown_jobs()

    def test_shutdown_drains_final_logs_and_leaves_unowned_process_running(self):
        unrelated = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        self.addCleanup(unrelated.wait, timeout=5)
        self.addCleanup(unrelated.terminate)
        job = self.start_waiting_job("log_test", finalization_delay=0.1)

        self.job_manager.shutdown_jobs()
        self.job_manager.shutdown_jobs()

        self.assertEqual(job.status, "stopped")
        self.assertTrue(job._completed.is_set())
        self.assertIsNotNone(job.finished_at)
        self.assertIsNotNone(job.process.poll())
        self.assertIsNone(job._log_writer)
        self.assertIsNone(unrelated.poll())
        saved_log = (self.log_directory / job.log_filename).read_text(encoding="utf-8")
        self.assertIn("READY", saved_log)
        if os.name != "nt":
            # Windows taskkill /F cannot execute the child's Python finally.
            self.assertIn("FINALIZED", saved_log)
        self.assertIn("状态: stopped", saved_log)
        with patch.object(web_dashboard, "build_job_spec") as build_spec:
            with self.assertRaisesRegex(web_dashboard.DashboardShutdownConflict, "不能启动新任务"):
                self.job_manager.start("log_test", {})
        build_spec.assert_not_called()

    def test_proxy_shutdown_waits_for_collection_finalization(self):
        main_proxy = self.start_waiting_job("mitm_proxy")
        public_proxy = self.start_waiting_job("public_mitm_proxy")
        first_collection = self.start_waiting_job("main_test", finalization_delay=0.2)
        second_collection = self.start_waiting_job("log_test", finalization_delay=0.2)
        started_stops = []
        original_stop = self.job_manager.stop

        def record_stop(job_id):
            started_stops.append(job_id)
            if job_id == second_collection.id and os.name != "nt":
                self.assertFalse(first_collection._completed.is_set())
            if job_id in {main_proxy.id, public_proxy.id}:
                for collection_job in (first_collection, second_collection):
                    self.assertTrue(collection_job._completed.is_set())
                    self.assertIsNotNone(collection_job.process.poll())
                    self.assertIsNone(collection_job._log_writer)
            return original_stop(job_id)

        with patch.object(self.job_manager, "stop", side_effect=record_stop):
            self.job_manager.shutdown_jobs()

        if os.name != "nt":
            shutdown_order = (self.log_directory / "shutdown-order.txt").read_text(encoding="utf-8").splitlines()
            self.assertEqual(set(shutdown_order[:2]), {"main_test", "log_test"})
            self.assertEqual(set(shutdown_order[2:]), {"mitm_proxy", "public_mitm_proxy"})
        self.assertEqual(started_stops[:2], [first_collection.id, second_collection.id])
        self.assertEqual(set(started_stops[2:]), {main_proxy.id, public_proxy.id})
        for proxy_job in (main_proxy, public_proxy):
            self.assertTrue(proxy_job._completed.is_set())
            self.assertIsNotNone(proxy_job.process.poll())

    def test_manual_stop_and_concurrent_shutdown_send_termination_only_once(self):
        job = self.start_waiting_job("log_test", finalization_delay=0.1)
        termination_started = threading.Event()
        allow_termination = threading.Event()
        original_terminate = web_dashboard.terminate_process_tree

        def delayed_termination(child):
            termination_started.set()
            self.assertTrue(allow_termination.wait(5))
            original_terminate(child)

        with patch.object(web_dashboard, "terminate_process_tree", side_effect=delayed_termination) as terminate:
            self.assertTrue(self.job_manager.stop(job.id))
            self.assertTrue(termination_started.wait(5))
            closing_threads = [threading.Thread(target=self.job_manager.shutdown_jobs) for _ in range(2)]
            for closing_thread in closing_threads:
                closing_thread.start()
            self.assertTrue(self.job_manager.stop(job.id))
            allow_termination.set()
            for closing_thread in closing_threads:
                closing_thread.join(timeout=10)
                self.assertFalse(closing_thread.is_alive())
            self.assertEqual(terminate.call_count, 1)
        self.assertTrue(job._completed.is_set())
        if os.name != "nt":
            self.assertEqual(sum(line.endswith("TERM_RECEIVED") for line in job.to_dict(True)["logs"]), 1)

    def test_maintenance_rejects_shutdown_without_stopping_tasks_or_closing_start_gate(self):
        for action in ("upgrade_code", "fetch_update", "db_rebuild"):
            with self.subTest(action=action):
                maintenance_job = self.start_waiting_job(action)
                with self.assertRaisesRegex(web_dashboard.DashboardShutdownConflict, "完成后再关闭"):
                    self.job_manager.shutdown_jobs()
                self.assertIsNone(maintenance_job.process.poll())
                temporary_job = self.start_waiting_job("log_test")
                self.job_manager.stop(temporary_job.id)
                self.job_manager.stop(maintenance_job.id)
                self.assertTrue(temporary_job._completed.wait(10))
                self.assertTrue(maintenance_job._completed.wait(10))

    def test_terminal_exit_waits_for_maintenance_to_finish(self):
        maintenance_job = self.start_waiting_job("db_rebuild")
        collection_job = self.start_waiting_job("log_test")
        closing_thread = threading.Thread(target=self.job_manager.finish_jobs_before_exit)
        closing_thread.start()
        try:
            deadline = time.monotonic() + 5
            while not self.job_manager._shutdown_started:
                self.assertLess(time.monotonic(), deadline)
                time.sleep(0.01)
            self.assertIsNone(maintenance_job.process.poll())
            self.assertIsNone(collection_job.process.poll())
            self.assertTrue(closing_thread.is_alive())
            self.job_manager.stop(maintenance_job.id)
            closing_thread.join(timeout=10)
            self.assertFalse(closing_thread.is_alive())
            self.assertTrue(collection_job._completed.is_set())
        finally:
            self.job_manager.stop(maintenance_job.id)
            closing_thread.join(timeout=10)

    def test_failed_termination_is_reported_and_can_be_retried(self):
        job = self.start_waiting_job("log_test")
        with patch.object(web_dashboard, "terminate_process_tree", side_effect=OSError("permission denied")):
            with self.assertRaisesRegex(RuntimeError, "permission denied"):
                self.job_manager.shutdown_jobs()
        self.assertIsNone(job.process.poll())
        self.job_manager.shutdown_jobs()
        self.assertTrue(job._completed.is_set())
        self.assertIsNone(job._termination_error)

    def test_missing_finalization_has_bounded_wait(self):
        job = self.start_waiting_job("log_test")
        with patch.object(web_dashboard, "DESKTOP_SHUTDOWN_TIMEOUT_SECONDS", 0.1), patch.object(
            web_dashboard, "terminate_process_tree", return_value=None,
        ):
            started = time.monotonic()
            with self.assertRaisesRegex(RuntimeError, "保存日志超时"):
                self.job_manager.shutdown_jobs()
            self.assertLess(time.monotonic() - started, 2)
        self.job_manager.shutdown_jobs()
        self.assertTrue(job._completed.is_set())


class DesktopShutdownHttpTests(DashboardShutdownFixture):
    def setUp(self):
        super().setUp()
        owned_jobs = self.job_manager

        class ShutdownRequest(web_dashboard.DashboardHandler):
            job_manager = owned_jobs
            test_peer = None

            def setup(self):
                if self.test_peer is not None:
                    self.client_address = (self.test_peer, self.client_address[1])
                super().setup()

        self.request_class = ShutdownRequest
        self.server = web_dashboard.DashboardHTTPServer(("127.0.0.1", 0), ShutdownRequest)
        self.server_thread = threading.Thread(target=self.server.serve_forever)
        self.server_thread.start()
        self.addCleanup(self.stop_server)
        token_patch = patch.object(web_dashboard, "api_token_matches", side_effect=lambda token: token == "valid-token")
        token_patch.start()
        self.addCleanup(token_patch.stop)
        self.base_url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.server_thread.join(timeout=5)

    def request_shutdown(self, token="valid-token", instance_id=None):
        request = urllib.request.Request(
            self.base_url + "/api/desktop-shutdown",
            data=json.dumps({"instance_id": instance_id or self.server.instance_id}).encode(),
            headers={"Content-Type": "application/json", "X-CNIPA-Token": token},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as rejected:
            return rejected.code, json.loads(rejected.read())

    def test_status_and_shutdown_use_the_same_server_instance(self):
        job = self.start_waiting_job("log_test")
        with urllib.request.urlopen(self.base_url + "/api/desktop-status", timeout=5) as response:
            desktop_status = json.loads(response.read())
        self.assertEqual(desktop_status["desktop_protocol"], 2)
        self.assertRegex(desktop_status["instance_id"], r"^[0-9a-f]{32}$")
        status, shutdown_receipt = self.request_shutdown()
        self.assertEqual(status, 200)
        self.assertEqual(shutdown_receipt, {"stopped": True, "instance_id": desktop_status["instance_id"]})
        self.assertTrue(job._completed.is_set())
        self.assertIsNone(job._log_writer)
        self.server_thread.join(timeout=5)
        self.assertFalse(self.server_thread.is_alive())

    def test_invalid_token_remote_peer_and_stale_instance_have_no_side_effects(self):
        job = self.start_waiting_job("log_test")
        self.assertEqual(self.request_shutdown(token="")[0], 403)
        self.request_class.test_peer = "192.0.2.10"
        try:
            self.assertEqual(self.request_shutdown()[0], 403)
        finally:
            self.request_class.test_peer = None
        self.assertEqual(self.request_shutdown(instance_id="0" * 32)[0], 409)
        self.assertIsNone(job.process.poll())
        self.assertFalse(self.job_manager._shutdown_started)
        self.assertTrue(self.server_thread.is_alive())

    def test_protected_maintenance_returns_conflict_and_preserves_server(self):
        job = self.start_waiting_job("db_rebuild")
        status, rejection = self.request_shutdown()
        self.assertEqual(status, 409)
        self.assertIn("完成后再关闭", rejection["error"])
        self.assertEqual(rejection["reason"], "maintenance_running")
        self.assertIsNone(job.process.poll())
        self.assertTrue(self.server_thread.is_alive())

    def test_failed_termination_returns_error_and_keeps_server_available_for_retry(self):
        job = self.start_waiting_job("log_test")
        with patch.object(web_dashboard, "terminate_process_tree", side_effect=OSError("permission denied")):
            status, rejection = self.request_shutdown()
        self.assertEqual(status, 500)
        self.assertIn("permission denied", rejection["error"])
        self.assertIsNone(job.process.poll())
        self.assertTrue(self.server_thread.is_alive())
        self.assertEqual(self.request_shutdown()[0], 200)


if __name__ == "__main__":
    unittest.main()
