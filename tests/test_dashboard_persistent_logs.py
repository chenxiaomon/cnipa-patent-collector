"""Task logs survive restarts; incremental polling preserves bounded output."""

import json
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from unittest.mock import PropertyMock, patch

import web_dashboard


class TestPersistentJobLogs(unittest.TestCase):
    def setUp(self):
        temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        self.log_directory = Path(temporary_directory.name)
        log_patch = patch.object(web_dashboard, 'DASHBOARD_JOB_LOG_DIR', self.log_directory)
        log_patch.start()
        self.addCleanup(log_patch.stop)

    def test_subprocess_output_and_final_status_survive_new_job_manager(self):
        job_manager = web_dashboard.JobManager()
        command = [sys.executable, '-u', '-c',
                   'import sys; print("normal output"); print("stderr evidence", file=sys.stderr); sys.exit(7)']
        with patch.object(web_dashboard, 'build_job_spec', return_value={
            'action': 'log_test', 'title': '日志测试', 'command': command,
        }):
            job = job_manager.start('log_test', {})
        deadline = time.monotonic() + 5
        while job_manager.list_jobs()[0]['status'] == 'running':
            self.assertLess(time.monotonic(), deadline, 'test subprocess did not finish')
            time.sleep(0.01)

        saved_text = (self.log_directory / job.log_filename).read_text(encoding='utf-8')
        self.assertIn('normal output', saved_text)
        self.assertIn('stderr evidence', saved_text)
        self.assertIn('状态: failed，退出码: 7', saved_text)
        self.assertRegex(saved_text, r'^\[\d{4}-\d{2}-\d{2}T')
        self.assertEqual(web_dashboard.JobManager().list_log_files()[0]['name'], job.log_filename)
        self.assertIsNone(job._log_writer)

    def test_secrets_are_redacted_in_both_memory_and_saved_output(self):
        job = web_dashboard.Job('0123456789', 'log_test', '测试', [])
        job.open_log()
        self.addCleanup(job.close_log)
        for line in (
            'https://example.test/query?token=secret-query&application_no=A',
            'Authorization: Bearer secret-auth',
            'Cookie: SESSION=secret-cookie; token=secret-cookie-token',
            '{"password": "secret-password", "access_token": "secret-access"}',
            '--password secret-cli',
        ):
            job.append(line)
        saved_text = (self.log_directory / job.log_filename).read_text(encoding='utf-8')
        self.assertNotIn('secret-', saved_text)
        self.assertNotIn('secret-', '\n'.join(job.to_dict(include_logs=True)['logs']))
        self.assertIn('application_no=A', saved_text)
        self.assertEqual(saved_text.count('[REDACTED]'), 6)

    def test_rotation_keeps_latest_evidence_and_limits_file_count(self):
        with patch.object(web_dashboard, 'DASHBOARD_JOB_LOG_MAX_BYTES', 220), patch.object(
            web_dashboard, 'DASHBOARD_JOB_LOG_BACKUP_COUNT', 2,
        ):
            job = web_dashboard.Job('0123456789', 'log_test', '测试', [])
            job.open_log()
            try:
                for index in range(30):
                    job.append(f'output-{index:03d} ' + 'x' * 40)
                self.assertEqual(len(web_dashboard.JobManager().list_log_files()), 3)
                self.assertIn('output-029', (self.log_directory / job.log_filename).read_text())
            finally:
                job.close_log()

    def test_retention_deletes_whole_old_task_but_preserves_active_task(self):
        job_manager = web_dashboard.JobManager()
        active_name = '20260101T000000Z-0000000000-log_test.log'
        active = web_dashboard.Job('0000000000', 'log_test', '测试', [], log_filename=active_name)
        job_manager._jobs[active.id] = active
        old_name = '20260102T000000Z-0000000001-log_test.log'
        new_name = '20260103T000000Z-0000000002-log_test.log'
        for filename in (active_name, old_name, old_name + '.1', new_name):
            (self.log_directory / filename).write_text('log evidence')
        with patch.object(web_dashboard, 'DASHBOARD_JOB_LOG_RETENTION_COUNT', 1):
            job_manager._prune_log_files_locked()
        self.assertEqual({item['name'] for item in job_manager.list_log_files()}, {active_name, new_name})

    def test_incremental_cursor_recovers_after_buffer_overflow(self):
        job = web_dashboard.Job('0123456789', 'log_test', '测试', [])
        for index in range(web_dashboard.MAX_LOG_LINES + 4):
            job.append(f'output-{index}')
        snapshot = job.read_logs(0)
        self.assertTrue(snapshot['logs_reset'])
        self.assertEqual(len(snapshot['logs']), web_dashboard.MAX_LOG_LINES)
        cursor = snapshot['log_cursor']
        self.assertEqual(job.read_logs(cursor)['logs'], [])
        job.append('[WAITING_FOR_LOGIN]')
        delta = job.read_logs(cursor)
        self.assertFalse(delta['logs_reset'])
        self.assertEqual(len(delta['logs']), 1)
        self.assertTrue(delta['waiting_for_login'])
        job.append('[LOGIN_CONFIRMED]')
        self.assertFalse(job.read_logs(delta['log_cursor'])['waiting_for_login'])
        self.assertTrue(job.read_logs(10 ** 12)['logs_reset'])


