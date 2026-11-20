import json
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import detail_search
from cnipa_session import CNIPALoginRequired


class TestFwxxSearchReady(unittest.TestCase):
    def setUp(self):
        patches = ExitStack()
        self.addCleanup(patches.close)
        self.cache_directory = patches.enter_context(tempfile.TemporaryDirectory())
        self.cache_file = Path(self.cache_directory) / 'search.json'
        self.attempt = {
            'application_no': '2022114363024',
            'attempt_id': 'current-attempt',
            'started_at': '2026-09-24T00:00:00Z',
        }
        patches.enter_context(patch.object(detail_search, 'PATENT_DETAIL_SEARCH_CACHE_FILE', self.cache_file))
        self.current_marker = patches.enter_context(
            patch.object(detail_search, 'read_detail_attempt_marker', return_value=self.attempt)
        )
        search_clock = patches.enter_context(patch.object(detail_search, 'time'))
        self.sleep = search_clock.sleep
        self.monotonic = search_clock.monotonic
        self.monotonic.return_value = 0
        self.check_login = patches.enter_context(patch.object(detail_search, 'raise_if_cnipa_login_required'))
        patches.enter_context(patch.object(detail_search, 'FWXX_SEARCH_READY_TIMEOUT', 15))

    def publish(self, response_payload, http_status=200):
        detail_search.publish_detail_search_response(self.attempt, http_status, response_payload)
        return json.loads(self.cache_file.read_text(encoding='utf-8'))[self.attempt['attempt_id']]

    def test_unique_target_in_supported_response_shapes_authorizes_without_browser(self):
        record = {'zhuanlisqh': 'CN202211436302.4', 'zhuanlimc': 'must not persist', 'token': 'secret'}
        for payload in (
            {'records': [record], 'total': 1},
            {'code': 200, 'data': {'records': [record], 'total': 1}},
            {'data': [record]},
            [record],
        ):
            with self.subTest(payload=payload):
                receipt = self.publish(payload)
                detail_search.wait_for_detail_search_target(self.attempt)
                self.assertEqual(receipt, {
                    'application_no': '2022114363024',
                    'http_status': 200,
                    'record_count': 1,
                    'returned_application_no': '2022114363024',
                    'reason': 'target_confirmed',
                })
                self.assertNotIn('secret', self.cache_file.read_text(encoding='utf-8'))
                self.assertNotIn('must not persist', self.cache_file.read_text(encoding='utf-8'))
        self.sleep.assert_not_called()

    def test_incomplete_ambiguous_and_failed_responses_cannot_authorize(self):
        target = {'zhuanlisqh': '2022114363024'}
        examples = (
            (400, {'records': [target]}, 'http_error'),
            (200, None, 'response_not_object_or_list'),
            (200, 'private raw body', 'response_not_object_or_list'),
            (200, {'code': 500, 'records': [target]}, 'api_error'),
            (200, {}, 'records_missing'),
            (200, {'data': {}}, 'records_missing'),
            (200, {'records': {}}, 'records_not_list'),
            (200, {'data': {'records': None}}, 'records_not_list'),
            (200, {'records': []}, 'no_records'),
            (200, {'records': [target, target]}, 'multiple_records'),
            (200, {'data': {'records': [target], 'total': 2}}, 'multiple_records'),
            (200, {'records': [target], 'total': 0}, 'record_count_mismatch'),
            (200, {'records': [target], 'total': 'unknown'}, 'total_invalid'),
            (200, {'records': [target], 'total': True}, 'total_invalid'),
            (200, {'records': ['private raw record']}, 'record_not_object'),
            (200, {'records': [{}]}, 'application_no_missing'),
            (200, {'records': [{'zhuanlisqh': {'token': 'secret'}}]}, 'application_no_invalid'),
            (200, {'records': [{'zhuanlisqh': '--'}]}, 'application_no_invalid'),
            (200, {'records': [{'zhuanlisqh': '2022114339006'}]}, 'application_no_mismatch'),
        )
        for status, payload, reason in examples:
            with self.subTest(status=status, payload=payload):
                receipt = self.publish(payload, status)
                self.assertEqual(receipt['reason'], reason)
                with self.assertRaises(detail_search.DetailSearchRetryableError):
                    detail_search.wait_for_detail_search_target(self.attempt)
                serialized_receipt = self.cache_file.read_text(encoding='utf-8')
                self.assertNotIn('private raw', serialized_receipt)
                self.assertNotIn('secret', serialized_receipt)
        self.sleep.assert_not_called()

    def test_no_bound_attempt_does_not_publish(self):
        detail_search.publish_detail_search_response(None, 200, {'records': [{'zhuanlisqh': '2022114363024'}]})
        self.assertFalse(self.cache_file.exists())

    def test_late_old_attempt_does_not_overwrite_current_receipt(self):
        target_response = {'records': [{'zhuanlisqh': '2022114363024'}]}
        current_receipt = self.publish(target_response)
        old_attempt = dict(self.attempt, attempt_id='previous-attempt')
        detail_search.publish_detail_search_response(old_attempt, 400, None)
        self.assertEqual(json.loads(self.cache_file.read_text(encoding='utf-8')), {
            self.attempt['attempt_id']: current_receipt,
        })

    def test_cleared_marker_rejects_late_response(self):
        self.current_marker.return_value = None
        detail_search.publish_detail_search_response(self.attempt, 200, {'records': [{'zhuanlisqh': '2022114363024'}]})
        self.assertFalse(self.cache_file.exists())

    def test_old_nonce_in_cache_cannot_authorize_same_application(self):
        self.publish({'records': [{'zhuanlisqh': '2022114363024'}]})
        self.monotonic.side_effect = [0, 15]
        with self.assertRaisesRegex(detail_search.DetailSearchRetryableError, '重启主 MITM 代理'):
            detail_search.wait_for_detail_search_target(dict(self.attempt, attempt_id='next-attempt'))

    def test_wait_accepts_only_receipt_arriving_during_current_attempt(self):
        self.sleep.side_effect = lambda _: self.publish({'records': [{'zhuanlisqh': '2022114363024'}]})
        detail_search.wait_for_detail_search_target(self.attempt)
        self.sleep.assert_called_once()
        self.assertEqual(self.check_login.call_count, 2)

    def test_missing_receipt_times_out_with_proxy_restart_diagnostic(self):
        self.monotonic.side_effect = [0, 15]
        with self.assertRaisesRegex(detail_search.DetailSearchRetryableError, '当前尝试的主代理回执.*重启主 MITM 代理'):
            detail_search.wait_for_detail_search_target(self.attempt)

    def test_login_expiry_interrupts_without_browser_access_or_wait(self):
        self.check_login.side_effect = CNIPALoginRequired('需要重新登录')
        with self.assertRaisesRegex(CNIPALoginRequired, '需要重新登录'):
            detail_search.wait_for_detail_search_target(self.attempt)
        self.sleep.assert_not_called()


if __name__ == '__main__':
    unittest.main()
