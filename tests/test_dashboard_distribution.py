"""HTTP credentials, task isolation and display contracts for collection workers."""

import io
import json
import shutil
import subprocess
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import Mock, PropertyMock, patch

import web_dashboard
from db_manager import PatentsDB


class TestDistributionHttp(unittest.TestCase):
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

    def setUp(self):
        temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        self.patents = PatentsDB(Path(temporary_directory.name) / 'patents.db')
        for patcher in (
            patch.object(web_dashboard, '_patents_db', self.patents),
            patch.object(web_dashboard, 'api_token_matches', side_effect=lambda token: token == 'local-operator-token'),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        role_patcher = patch.object(web_dashboard, 'read_machine_role', return_value='master')
        self.machine_role = role_patcher.start()
        self.addCleanup(role_patcher.stop)
        web_dashboard.DashboardHandler.job_manager = web_dashboard.JobManager()
        self.operator_headers = {'X-CNIPA-Token': 'local-operator-token'}

    def request_json(self, path, method='GET', payload=None, headers=None):
        request = urllib.request.Request(
            self.endpoint + path,
            data=None if method == 'GET' else json.dumps(payload if payload is not None else {}).encode(),
            headers={'Content-Type': 'application/json', **(headers or {})}, method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as exception:
            content = exception.read()
            try:
                return exception.code, json.loads(content)
            except json.JSONDecodeError:
                return exception.code, {'error': 'HTTP error'}

    def create_assignment(self):
        status, created = self.request_json('/api/distribution/assignments', 'POST', {
            'collector': 'main', 'application_nos': ['2018108715138'], 'device_names': ['工作机 A'],
        }, self.operator_headers)
        self.assertEqual(status, 201, created)
        return created['assignments'][0]

    def worker_headers(self, assignment):
        return {'X-CNIPA-Task-Token': assignment['access_token'], 'X-CNIPA-Worker-ID': 'd' * 32}

    def test_local_operator_can_create_and_read_invitation_without_leaking_list_tokens(self):
        assignment = self.create_assignment()
        status, overview = self.request_json('/api/distribution', headers=self.operator_headers)
        self.assertEqual(status, 200)
        self.assertEqual(overview['role'], 'master')
        self.assertEqual(overview['assignments'][0]['task_id'], assignment['task_id'])
        self.assertNotIn(assignment['access_token'], json.dumps(overview))
        status, invitation = self.request_json(
            f"/api/distribution/assignments/{assignment['task_id']}/invitation", headers=self.operator_headers,
        )
        self.assertEqual(status, 200)
        self.assertEqual(invitation['access_token'], assignment['access_token'])

    def test_remote_worker_can_claim_report_and_deliver_without_operator_token(self):
        assignment = self.create_assignment()
        task_path = f"/api/worker/assignments/{assignment['task_id']}"
        credentials = self.worker_headers(assignment)
        with patch.object(web_dashboard.DashboardHandler, 'is_operator', new_callable=PropertyMock, return_value=False):
            status, claimed = self.request_json(task_path, headers=credentials)
            self.assertEqual(status, 200, claimed)
            self.assertNotIn(assignment['access_token'], json.dumps(claimed))
            self.assertEqual(self.request_json(task_path + '/progress', 'POST', {
                'state': 'collecting', 'completed': 0, 'succeeded': 0, 'failed': 0,
            }, credentials)[0], 200)
            status, delivered = self.request_json(task_path + '/results', 'POST', {
                'protocol_version': 1, 'application_no': '2018108715138', 'status': 'failed',
                'fields': {}, 'reason': '官网暂未返回详情',
            }, credentials)
        self.assertEqual(status, 200, delivered)
        self.assertEqual(delivered['receipt']['status'], 'failed')
        self.assertNotIn(assignment['access_token'], json.dumps(delivered))
        self.assertEqual(self.patents.list_collection_assignments()[0]['counts']['failed'], 1)

    def test_worker_credentials_are_required_on_every_worker_route(self):
        assignment = self.create_assignment()
        task_path = f"/api/worker/assignments/{assignment['task_id']}"
        for suffix, method in (('', 'GET'), ('/progress', 'POST'), ('/results', 'POST')):
            with self.subTest(suffix=suffix):
                status, _ = self.request_json(task_path + suffix, method, {}, self.operator_headers)
                self.assertEqual(status, 403)

    def test_task_token_cannot_authorize_administrator_or_similarly_named_routes(self):
        assignment = self.create_assignment()
        credentials = self.worker_headers(assignment)
        task_path = f"/api/worker/assignments/{assignment['task_id']}"
        for path in (
            '/api/jobs', '/api/distribution/assignments', task_path + '/results/extra',
            task_path + '/progress/', '/api/worker/assignments/not-a-task/results',
        ):
            with self.subTest(path=path):
                self.assertEqual(self.request_json(path, 'POST', {}, credentials)[0], 401)
        self.assertEqual(self.request_json('/api/worker/assignments/not-a-task', headers=credentials)[0], 404)

    def test_distribution_reads_require_local_operator_and_token(self):
        assignment = self.create_assignment()
        paths = (
            '/api/distribution', '/api/distribution/worker-tasks',
            f"/api/distribution/assignments/{assignment['task_id']}/invitation",
        )
        for path in paths:
            with self.subTest(path=path):
                self.assertEqual(self.request_json(path)[0], 403)
                with patch.object(web_dashboard.DashboardHandler, 'is_operator', new_callable=PropertyMock, return_value=False):
                    self.assertEqual(self.request_json(path, headers=self.operator_headers)[0], 403)

    def test_replica_can_list_local_worker_tasks_but_cannot_coordinate(self):
        assignment = self.create_assignment()
        self.machine_role.return_value = 'replica'
        self.assertEqual(self.request_json('/api/distribution', headers=self.operator_headers), (
            200, {'role': 'replica', 'assignments': []},
        ))
        with patch.object(web_dashboard, 'list_worker_assignments', return_value=[{'task_id': assignment['task_id']}]) as local_tasks:
            status, response = self.request_json('/api/distribution/worker-tasks', headers=self.operator_headers)
        self.assertEqual(status, 200)
        self.assertEqual(response['tasks'][0]['task_id'], assignment['task_id'])
        local_tasks.assert_called_once_with()
        for path, method, headers in (
            ('/api/distribution/assignments', 'POST', self.operator_headers),
            (f"/api/distribution/assignments/{assignment['task_id']}/invitation", 'GET', self.operator_headers),
            (f"/api/distribution/assignments/{assignment['task_id']}/cancel", 'POST', self.operator_headers),
            (f"/api/worker/assignments/{assignment['task_id']}", 'GET', self.worker_headers(assignment)),
            (f"/api/worker/assignments/{assignment['task_id']}/progress", 'POST', self.worker_headers(assignment)),
            (f"/api/worker/assignments/{assignment['task_id']}/results", 'POST', self.worker_headers(assignment)),
        ):
            with self.subTest(path=path):
                self.assertEqual(self.request_json(path, method, {}, headers)[0], 403)

    def test_validation_conflict_and_cancellation_have_actionable_http_status(self):
        self.assertEqual(self.request_json('/api/distribution/assignments', 'POST', {
            'collector': 'unsupported', 'application_nos': [], 'device_names': [],
        }, self.operator_headers)[0], 400)
        assignment = self.create_assignment()
        self.assertEqual(self.request_json('/api/distribution/assignments', 'POST', {
            'collector': 'main', 'application_nos': ['2018108715138'], 'device_names': ['重复工作机'],
        }, self.operator_headers)[0], 409)
        cancel_path = f"/api/distribution/assignments/{assignment['task_id']}/cancel"
        self.assertEqual(self.request_json(cancel_path, 'POST', {}, self.operator_headers), (200, {'ok': True}))
        task_path = f"/api/worker/assignments/{assignment['task_id']}"
        self.assertEqual(self.request_json(task_path, headers=self.worker_headers(assignment))[0], 409)

    def test_remote_operator_cannot_create_cancel_or_launch_local_worker(self):
        assignment = self.create_assignment()
        paths = (
            '/api/distribution/assignments', f"/api/distribution/assignments/{assignment['task_id']}/cancel",
        )
        with patch.object(web_dashboard.DashboardHandler, 'is_operator', new_callable=PropertyMock, return_value=False), patch.object(
            web_dashboard.DashboardHandler.job_manager, 'start',
        ) as start_job:
            for path in paths:
                self.assertEqual(self.request_json(path, 'POST', {}, self.operator_headers)[0], 403)
            for action in (
                'distributed_collect', 'distributed_resume', 'distributed_deliver',
                ' distributed_collect ', ' distributed_resume ', ' distributed_deliver ',
            ):
                self.assertEqual(self.request_json('/api/jobs', 'POST', {
                    'action': action, 'params': {},
                }, self.operator_headers)[0], 403)
        start_job.assert_not_called()


class TestDistributedJobSpecs(unittest.TestCase):
    def test_collection_credentials_never_enter_process_command_or_environment(self):
        task_id = 'a' * 32
        access_token = 'sensitive-task-token-for-test'
        with patch.object(web_dashboard, 'resolve_task_python', return_value='collection-python'), patch.object(
            web_dashboard, 'register_worker_assignment', return_value=task_id,
        ) as register:
            specification = web_dashboard.build_job_spec('distributed_collect', {
                'coordinator_url': 'http://192.168.1.10:8765', 'task_id': task_id, 'access_token': access_token,
            })
        register.assert_called_once_with('http://192.168.1.10:8765', task_id, access_token)
        self.assertEqual(specification['command'], ['collection-python', '-u', 'distributed_worker.py', 'run', '--task-id', task_id])
        self.assertEqual(specification['env']['CNIPA_DATA_DIR'], str(web_dashboard.WORKER_TASKS_DIR / task_id))
        self.assertEqual(specification['env']['USE_MITM_PROXY'], 'true')
        self.assertNotIn(access_token, json.dumps(specification))

    def test_delivery_requires_a_local_assignment_and_does_not_claim_desktop(self):
        task_id = 'b' * 32
        with patch.object(web_dashboard, 'resolve_task_python', return_value='collection-python'), patch.object(
            web_dashboard, 'list_worker_assignments', return_value=[{'task_id': task_id}],
        ):
            specification = web_dashboard.build_job_spec('distributed_deliver', {'task_id': task_id})
            for invalid in ('../data', 'c' * 32, None):
                with self.subTest(task_id=invalid), self.assertRaises(ValueError):
                    web_dashboard.build_job_spec('distributed_deliver', {'task_id': invalid})
        self.assertEqual(specification['command'], ['collection-python', '-u', 'distributed_worker.py', 'deliver', '--task-id', task_id])
        self.assertNotIn('distributed_deliver', web_dashboard.DESKTOP_BROWSER_ACTIONS)

    def test_registered_assignment_resumes_without_requesting_or_reregistering_credentials(self):
        task_id = 'b' * 32
        with patch.object(web_dashboard, 'resolve_task_python', return_value='collection-python'), patch.object(
            web_dashboard, 'list_worker_assignments', return_value=[{'task_id': task_id}],
        ), patch.object(web_dashboard, 'register_worker_assignment') as register:
            specification = web_dashboard.build_job_spec('distributed_resume', {'task_id': task_id})
            for invalid in ('../data', 'c' * 32, None):
                with self.subTest(task_id=invalid), self.assertRaises(ValueError):
                    web_dashboard.build_job_spec('distributed_resume', {'task_id': invalid})
        register.assert_not_called()
        self.assertEqual(specification['command'], ['collection-python', '-u', 'distributed_worker.py', 'run', '--task-id', task_id])
        self.assertEqual(specification['env']['CNIPA_DATA_DIR'], str(web_dashboard.WORKER_TASKS_DIR / task_id))
        self.assertIn('distributed_resume', web_dashboard.DESKTOP_BROWSER_ACTIONS)

    def test_busy_desktop_rejects_collection_before_persisting_an_invitation(self):
        jobs = web_dashboard.JobManager()
        jobs._jobs['existing'] = web_dashboard.Job('existing', 'collect_fwxx', '正在采集', ['python'])
        with patch.object(web_dashboard, 'register_worker_assignment') as register:
            for action in ('distributed_collect', 'distributed_resume'):
                with self.subTest(action=action), self.assertRaisesRegex(ValueError, '桌面浏览器'):
                    jobs.start(action, {'task_id': 'a' * 32})
        register.assert_not_called()


class TestDistributionBodyLimits(unittest.TestCase):
    def test_large_result_is_allowed_without_relaxing_regular_json_limit(self):
        contents = json.dumps({'fields': {'text': 'x' * (web_dashboard.MAX_BODY_BYTES + 1)}}).encode()
        request = object.__new__(web_dashboard.DashboardHandler)
        request.headers = {'Content-Length': str(len(contents))}
        request.rfile = io.BytesIO(contents)
        self.assertEqual(request.read_collection_transfer()['fields']['text'], 'x' * (web_dashboard.MAX_BODY_BYTES + 1))
        request.rfile = io.BytesIO(contents)
        with self.assertRaisesRegex(ValueError, '超过大小限制'):
            request.read_json_body()
        self.assertEqual(request.rfile.tell(), 0)

    def test_oversized_or_empty_result_is_rejected_before_reading(self):
        request = object.__new__(web_dashboard.DashboardHandler)
        request.rfile = Mock()
        for length in ('0', '-1', str(web_dashboard.MAX_COLLECTION_TRANSFER_BYTES + 1)):
            request.headers = {'Content-Length': length}
            with self.subTest(length=length), self.assertRaises(ValueError):
                request.read_collection_transfer()
        request.rfile.read.assert_not_called()


@unittest.skipUnless(shutil.which('node'), 'JavaScript runtime unavailable')
class TestDistributionRendering(unittest.TestCase):
    def test_untrusted_names_and_reasons_are_escaped_and_hidden_tab_does_not_poll(self):
        startup = """
const rendered = new Map();
global.localStorage = {getItem: () => ''};
global.document = {
  hidden: false,
  addEventListener: () => {},
  querySelector: selector => {
    if (!rendered.has(selector)) rendered.set(selector, {});
    return rendered.get(selector);
  },
};
global.fetch = () => { throw new Error('Inactive tab attempted a network request'); };
"""
        dashboard_js = web_dashboard.JS.rsplit('boot().catch', 1)[0]
        assertions = """
const assert = require('node:assert/strict');
const attack = '<img src=x onerror="throw 1">';
renderDistributionAssignments([{
  task_id: 'a'.repeat(32), device_name: attack, collector: 'fees', state: 'completed', last_seen: null,
  progress: {completed: 1}, counts: {total: 1, pending: 0, success: 0, failed: 1, interrupted: 0, conflict: 0, cancelled: 0},
  items: [{application_no: '2018108715138', status: 'failed', reason: attack}],
}]);
renderDistributionWorkerTasks([{
  task_id: 'a'.repeat(32), device_name: attack, collector: 'fees', state: 'delivery_pending',
  completed: 1, total: 1, acknowledged: 0, delivery_pending: 1, reason: attack,
  acknowledged_counts: {success: 0, failed: 0, interrupted: 0, conflict: 0},
}]);
for (const selector of ['#distributionRows', '#distributionWorkerRows']) {
  assert.ok(!rendered.get(selector).innerHTML.includes('<img'));
  assert.ok(rendered.get(selector).innerHTML.includes('&lt;img'));
}
state.currentTab = 'overview';
refreshDistribution().then(() => {
  state.currentTab = 'distribution'; document.hidden = true;
  return refreshDistribution();
}).catch(error => { console.error(error); process.exitCode = 1; });
"""
        completed = subprocess.run(
            [shutil.which('node')], input=startup + dashboard_js + assertions, text=True,
            capture_output=True, timeout=10, check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_registered_task_button_resumes_with_only_id_and_receipts_show_outcomes(self):
        startup = """
const rendered = new Map();
const events = new Map();
global.localStorage = {getItem: () => ''};
global.document = {
  addEventListener: () => {},
  querySelectorAll: () => [],
  getElementById: id => document.querySelector('#' + id),
  querySelector: selector => {
    if (!rendered.has(selector)) rendered.set(selector, {
      addEventListener: (event, callback) => events.set(selector + ':' + event, callback),
      scrollIntoView: () => {},
    });
    return rendered.get(selector);
  },
};
"""
        dashboard_js = web_dashboard.JS.rsplit('boot().catch', 1)[0]
        assertions = """
const assert = require('node:assert/strict');
const localTask = {
  task_id: 'b'.repeat(32), device_name: '工作机', collector: 'main', state: 'registered',
  completed: 0, total: 0, acknowledged: 0, delivery_pending: 0, reason: '',
  acknowledged_counts: {success: 0, failed: 0, interrupted: 0, conflict: 0},
};
renderDistributionWorkerTasks([localTask]);
const taskMarkup = rendered.get('#distributionWorkerRows').innerHTML;
assert.ok(taskMarkup.includes('data-resume-task="' + localTask.task_id + '"'));
assert.ok(taskMarkup.includes('继续接单'));
renderDistributionWorkerTasks([{
  ...localTask, state: 'delivered', completed: 4, total: 4, acknowledged: 4,
  acknowledged_counts: {success: 1, failed: 1, interrupted: 1, conflict: 1},
}]);
assert.ok(rendered.get('#distributionWorkerRows').innerHTML.includes('成功 1 · 失败 1 · 中断 1 · 冲突 1'));
const launches = [];
startJob = (action, params) => launches.push({action, params});
bindEvents();
events.get('#distributionWorkerRows:click')({target: {
  closest: selector => selector === 'button[data-resume-task]'
    ? {disabled: false, dataset: {resumeTask: localTask.task_id}} : null,
}});
assert.deepEqual(launches, [{action: 'distributed_resume', params: {task_id: localTask.task_id}}]);
renderDistributionWorkerTasks([{...localTask, state: 'rejected'}]);
assert.ok(!rendered.get('#distributionWorkerRows').innerHTML.includes('data-resume-task'));
assert.ok(rendered.get('#distributionWorkerRows').innerHTML.includes('主库拒绝接收'));
renderDistributionWorkerTasks([{...localTask, state: 'collecting'}]);
const recoveryMarkup = rendered.get('#distributionWorkerRows').innerHTML;
assert.ok(recoveryMarkup.includes('恢复回传'));
assert.ok(!recoveryMarkup.includes(' disabled'));
events.get('#distributionWorkerRows:click')({target: {
  closest: selector => selector === 'button[data-deliver-task]'
    ? {disabled: false, dataset: {deliverTask: localTask.task_id}} : null,
}});
assert.deepEqual(launches[1], {action: 'distributed_deliver', params: {task_id: localTask.task_id}});
const cancelledTask = {
  task_id: 'c'.repeat(32), device_name: '工作机 C', collector: 'fees', state: 'cancelled',
  last_seen: null, progress: {completed: 1},
  counts: {total: 2, pending: 0, success: 1, failed: 0, interrupted: 0, conflict: 0, cancelled: 1},
  items: [
    {application_no: '2018108715138', status: 'success', reason: ''},
    {application_no: '2024110065970', status: 'cancelled', reason: ''},
  ],
};
state.distributionAssignments = [cancelledTask];
renderDistributionAssignments([cancelledTask]);
const cancelledMarkup = rendered.get('#distributionRows').innerHTML;
assert.ok(cancelledMarkup.includes('任务已撤销'));
assert.ok(cancelledMarkup.includes('data-distribution-operation="retry">重分未成功项'));
showToast = () => {};
events.get('#distributionRows:click')({target: {
  closest: () => ({disabled: false, dataset: {distributionTask: cancelledTask.task_id, distributionOperation: 'retry'}}),
}});
assert.equal(rendered.get('#distributionAppNos').value, '2024110065970');
assert.equal(rendered.get('#distributionCollector').value, 'fees');
assert.equal(rendered.get('#distributionDeviceNames').value, '工作机 C');
"""
        completed = subprocess.run(
            [shutil.which('node')], input=startup + dashboard_js + assertions, text=True,
            capture_output=True, timeout=10, check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)


if __name__ == '__main__':
    unittest.main()