class TestJobLogHttp(unittest.TestCase):
    def setUp(self):
        temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        self.log_directory = Path(temporary_directory.name)
        log_patch = patch.object(web_dashboard, 'DASHBOARD_JOB_LOG_DIR', self.log_directory)
        log_patch.start()
        self.addCleanup(log_patch.stop)
        self.job_manager = web_dashboard.JobManager()
        jobs_patch = patch.object(web_dashboard.DashboardHandler, 'job_manager', self.job_manager, create=True)
        jobs_patch.start()
        self.addCleanup(jobs_patch.stop)
        self.server = web_dashboard.ThreadingHTTPServer(('127.0.0.1', 0), web_dashboard.DashboardHandler)
        self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.server_thread.start()
        self.addCleanup(self.stop_server)
        self.base_url = f'http://127.0.0.1:{self.server.server_address[1]}'

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.server_thread.join(timeout=5)

    def fetch(self, path):
        try:
            with urllib.request.urlopen(self.base_url + path, timeout=5) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()

    def test_saved_log_download_requires_operator_and_rejects_traversal(self):
        filename = '20260101T000000Z-0123456789-log_test.log'
        (self.log_directory / filename).write_text('saved evidence')
        download_path = '/api/job-logs/download?name=' + filename
        with patch.object(web_dashboard.DashboardHandler, 'is_operator', new_callable=PropertyMock, return_value=False), patch.object(
            web_dashboard, 'api_token_matches', return_value=False,
        ):
            self.assertEqual(self.fetch('/api/job-logs')[0], 403)
            self.assertEqual(self.fetch(download_path)[0], 403)
        self.assertEqual(self.fetch(download_path), (200, b'saved evidence'))
        self.assertEqual(self.fetch('/api/job-logs/download?name=..%2Fsecret.log')[0], 400)
        linked_name = '20260102T000000Z-0123456789-log_test.log'
        (self.log_directory / linked_name).symlink_to(self.log_directory / filename)
        self.assertEqual(self.fetch('/api/job-logs/download?name=' + linked_name)[0], 404)

    def test_job_endpoint_returns_only_unseen_lines_and_rejects_invalid_cursor(self):
        job = web_dashboard.Job('0123456789', 'log_test', '测试', [])
        job.append('first')
        job.append('second')
        self.job_manager._jobs[job.id] = job
        status, response_bytes = self.fetch('/api/jobs/' + job.id + '?after=1')
        self.assertEqual(status, 200)
        snapshot = json.loads(response_bytes)['job']
        self.assertEqual(len(snapshot['logs']), 1)
        self.assertTrue(snapshot['logs'][0].endswith('second'))
        self.assertEqual(self.fetch('/api/jobs/' + job.id + '?after=-1')[0], 400)


