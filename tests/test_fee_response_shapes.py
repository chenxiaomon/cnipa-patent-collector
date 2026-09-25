"""Unknown fee sections remain unknown, with safe diagnostics and a current snapshot."""

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from db_manager import PatentsDB
from patent_mitm_scraper import PatentMITMScraper


APPLICATION_NO = '2024103465076'
SECTION_FIELDS = {
    'payable_fee_records': ('yingjiaofei', 'svYingjfList'),
    'late_fee_schedule_records': ('zhinajin', 'svZnjList'),
    'paid_fee_records': ('yijiaofei', 'svYijfList'),
    'fee_receipt_dispatch_records': ('shoujufawen', 'svSjfwList'),
}


class TestFeeResponseShapes(unittest.TestCase):
    def setUp(self):
        logger_patch = patch('patent_mitm_scraper.DetectionLogger')
        logger_patch.start()
        self.addCleanup(logger_patch.stop)
        self.scraper = PatentMITMScraper()
        self.current_attempt = {'application_no': APPLICATION_NO, 'attempt_id': 'current-fee-attempt'}
        marker_patch = patch('patent_mitm_scraper.read_detail_attempt_marker', return_value=self.current_attempt)
        self.marker = marker_patch.start()
        self.addCleanup(marker_patch.stop)
        cache_read_patch = patch('patent_mitm_scraper.read_json_cache', return_value={})
        cache_read_patch.start()
        self.addCleanup(cache_read_patch.stop)
        cache_write_patch = patch('patent_mitm_scraper.write_json_cache')
        self.cache_write = cache_write_patch.start()
        self.addCleanup(cache_write_patch.stop)
        self.output = io.StringIO()

    def consume_response(self, response_payload):
        flow = SimpleNamespace(
            response=SimpleNamespace(content=json.dumps(response_payload).encode('utf-8')),
            metadata={
                'cnipa_detail_target_application_no': APPLICATION_NO,
                'cnipa_detail_attempt': dict(self.current_attempt),
            },
        )
        with redirect_stdout(self.output):
            self.scraper._process_fee_response(flow)

    def cached_fields(self):
        self.cache_write.assert_called_once()
        return self.cache_write.call_args.args[1][APPLICATION_NO]

    def test_explicit_empty_lists_are_complete_even_when_sections_are_hidden(self):
        self.consume_response({'code': 200, 'data': {
            section_name: {'isShow': False, list_name: []}
            for section_name, list_name in SECTION_FIELDS.values()
        }})

        captured_fields = self.cached_fields()
        for cache_field in SECTION_FIELDS:
            self.assertEqual(captured_fields[cache_field], [])
        self.assertNotIn('fee_section_issues', captured_fields)
        snapshot_at = datetime.fromisoformat(captured_fields['fee_snapshot_at'].replace('Z', '+00:00'))
        self.assertEqual(snapshot_at.utcoffset(), timezone.utc.utcoffset(snapshot_at))

    def test_non_object_sections_do_not_become_zero_fees(self):
        for section, type_name in (
            (None, 'null'), ('private-response-value', 'string'), ([], 'array'),
            ([{'private-record-value': 'account-placeholder'}], 'array'),
            (False, 'boolean'), (0, 'number'),
        ):
            with self.subTest(type_name=type_name, section_type=type(section).__name__):
                self.cache_write.reset_mock()
                self.consume_response({'code': 200, 'data': {
                    'yingjiaofei': section,
                    'yijiaofei': {'svYijfList': []},
                    'shoujufawen': {'svSjfwList': []},
                }})
                captured_fields = self.cached_fields()
                self.assertNotIn('payable_fee_records', captured_fields)
                self.assertIn('fee_snapshot_at', captured_fields)
                self.assertEqual(captured_fields['fee_section_issues']['payable_fee_records'], {
                    'reason': 'section_not_object', 'section_type': type_name,
                })
        self.assertNotIn('private-response-value', self.output.getvalue())
        self.assertNotIn('private-record-value', self.output.getvalue())
        self.assertNotIn('account-placeholder', self.output.getvalue())

    def test_missing_section_and_hidden_section_without_records_remain_distinct(self):
        self.consume_response({'code': 200, 'data': {
            'zhinajin': {'isShow': False},
            'yijiaofei': {'svYijfList': []},
            'shoujufawen': {'svSjfwList': []},
        }})

        captured_fields = self.cached_fields()
        self.assertNotIn('payable_fee_records', captured_fields)
        self.assertNotIn('late_fee_schedule_records', captured_fields)
        self.assertEqual(captured_fields['fee_section_issues']['payable_fee_records'], {
            'reason': 'section_missing',
        })
        self.assertEqual(captured_fields['fee_section_issues']['late_fee_schedule_records'], {
            'reason': 'records_missing', 'section_type': 'object', 'is_show': False,
        })

    def test_invalid_records_report_only_types_and_counts(self):
        self.consume_response({'code': 200, 'data': {
            'yingjiaofei': {'isShow': True, 'svYingjfList': 'private-response-value'},
            'zhinajin': {'isShow': True, 'svZnjList': [{'private-record-value': 3}, 'account-placeholder']},
            'yijiaofei': {'svYijfList': []},
            'shoujufawen': {'svSjfwList': []},
        }})

        captured_fields = self.cached_fields()
        issues = captured_fields['fee_section_issues']
        self.assertEqual(issues['payable_fee_records'], {
            'reason': 'records_not_list', 'section_type': 'object', 'is_show': True, 'records_type': 'string',
        })
        self.assertEqual(issues['late_fee_schedule_records'], {
            'reason': 'records_not_objects', 'section_type': 'object', 'is_show': True,
            'records_type': 'array', 'record_count': 2, 'invalid_record_count': 1,
        })
        for private_value in ('private-response-value', 'private-record-value', 'account-placeholder'):
            self.assertNotIn(private_value, self.output.getvalue())
            self.assertNotIn(private_value, json.dumps(issues))

    def test_unknown_record_fields_are_preserved_without_being_logged(self):
        raw_record = {'futureField': {'nested': ['private-record-value', 3]}}
        self.consume_response({'code': 200, 'data': {
            'yijiaofei': {'isShow': True, 'svYijfList': [raw_record]},
        }})

        self.assertEqual(self.cached_fields()['paid_fee_records'], [raw_record])
        self.assertNotIn('private-record-value', self.output.getvalue())

    def test_response_without_any_valid_section_does_not_publish_diagnostics_as_fees(self):
        self.consume_response({'code': 200, 'data': {
            'yingjiaofei': None, 'zhinajin': {'isShow': False},
            'yijiaofei': {'svYijfList': None}, 'shoujufawen': [],
        }})

        self.cache_write.assert_not_called()
        self.assertIn('没有可缓存的有效栏目', self.output.getvalue())

    def test_invalid_response_containers_never_publish_fee_fields(self):
        for response_payload in ([], None, 'private-response-value', {'code': 200, 'data': None}):
            with self.subTest(response_type=type(response_payload).__name__):
                self.consume_response(response_payload)
        self.cache_write.assert_not_called()
        self.assertNotIn('private-response-value', self.output.getvalue())

    def test_partial_response_from_revoked_attempt_cannot_replace_current_cache(self):
        self.marker.return_value = {**self.current_attempt, 'attempt_id': 'replacement-fee-attempt'}
        self.consume_response({'code': 200, 'data': {
            'yingjiaofei': None, 'yijiaofei': {'svYijfList': []},
        }})
        self.cache_write.assert_not_called()

    def test_new_partial_snapshot_cannot_borrow_payable_fees_from_old_complete_snapshot(self):
        self.consume_response({'code': 200, 'data': {
            'yingjiaofei': None, 'zhinajin': None,
            'yijiaofei': {'svYijfList': [{'receipt': 'new-test-receipt'}]},
            'shoujufawen': {'svSjfwList': []},
        }})
        captured_fields = self.cached_fields()

        with tempfile.TemporaryDirectory() as temporary_directory:
            patents = PatentsDB(Path(temporary_directory) / 'patents.db')
            patents.upsert({
                'application_no': APPLICATION_NO,
                'anjianywzt': 'test-status', 'timestamp': '2020-01-01T00:00:00Z',
                'fee_snapshot_at': '2020-01-01T00:00:00Z',
                **{cache_field: [] for cache_field in SECTION_FIELDS},
            })
            stored_snapshot = patents.update_fee_snapshot(APPLICATION_NO, {
                cache_field: captured_fields[cache_field]
                for cache_field in (*SECTION_FIELDS, 'fee_snapshot_at')
                if cache_field in captured_fields
            })
            self.assertIsNone(stored_snapshot['payable_fee_records'])
            self.assertIsNone(stored_snapshot['late_fee_schedule_records'])
            self.assertEqual(stored_snapshot['paid_fee_records'], [{'receipt': 'new-test-receipt'}])
            self.assertEqual(stored_snapshot['fee_receipt_dispatch_records'], [])
            self.assertEqual(stored_snapshot['fee_snapshot_at'], captured_fields['fee_snapshot_at'])
            self.assertNotIn(APPLICATION_NO, patents.fee_details_completed_app_nos())
            patent_record = patents.get_record(APPLICATION_NO)
            self.assertEqual(patent_record['anjianywzt'], 'test-status')
            self.assertEqual(patent_record['timestamp'], '2020-01-01T00:00:00Z')


if __name__ == '__main__':
    unittest.main()
