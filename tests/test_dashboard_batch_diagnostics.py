"""HTTP and launch contracts for durable collection batches and diagnostics."""

import json
import threading
import unittest
import urllib.error
import urllib.request
from unittest.mock import PropertyMock, patch

import web_dashboard


class TestBatchLaunch(unittest.TestCase):
    def test_each_batch_resumes_its_original_collector_with_saved_id(self):
        batch_id = 'a' * 32
        with patch.object(web_dashboard, 'resolve_task_python', return_value='collection-python'):
            for collector, script in (
                ('main', 'main_automation.py'), ('fwxx', 'collect_fwxx.py'), ('fees', 'collect_fees.py'),
            ):
                with self.subTest(collector=collector), patch.object(web_dashboard, 'read_collection_batch', return_value={
                    'id': batch_id, 'collector': collector, 'resumable': True,
                }) as read_batch:
                    specification = web_dashboard.build_job_spec('resume_collection_batch', {'batch_id': batch_id})
                read_batch.assert_called_once_with(batch_id)
                self.assertEqual(specification['command'], ['collection-python', '-u', script, '--resume-batch', batch_id])
                self.assertEqual(specification['env']['USE_MITM_PROXY'], 'true')
        self.assertIn('resume_collection_batch', web_dashboard.DESKTOP_BROWSER_ACTIONS)

    def test_running_or_completed_batch_is_rejected_before_launch(self):
        with patch.object(web_dashboard, 'read_collection_batch', return_value={'resumable': False}):
            with self.assertRaisesRegex(ValueError, '不能续跑'):
                web_dashboard.build_job_spec('resume_collection_batch', {'batch_id': 'a' * 32})


class TestBatchAndDiagnosticsHttp(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = web_dashboard.ThreadingHTTPServer(('127.0.0.1', 0), web_dashboard.DashboardHandler)
        cls.server_thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.server_thread.start()
        cls.endpoint = f'http://127.0.0.1:{cls.server.server_address[1]}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.server_thread.join(timeout=5)

    def request_json(self, path, method='GET'):
        request = urllib.request.Request(
            self.endpoint + path, data=b'{}' if method == 'POST' else None,
            headers={'Content-Type': 'application/json'}, method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as exception:
            return exception.code, json.loads(exception.read())

    def test_batch_reads_use_persisted_owner_after_job_list_restarts(self):
        batch_id = 'a' * 32
        saved_batch = {'id': batch_id, 'status': 'interrupted', 'remaining': 2}
        web_dashboard.DashboardHandler.job_manager = web_dashboard.JobManager()
        with patch.object(web_dashboard, 'list_collection_batches', return_value=[saved_batch]), patch.object(
            web_dashboard, 'read_collection_batch', return_value=saved_batch,
        ) as read_batch:
            self.assertEqual(self.request_json('/api/collection-batches'), (200, {'batches': [saved_batch]}))
            self.assertEqual(self.request_json('/api/collection-batches/' + batch_id), (200, {'batch': saved_batch}))
        read_batch.assert_called_once_with(batch_id)

    def test_untrusted_remote_batch_reads_require_operator_token(self):
        with patch.object(web_dashboard.DashboardHandler, 'is_operator', new_callable=PropertyMock, return_value=False), patch.object(
            web_dashboard, 'api_token_matches', return_value=False,
        ), patch.object(web_dashboard, 'list_collection_batches') as read_batches:
            self.assertEqual(self.request_json('/api/collection-batches')[0], 403)
        read_batches.assert_not_called()

    def test_invalid_batch_id_is_rejected_by_owner(self):
        self.assertEqual(self.request_json('/api/collection-batches/not-a-batch')[0], 400)

    def test_diagnostics_check_selected_collection_python(self):
        report = {'generated_at': '2026-09-06T00:00:00Z', 'checks': []}
        with patch.object(web_dashboard, 'api_token_matches', return_value=True), patch.object(
            web_dashboard, 'resolve_task_python', return_value='company-venv-python',
        ), patch.object(web_dashboard, 'run_environment_diagnostics', return_value=report) as diagnose:
            self.assertEqual(self.request_json('/api/environment-diagnostics', 'POST'), (200, report))
        diagnose.assert_called_once_with('company-venv-python')

    def test_diagnostics_reject_unauthenticated_request_before_probing(self):
        with patch.object(web_dashboard, 'api_token_matches', return_value=False), patch.object(
            web_dashboard, 'run_environment_diagnostics',
        ) as diagnose:
            self.assertEqual(self.request_json('/api/environment-diagnostics', 'POST')[0], 401)
        diagnose.assert_not_called()

    def test_diagnostics_reject_overlapping_requests(self):
        with web_dashboard._ENVIRONMENT_DIAGNOSTICS_LOCK, patch.object(
            web_dashboard, 'api_token_matches', return_value=True,
        ), patch.object(web_dashboard, 'run_environment_diagnostics') as diagnose:
            self.assertEqual(self.request_json('/api/environment-diagnostics', 'POST')[0], 409)
        diagnose.assert_not_called()

    def test_failed_diagnostics_release_the_reservation_for_next_request(self):
        with patch.object(web_dashboard, 'api_token_matches', return_value=True), patch.object(
            web_dashboard, 'run_environment_diagnostics', side_effect=[RuntimeError('probe failed'), {'checks': []}],
        ):
            self.assertEqual(self.request_json('/api/environment-diagnostics', 'POST')[0], 500)
            self.assertEqual(self.request_json('/api/environment-diagnostics', 'POST'), (200, {'checks': []}))


if __name__ == '__main__':
    unittest.main()
