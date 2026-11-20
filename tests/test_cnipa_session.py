"""Observed authentication failures stop the current collection without losing targets."""

import io
import json
import tempfile
import unittest
from contextlib import ExitStack, redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

import cache_utils
import cnipa_session
import collection_checkpoint
import main_automation
import patent_mitm_scraper
from tests.test_mitm_scraper import _json_body, _make_flow


class TestCNIPASessionFailures(unittest.TestCase):
    def setUp(self):
        patches = ExitStack()
        self.addCleanup(patches.close)
        self.directory = Path(patches.enter_context(tempfile.TemporaryDirectory()))
        for constant, filename in (
            ('CNIPA_SESSION_FILE', 'session.json'),
            ('CNIPA_SESSION_FAILURE_FILE', 'failure.json'),
            ('CNIPA_API_EVENTS_FILE', 'api.jsonl'),
        ):
            patches.enter_context(patch.object(cnipa_session, constant, self.directory / filename))
        patches.enter_context(patch.object(cnipa_session, 'clear_collection_alert'))
        self.alert = patches.enter_context(patch.object(cnipa_session, 'record_collection_alert'))
        patches.enter_context(patch.object(patent_mitm_scraper, 'DetectionLogger'))
        patches.enter_context(patch.object(collection_checkpoint, 'COLLECTION_BATCHES_DIR', self.directory / 'batches'))
        self.scraper = patent_mitm_scraper.PatentMITMScraper()
        cnipa_session.begin_cnipa_session()

    def failed_response(self, status=401, payload=None):
        flow = _make_flow(
            'https://cpquery.cponline.cnipa.gov.cn/api/query?token=private-token',
            status=status, body=_json_body(payload if payload is not None else {}),
        )
        self.scraper.request(flow)
        return flow

    def test_current_http_401_stops_polling_without_retry_or_business_write(self):
        flow = self.failed_response()
        self.scraper.response(flow)
        with patch.object(cache_utils.time, 'sleep') as sleep, patch.object(
            main_automation, 'InputService'
        ) as input_service:
            logger = Mock()
            with self.assertRaises(cnipa_session.CNIPALoginRequired):
                with collection_checkpoint.CollectionBatch.create(
                    'main', self.directory / 'pending.txt', ['2024100659780'],
                ) as batch:
                    batch.record_started('2024100659780')
                    main_automation.search_application(Mock(), '2024100659780', 1, 2, 3, 4, logger)
        sleep.assert_not_called()
        input_service.type_in_search.assert_not_called()
        logger.add_record.assert_not_called()
        logger.upsert_record.assert_not_called()
        self.assertEqual((self.directory / 'pending.txt').read_text(), '2024100659780\n')
        snapshot = json.loads(next((self.directory / 'batches').glob('*.json')).read_text())
        self.assertEqual(snapshot['items'][0]['status'], 'interrupted')
        self.assertIn('登录已失效', snapshot['runs'][0]['stop_reason'])
        self.assertEqual(self.alert.call_args.args[0], 'login_required')

    def test_login_failure_arriving_during_cache_wait_aborts_all_retry_windows(self):
        flow = self.failed_response()
        retry_query = Mock()
        with patch.object(cache_utils.time, 'sleep', side_effect=lambda seconds: self.scraper.response(flow)) as sleep:
            with self.assertRaises(cnipa_session.CNIPALoginRequired):
                cache_utils.poll_cache_with_retry(
                    str(self.directory / 'missing.json'), '2024100659780',
                    on_retry=retry_query, on_poll=cnipa_session.raise_if_cnipa_login_required,
                )
        self.assertEqual(sleep.call_count, 1)
        retry_query.assert_not_called()

    def test_old_response_and_old_failure_cannot_invalidate_new_confirmed_login(self):
        old_flow = self.failed_response()
        self.scraper.response(old_flow)
        cnipa_session.begin_cnipa_session()
        cnipa_session.raise_if_cnipa_login_required()
        self.scraper.response(old_flow)
        cnipa_session.raise_if_cnipa_login_required()
        self.alert.assert_not_called()

    def test_login_failure_at_timeout_boundary_prevents_retry_click(self):
        flow = self.failed_response()
        retry_query = Mock()
        elapsed = [0]

        def publish_expiry(seconds):
            elapsed[0] += 9
            self.scraper.response(flow)

        with patch.object(cache_utils.time, 'monotonic', side_effect=lambda: elapsed[0]), patch.object(
            cache_utils.time, 'sleep', side_effect=publish_expiry,
        ):
            with self.assertRaises(cnipa_session.CNIPALoginRequired):
                cache_utils.poll_cache_with_retry(
                    str(self.directory / 'missing.json'), '2024100659780',
                    on_retry=retry_query, on_poll=cnipa_session.raise_if_cnipa_login_required,
                )
        retry_query.assert_not_called()

    def test_explicit_business_login_errors_are_recognized(self):
        for payload in ({'code': 401}, {'code': 500, 'msg': '登录已过期，请重新登录'}, {'msg': '请重新登录'}):
            with self.subTest(payload=payload):
                cnipa_session.begin_cnipa_session()
                self.scraper.response(self.failed_response(200, payload))
                with self.assertRaises(cnipa_session.CNIPALoginRequired):
                    cnipa_session.raise_if_cnipa_login_required()

    def test_forbidden_rate_limit_and_transient_failure_are_not_assumed_to_be_login_expiry(self):
        for status in (403, 429, 500):
            self.scraper.response(self.failed_response(status, {'msg': '访问受限'}))
            cnipa_session.raise_if_cnipa_login_required()
        self.alert.assert_not_called()
        events = [json.loads(line) for line in (self.directory / 'api.jsonl').read_text().splitlines()]
        self.assertEqual([event['http_status'] for event in events], [403, 429, 500])

    def test_diagnostic_files_and_output_do_not_contain_query_tokens_or_messages(self):
        flow = self.failed_response(200, {'code': 500, 'msg': 'token=private-message'})
        with redirect_stdout(io.StringIO()) as output:
            self.scraper.response(flow)
        recorded = (self.directory / 'api.jsonl').read_text() + output.getvalue()
        self.assertNotIn('private-token', recorded)
        self.assertNotIn('private-message', recorded)
        self.assertIn('/api/query', recorded)

    def test_logging_failure_does_not_disable_authentication_stop(self):
        with patch.object(cnipa_session, 'RotatingFileHandler', side_effect=PermissionError('read only')):
            self.scraper.response(self.failed_response())
        with self.assertRaises(cnipa_session.CNIPALoginRequired):
            cnipa_session.raise_if_cnipa_login_required()

    def test_transport_failure_is_recorded_without_becoming_login_expiry(self):
        flow = self.failed_response()
        self.scraper.error(flow)
        cnipa_session.raise_if_cnipa_login_required()
        event = json.loads((self.directory / 'api.jsonl').read_text())
        self.assertEqual(event['reason'], 'transport_failed')
        self.assertEqual(event['http_status'], 0)
        self.alert.assert_not_called()

    def test_other_origin_cannot_invalidate_session_by_embedding_cnipa_in_url(self):
        flow = _make_flow('https://other.example/api/cponline.cnipa.gov.cn', status=401)
        self.scraper.request(flow)
        self.scraper.response(flow)
        cnipa_session.raise_if_cnipa_login_required()
        self.assertNotIn('cnipa_session', flow.metadata)
        self.assertFalse((self.directory / 'failure.json').exists())

    def test_success_or_transient_error_cannot_clear_confirmed_login_failure(self):
        self.scraper.response(self.failed_response())
        self.scraper.response(self.failed_response(500))
        self.scraper.response(self.failed_response(200, {'code': 200}))
        with self.assertRaises(cnipa_session.CNIPALoginRequired):
            cnipa_session.raise_if_cnipa_login_required()


if __name__ == '__main__':
    unittest.main()
