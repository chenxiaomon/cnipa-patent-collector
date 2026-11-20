"""Exporting the patents of a pasted application-number list."""

import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

import web_dashboard
from db_manager import PatentsDB


def _patent(app_no: str, title: str) -> dict:
    return {
        'application_no': app_no,
        'status_code': 200,
        'zhuanlimc': title,
        'fwxx_list': [{'tongzhismc': '驳回决定', 'fawenr': '20250926'}],
    }


class TestRecordsForApplicationNos(unittest.TestCase):
    def test_returns_stored_records_in_list_order_and_skips_unknown_numbers(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db = PatentsDB(Path(tmpdir) / 'patents.db')
            db.upsert_batch([_patent('2023100000001', '甲'), _patent('2023100000002', '乙')])

            records = db.records_for_application_nos(['2023100000002', '2023199999999', '2023100000001'])

        self.assertEqual([record['application_no'] for record in records], ['2023100000002', '2023100000001'])
        self.assertEqual(records[0]['fwxx_list'], [{'tongzhismc': '驳回决定', 'fawenr': '20250926'}])
        self.assertNotIn('updated_at', records[0])


class TestAppNoExportHttp(unittest.TestCase):
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
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db = PatentsDB(Path(self.tmpdir.name) / 'patents.db')
        self.db.upsert_batch([_patent('2023100000001', '甲'), _patent('202310000002X', '乙')])
        patches = [
            patch.object(web_dashboard, '_patents_db', self.db),
            patch.object(web_dashboard, 'api_token_matches', return_value=True),
        ]
        for active_patch in patches:
            active_patch.start()
            self.addCleanup(active_patch.stop)
        self.addCleanup(self.tmpdir.cleanup)

    def post(self, path: str, text: str):
        request = urllib.request.Request(
            self.endpoint + path, data=json.dumps({'text': text}).encode('utf-8'),
            headers={'Content-Type': 'application/json'}, method='POST',
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, response.headers, response.read()
        except urllib.error.HTTPError as exception:
            return exception.code, exception.headers, exception.read()

    def test_preview_counts_recognized_and_stored_numbers(self):
        status, _, body = self.post(
            '/api/export/excel-by-app-nos?preview=true',
            'CN202310000002.X\n2023100000001, 2023100000001\n2023199999999\n申请号',
        )

        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {'requested': 3, 'found': 2, 'missing_sample': ['2023199999999']})

    def test_export_passes_only_listed_patents_in_list_order(self):
        with patch.object(web_dashboard, 'build_filtered_excel', return_value=b'workbook') as build_excel:
            status, headers, body = self.post(
                '/api/export/excel-by-app-nos', 'CN202310000002.X\n2023199999999\n2023100000001',
            )

        self.assertEqual(status, 200)
        self.assertEqual(body, b'workbook')
        self.assertIn('patents_by_list_', headers['Content-Disposition'])
        exported = build_excel.call_args.args[0]
        self.assertEqual([record['application_no'] for record in exported], ['202310000002X', '2023100000001'])

    def test_list_without_application_numbers_is_rejected(self):
        with patch.object(web_dashboard, 'build_filtered_excel') as build_excel:
            status, _, body = self.post('/api/export/excel-by-app-nos', '申请号\n\n')

        self.assertEqual(status, 400)
        self.assertIn('没有识别到申请号', json.loads(body)['error'])
        build_excel.assert_not_called()

    def test_list_with_no_stored_patents_is_rejected_before_building_workbook(self):
        with patch.object(web_dashboard, 'build_filtered_excel') as build_excel:
            status, _, _ = self.post('/api/export/excel-by-app-nos', '2023199999999')

        self.assertEqual(status, 404)
        build_excel.assert_not_called()

    def test_export_requires_operator_token(self):
        with patch.object(web_dashboard, 'api_token_matches', return_value=False), patch.object(
            web_dashboard, 'build_filtered_excel',
        ) as build_excel:
            status, _, _ = self.post('/api/export/excel-by-app-nos', '2023100000001')

        self.assertEqual(status, 401)
        build_excel.assert_not_called()


if __name__ == '__main__':
    unittest.main()
