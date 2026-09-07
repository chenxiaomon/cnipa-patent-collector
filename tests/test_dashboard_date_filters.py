"""Exercise export and business dates through the Dashboard HTTP boundary."""

import json
import os
import shutil
import subprocess
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch

import web_dashboard
from db_manager import PatentsDB


class TestDashboardDateFilters(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = web_dashboard.ThreadingHTTPServer(
            ('127.0.0.1', 0), web_dashboard.DashboardHandler
        )
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
        self.patents_db = PatentsDB(Path(temporary_directory.name) / 'patents.db')
        for dashboard_patch in (
            patch.object(web_dashboard, '_patents_db', self.patents_db),
            patch.object(web_dashboard, 'api_token_matches', return_value=True),
        ):
            dashboard_patch.start()
            self.addCleanup(dashboard_patch.stop)

    def post_filters(self, path, filters):
        request = urllib.request.Request(
            self.endpoint + path,
            data=json.dumps(filters).encode(),
            headers={'Content-Type': 'application/json'},
            method='POST',
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as exception:
            with exception:
                return exception.code, exception.read()

    def test_beijing_date_preview_and_export_select_same_records(self):
        for suffix, timestamp in enumerate((
            '2026-09-05T15:59:59.999999Z',
            '2026-09-05T16:00:00Z',
            '2026-09-06T15:59:59.999999Z',
            '2026-09-06T16:00:00Z',
        ), start=1):
            self.patents_db.upsert({
                'application_no': f'20260000000{suffix:02d}',
                'timestamp': timestamp,
            })
        filters = {'timestamp_from': '2026-09-06', 'timestamp_to': '2026-09-06'}

        status, preview_body = self.post_filters('/api/export/excel-filtered?preview=true', filters)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(preview_body), {'count': 2})

        with patch.object(web_dashboard, 'build_filtered_excel', return_value=b'workbook') as build_excel:
            status, workbook_body = self.post_filters('/api/export/excel-filtered', filters)
        self.assertEqual((status, workbook_body), (200, b'workbook'))
        self.assertEqual(
            {patent['application_no'] for patent in build_excel.call_args.args[0]},
            {'2026000000002', '2026000000003'},
        )

    def test_invalid_and_reversed_dates_return_client_error(self):
        for filters in (
            {'timestamp_from': '2026-02-30'},
            {'timestamp_to': ['2026-09-06']},
            {'timestamp_from': False},
            {'timestamp_to': 0},
            {'timestamp_from': []},
            {'timestamp_to': {}},
            {'timestamp_from': '2026-09-07', 'timestamp_to': '2026-09-06'},
        ):
            with self.subTest(filters=filters):
                status, response_body = self.post_filters('/api/export/excel-filtered?preview=true', filters)
                self.assertEqual(status, 400)
                self.assertIn('error', json.loads(response_body))

    def test_legacy_exact_timestamp_range_remains_inclusive(self):
        self.patents_db.upsert({
            'application_no': '2026000000001',
            'timestamp': '2026-09-06T08:00:00+08:00',
        })
        status, preview_body = self.post_filters('/api/export/excel-filtered?preview=true', {
            'timestamp_from': '2026-09-06T00:00:00Z',
            'timestamp_to': '2026-09-06T00:00:00Z',
        })
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(preview_body), {'count': 1})

    def test_fee_analysis_uses_beijing_day_at_utc_year_boundary(self):
        utc_instant = datetime(2026, 12, 31, 16, 0, tzinfo=timezone.utc)
        with patch.object(web_dashboard, 'utc_now', return_value=utc_instant), patch.object(
            web_dashboard, 'build_agency_arrears_ranking', return_value=[]
        ) as rank_arrears:
            with urllib.request.urlopen(self.endpoint + '/api/agency-arrears', timeout=5) as response:
                response_payload = json.loads(response.read())
        self.assertEqual(response_payload['analysis_date'], '2027-01-01')
        rank_arrears.assert_called_once_with([], date(2027, 1, 1))


@unittest.skipUnless(shutil.which('node'), 'Node.js is required for JavaScript timezone checks')
class TestDashboardBusinessTimeJavascript(unittest.TestCase):
    def test_display_and_export_dates_are_independent_of_browser_timezone(self):
        javascript_check = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const fields = {
  '#exportTsFrom': {value: '2027-01-01'}, '#exportTsTo': {value: '2027-01-01'},
};
const context = vm.createContext({
  localStorage: {getItem: () => ''},
  document: {
    addEventListener: () => {},
    querySelector: selector => fields[selector] || {value: ''},
  },
});
// Disable startup requests while evaluating the actual Dashboard script.
vm.runInContext(fs.readFileSync(0, 'utf8') + '\nasync function boot() {}', context);
assert.equal(context.timestampDate('2026-12-31T16:00:00.123456').toISOString(), '2026-12-31T16:00:00.123Z');
assert.equal(context.timestampDate('2026-12-31 16:00:00').toISOString(), '2026-12-31T16:00:00.000Z');
for (const timestamp of ['2026-12-31T16:00:00Z', '2026-12-31T16:00:00', '2027-01-01T00:00:00+08:00']) {
  assert.match(context.shortTime(timestamp), /^2027\/1\/1\s+00:00:00$/);
}
const filters = context.collectExportFilters();
assert.equal(filters.timestamp_from, '2027-01-01');
assert.equal(filters.timestamp_to, '2027-01-01');
fields['#exportTsFrom'].value = '';
fields['#exportTsTo'].value = '';
assert.equal(context.collectExportFilters().timestamp_from, '');
assert.equal(context.collectExportFilters().timestamp_to, '');
"""
        for browser_timezone in ('UTC', 'America/Los_Angeles', 'Asia/Shanghai'):
            with self.subTest(timezone=browser_timezone):
                node_execution = subprocess.run(
                    [shutil.which('node'), '-e', javascript_check],
                    input=web_dashboard.JS, text=True, capture_output=True,
                    env={**os.environ, 'TZ': browser_timezone}, timeout=15,
                )
                self.assertEqual(node_execution.returncode, 0, node_execution.stderr)


if __name__ == '__main__':
    unittest.main()