@unittest.skipUnless(shutil.which('node'), 'Node.js is needed to exercise browser polling')
class TestJobLogPolling(unittest.TestCase):
    def assert_polling_script(self, assertions):
        browser_script = web_dashboard.JS.rsplit('boot().catch', 1)[0]
        harness = r'''
const assert = require('node:assert/strict');
const elements = new Map();
global.localStorage = { getItem: () => '' };
global.document = {
  hidden: false,
  addEventListener: () => {},
  querySelector: selector => {
    if (!elements.has(selector)) elements.set(selector, {
      dataset: {}, textContent: '', innerHTML: '', scrollTop: 0, scrollHeight: 10,
      classList: { toggle(name, hidden) { this[name] = hidden; } },
    });
    return elements.get(selector);
  },
};
'''
        completed = subprocess.run(
            ['node', '-'], input=harness + browser_script + assertions,
            text=True, capture_output=True, timeout=10,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_hidden_unchanged_and_finished_logs_skip_downloads_but_login_banner_updates(self):
        self.assert_polling_script(r'''
(async () => {
  let logRequests = 0;
  const job = {id: 'one', status: 'running', log_cursor: 1, waiting_for_login: true};
  state.jobs = [job];
  state.selectedJobId = job.id;
  renderJobList = () => {};
  api = async route => {
    if (route === '/api/jobs') return {jobs: [job]};
    logRequests++;
    assert.ok(route.endsWith('?after=0') || route.endsWith('?after=1'));
    return {job: {...job, logs: ['line-' + job.log_cursor], logs_reset: false}};
  };
  await refreshJobs();
  assert.equal(logRequests, 0);
  assert.equal($('#loginBanner').classList.hidden, false);
  state.currentTab = 'logs';
  await refreshJobLog();
  assert.equal(logRequests, 1);
  await refreshJobLog();
  assert.equal(logRequests, 1);
  document.hidden = true;
  job.log_cursor = 2;
  await refreshJobLog();
  assert.equal(logRequests, 1);
  document.hidden = false;
  job.status = 'finished';
  job.waiting_for_login = false;
  await refreshJobs();
  assert.equal(logRequests, 2);
  assert.deepEqual(state.jobLogLines, ['line-1', 'line-2']);
  assert.equal($('#loginBanner').classList.hidden, true);
  await refreshJobLog();
  assert.equal(logRequests, 2);
})().catch(error => { console.error(error); process.exitCode = 1; });
''')

    def test_slow_job_lists_apply_while_a_newer_poll_is_still_pending(self):
        self.assert_polling_script(r'''
(async () => {
  const responses = [];
  const rendered = [];
  renderJobList = jobs => rendered.push(jobs[0].id);
  api = () => new Promise(resolve => responses.push(resolve));
  const polls = [refreshJobs(), refreshJobs()];
  for (let index = 0; index < 3; index++) {
    const job = {id: 'job-' + index, waiting_for_login: index % 2 === 0};
    responses[index]({jobs: [job]});
    await polls[index];
    assert.equal(state.jobs[0].id, job.id);
    assert.equal($('#loginBanner').classList.hidden, !job.waiting_for_login);
    polls.push(refreshJobs());
  }
  responses[3]({jobs: [{id: 'job-3', waiting_for_login: false}]});
  await polls[3];
  responses[4]({jobs: [{id: 'job-4', waiting_for_login: false}]});
  await polls[4];
  assert.deepEqual(rendered, ['job-0', 'job-1', 'job-2', 'job-3', 'job-4']);
})().catch(error => { console.error(error); process.exitCode = 1; });
''')

    def test_old_job_lists_and_slow_logs_cannot_override_new_login_state_or_selection(self):
        self.assert_polling_script(r'''
(async () => {
  const responses = [];
  renderJobList = () => {};
  api = () => new Promise(resolve => responses.push(resolve));
  const oldPoll = refreshJobs();
  const newPoll = refreshJobs();
  const newestJob = {id: 'newest', waiting_for_login: true};
  responses[1]({jobs: [newestJob]});
  await newPoll;
  responses[0]({jobs: [{id: 'old', waiting_for_login: false}]});
  await oldPoll;
  assert.equal(state.jobs[0].id, 'newest');
  assert.equal($('#loginBanner').classList.hidden, false);

  const selectionPoll = refreshJobs();
  state.selectedJobId = 'just-started';
  responses[2]({jobs: [newestJob]});
  await selectionPoll;
  assert.equal(state.selectedJobId, 'just-started');

  let releaseLogs;
  let logCalls = 0;
  refreshJobLog = () => ++logCalls === 1 ? new Promise(resolve => { releaseLogs = resolve; }) : Promise.resolve();
  const beforeLogin = refreshJobs();
  responses[3]({jobs: [{id: 'just-started', waiting_for_login: false}]});
  await Promise.resolve();
  const afterLogin = refreshJobs();
  responses[4]({jobs: [{id: 'just-started', waiting_for_login: true}]});
  await afterLogin;
  assert.equal($('#loginBanner').classList.hidden, false);
  releaseLogs();
  await beforeLogin;
  assert.equal($('#loginBanner').classList.hidden, false);
})().catch(error => { console.error(error); process.exitCode = 1; });
''')
