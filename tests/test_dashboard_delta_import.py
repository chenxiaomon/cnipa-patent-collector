#!/usr/bin/env python3

import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import MagicMock, patch

import web_dashboard
from db_manager import PatentsDB


class TestDashboardDeltaImport(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        web_dashboard.DashboardHandler.job_manager = web_dashboard.JobManager()
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

    def post_delta(self, body: bytes) -> tuple[int, dict]:
        request = urllib.request.Request(
            self.endpoint + '/api/import/delta',
            data=body,
            headers={
                'Content-Type': 'application/x-ndjson',
                'X-CNIPA-Token': 'test-token',
            },
            method='POST',
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def test_valid_delta_is_normalized_before_database_write(self):
        patents_db = MagicMock()
        summary = {
            'records': 1,
            'applications': 1,
            'new_applications': 1,
            'updated_applications': 0,
            'timestamp_from': None,
            'timestamp_to': None,
        }
        patents_db.summarize_record_import.return_value = summary
        patents_db.apply_master_delta.return_value = 1

        with patch.object(web_dashboard, 'api_token_matches', return_value=True), patch.object(
            web_dashboard, 'read_machine_role', return_value='replica'
        ), patch.object(web_dashboard, '_patents_db', patents_db):
            status, response = self.post_delta(json.dumps({
                'application_no': 'CN202310869634.X',
                'status_code': 200,
                'response_time_ms': 12.5,
                'detected': 0,
                'response_summary': 'collected',
                'fwxx_list': [{'tongzhismc': '通知书'}],
                'bhsjtzs_data': {'tongzhismc': '通知书'},
                'payable_fee_records': [],
                '_sync_updated_at': '2026-09-06T00:00:00.000000Z',
            }).encode() + b'\n')

        expected_records = [{
            'application_no': '202310869634X',
            'status_code': 200,
            'response_time_ms': 12.5,
            'detected': False,
            'response_summary': 'collected',
            'fwxx_list': [{'tongzhismc': '通知书'}],
            'bhsjtzs_data': {'tongzhismc': '通知书'},
            'payable_fee_records': [],
            '_sync_updated_at': '2026-09-06T00:00:00.000000Z',
        }]
        self.assertEqual(status, 200)
        self.assertEqual(response['imported'], 1)
        patents_db.summarize_record_import.assert_called_once_with(expected_records)
        patents_db.apply_master_delta.assert_called_once_with(expected_records)

    def test_dashboard_accepts_its_patents_database_delta_contract(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source_db = PatentsDB(root / 'source.db')
            source_db.upsert({
                'application_no': '202310869634X',
                'status_code': 200,
                'response_time_ms': 12.5,
                'detected': True,
                'zhuanlimc': '测试专利',
                'fwxx_list': [{'tongzhismc': '通知书'}],
                'bhsjtzs_data': {'tongzhismc': '通知书'},
                'payable_fee_records': [],
            })
            exported_records = source_db.export_delta('1970-01-01T00:00:00Z')
            body = b'\n'.join(
                json.dumps(record, ensure_ascii=False).encode()
                for record in exported_records
            )
            target_db = PatentsDB(root / 'target.db')

            with patch.object(web_dashboard, 'api_token_matches', return_value=True), patch.object(
                web_dashboard, 'read_machine_role', return_value='replica'
            ), patch.object(web_dashboard, '_patents_db', target_db):
                status, response = self.post_delta(body)

            imported_record = target_db.get_record('202310869634X')
            self.assertEqual(status, 200)
            self.assertEqual(response['imported'], 1)
            self.assertEqual(imported_record['status_code'], 200)
            self.assertEqual(imported_record['detected'], 1)
            self.assertEqual(imported_record['fwxx_list'], [{'tongzhismc': '通知书'}])
            self.assertEqual(imported_record['bhsjtzs_data'], {'tongzhismc': '通知书'})

    def test_explicit_null_clears_existing_database_value(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            patents_db = PatentsDB(Path(tmpdir) / 'patents.db')
            patents_db.upsert({
                'application_no': '202310869634X',
                'status_code': 200,
                'zhuanlimc': '应被清空的专利名称',
            })
            body = json.dumps({
                'application_no': '202310869634X',
                'zhuanlimc': None,
            }).encode()

            with patch.object(web_dashboard, 'api_token_matches', return_value=True), patch.object(
                web_dashboard, 'read_machine_role', return_value='replica'
            ), patch.object(web_dashboard, '_patents_db', patents_db):
                status, response = self.post_delta(body)

            imported_record = patents_db.get_record('202310869634X')
            self.assertEqual(status, 200)
            self.assertEqual(response['imported'], 1)
            self.assertIsNone(imported_record['zhuanlimc'])
            self.assertEqual(imported_record['status_code'], 200)

    def test_master_requires_confirmation_before_delta_write(self):
        patents_db = MagicMock()
        import_summary = {
            'records': 1,
            'applications': 1,
            'new_applications': 0,
            'updated_applications': 1,
            'timestamp_from': None,
            'timestamp_to': None,
        }
        patents_db.summarize_record_import.return_value = import_summary
        body = b'{"application_no":"202310869634X","status_code":200}'

        with patch.object(web_dashboard, 'api_token_matches', return_value=True), patch.object(
            web_dashboard, 'read_machine_role', return_value='master'
        ), patch.object(web_dashboard, '_patents_db', patents_db):
            status, response = self.post_delta(body)

        self.assertEqual(status, 409)
        self.assertTrue(response['confirmation_required'])
        self.assertEqual(response['summary'], import_summary)
        patents_db.apply_master_delta.assert_not_called()

    def test_any_invalid_line_rejects_whole_delta_before_database_access(self):
        valid_line = '{"application_no":"202310869634X","status_code":200}'
        invalid_bodies = {
            'malformed JSON': f'{valid_line}\n{{'.encode(),
            'non-object': f'{valid_line}\n[]'.encode(),
            'missing application number': f'{valid_line}\n{{"timestamp":"2026-09-06T00:00:00Z"}}'.encode(),
            'numeric application number': f'{valid_line}\n{{"application_no":2023108696341}}'.encode(),
            'unsupported application number': f'{valid_line}\n{{"application_no":"not-a-patent"}}'.encode(),
            'application number only': f'{valid_line}\n{{"application_no":"202310869635X"}}'.encode(),
            'unknown field': (
                f'{valid_line}\n{{"application_no":"202310869635X","status_code":200,"title_typo":"x"}}'
            ).encode(),
        }

        for case, body in invalid_bodies.items():
            with self.subTest(case=case):
                patents_db = MagicMock()
                with patch.object(web_dashboard, 'api_token_matches', return_value=True), patch.object(
                    web_dashboard, '_patents_db', patents_db
                ):
                    status, response = self.post_delta(body)

                self.assertEqual(status, 400)
                self.assertIn('未导入任何记录', response['error'])
                patents_db.summarize_record_import.assert_not_called()
                patents_db.apply_master_delta.assert_not_called()

    def test_wrong_patent_field_types_reject_whole_delta(self):
        invalid_records = {
            'list field is object': {
                'application_no': '202310869634X', 'status_code': 200, 'fwxx_list': {},
            },
            'list member is scalar': {
                'application_no': '202310869634X', 'status_code': 200, 'fwxx_list': ['notice'],
            },
            'object field is list': {
                'application_no': '202310869634X', 'status_code': 200, 'bhsjtzs_data': [],
            },
            'integer field is boolean': {
                'application_no': '202310869634X', 'status_code': True,
            },
            'integer exceeds sqlite range': {
                'application_no': '202310869634X', 'status_code': 2 ** 63,
            },
            'number field is text': {
                'application_no': '202310869634X', 'status_code': 200, 'response_time_ms': '12.5',
            },
            'boolean field is text': {
                'application_no': '202310869634X', 'status_code': 200, 'detected': 'false',
            },
            'boolean field is other integer': {
                'application_no': '202310869634X', 'status_code': 200, 'detected': 2,
            },
            'text field is number': {
                'application_no': '202310869634X', 'status_code': 200, 'zhuanlimc': 123,
            },
        }

        for case, record in invalid_records.items():
            with self.subTest(case=case):
                patents_db = MagicMock()
                body = json.dumps(record).encode()
                with patch.object(web_dashboard, 'api_token_matches', return_value=True), patch.object(
                    web_dashboard, '_patents_db', patents_db
                ):
                    status, response = self.post_delta(body)

                self.assertEqual(status, 400)
                self.assertIn('未导入任何记录', response['error'])
                patents_db.summarize_record_import.assert_not_called()
                patents_db.apply_master_delta.assert_not_called()

    def test_non_finite_numbers_reject_whole_delta(self):
        invalid_bodies = {
            'NaN': b'{"application_no":"202310869634X","status_code":200,"response_time_ms":NaN}',
            'Infinity': b'{"application_no":"202310869634X","status_code":200,"response_time_ms":Infinity}',
            'negative Infinity': b'{"application_no":"202310869634X","status_code":200,"response_time_ms":-Infinity}',
            'overflowing exponent': b'{"application_no":"202310869634X","status_code":200,"response_time_ms":1e9999}',
            'nested Infinity': b'{"application_no":"202310869634X","status_code":200,"fwxx_list":[{"amount":Infinity}]}',
        }

        for case, body in invalid_bodies.items():
            with self.subTest(case=case):
                patents_db = MagicMock()
                with patch.object(web_dashboard, 'api_token_matches', return_value=True), patch.object(
                    web_dashboard, '_patents_db', patents_db
                ):
                    status, response = self.post_delta(body)

                self.assertEqual(status, 400)
                self.assertIn('有限', response['error'])
                patents_db.summarize_record_import.assert_not_called()
                patents_db.apply_master_delta.assert_not_called()

    def test_non_utf8_delta_is_rejected_before_database_access(self):
        patents_db = MagicMock()
        with patch.object(web_dashboard, 'api_token_matches', return_value=True), patch.object(
            web_dashboard, '_patents_db', patents_db
        ):
            status, response = self.post_delta(b'\xff\xfe')

        self.assertEqual(status, 400)
        self.assertIn('UTF-8', response['error'])
        patents_db.summarize_record_import.assert_not_called()
        patents_db.apply_master_delta.assert_not_called()

    def test_empty_delta_is_rejected_before_database_access(self):
        patents_db = MagicMock()
        with patch.object(web_dashboard, 'api_token_matches', return_value=True), patch.object(
            web_dashboard, '_patents_db', patents_db
        ):
            status, response = self.post_delta(b'')

        self.assertEqual(status, 400)
        self.assertIn('为空', response['error'])
        patents_db.summarize_record_import.assert_not_called()
        patents_db.apply_master_delta.assert_not_called()

    def test_delta_larger_than_normal_json_limit_is_accepted(self):
        patents_db = MagicMock()
        patents_db.summarize_record_import.return_value = {
            'records': 1,
            'applications': 1,
            'new_applications': 1,
            'updated_applications': 0,
            'timestamp_from': None,
            'timestamp_to': None,
        }
        patents_db.apply_master_delta.return_value = 1
        body = json.dumps({
            'application_no': '202310869634X',
            'status_code': 200,
            'response_summary': 'x' * (web_dashboard.MAX_BODY_BYTES + 1),
        }).encode()
        self.assertGreater(len(body), web_dashboard.MAX_BODY_BYTES)
        self.assertLess(len(body), web_dashboard.MAX_DELTA_IMPORT_BYTES)

        with patch.object(web_dashboard, 'api_token_matches', return_value=True), patch.object(
            web_dashboard, 'read_machine_role', return_value='replica'
        ), patch.object(web_dashboard, '_patents_db', patents_db):
            status, response = self.post_delta(body)

        self.assertEqual(status, 200)
        self.assertEqual(response['imported'], 1)
        patents_db.apply_master_delta.assert_called_once()

    def test_delta_over_independent_import_limit_is_rejected(self):
        body = b'{"application_no":"202310869634X","status_code":200}'
        patents_db = MagicMock()
        with patch.object(web_dashboard, 'api_token_matches', return_value=True), patch.object(
            web_dashboard, 'MAX_DELTA_IMPORT_BYTES', len(body) - 1
        ), patch.object(web_dashboard, '_patents_db', patents_db):
            status, response = self.post_delta(body)

        self.assertEqual(status, 413)
        self.assertIn('超过大小限制', response['error'])
        patents_db.summarize_record_import.assert_not_called()
        patents_db.apply_master_delta.assert_not_called()


if __name__ == '__main__':
    unittest.main()
