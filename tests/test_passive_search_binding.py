"""Search receipts authorize clicks without inspecting the browser document."""

import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import MagicMock, PropertyMock, patch

from mitmproxy import http

import collect_fees
import collect_fwxx
import detail_attempt
import detail_search
import patent_mitm_scraper
from cache_utils import read_json_cache
from tests.test_detail_response_binding import sqxx_flow
from tests.test_mitm_scraper import FWXX_RESPONSE, FYXX_RESPONSE, _json_body, _make_flow


APPLICATION_NO = '2026102909420'
SEARCH_URL = 'https://cpquery.cponline.cnipa.gov.cn/api/search/undomestic/publicSearch'


class TestPassiveSearchBinding(unittest.TestCase):
    def setUp(self):
        patches = ExitStack()
        self.addCleanup(patches.close)
        self.scratch = Path(patches.enter_context(tempfile.TemporaryDirectory()))
        for module, constant, filename in (
            (detail_attempt, 'MARKER_FILE', 'attempt.json'),
            (detail_attempt, 'PATENT_DETAIL_IDENTITY_CACHE_FILE', 'identity.json'),
            (detail_search, 'PATENT_DETAIL_SEARCH_CACHE_FILE', 'search.json'),
            (patent_mitm_scraper, 'PATENT_CACHE_FILE', 'patent.json'),
            (patent_mitm_scraper, 'PATENT_FWXX_CACHE_FILE', 'fwxx.json'),
            (patent_mitm_scraper, 'PATENT_FEE_CACHE_FILE', 'fees.json'),
            (collect_fwxx, 'PATENT_FWXX_CACHE_FILE', 'fwxx.json'),
            (collect_fees, 'PATENT_FEE_CACHE_FILE', 'fees.json'),
            (patent_mitm_scraper, 'FORCE_UPDATE_FLAG', 'force.flag'),
        ):
            patches.enter_context(patch.object(module, constant, self.scratch / filename))
        for module in (collect_fees, collect_fwxx, detail_search, detail_attempt):
            patches.enter_context(patch.object(module, 'raise_if_cnipa_login_required'))
        patches.enter_context(patch.object(detail_search, 'FWXX_SEARCH_READY_TIMEOUT', 0.02))
        patches.enter_context(patch.object(patent_mitm_scraper, 'read_agency_attempt_marker', return_value=None))
        patches.enter_context(patch.object(patent_mitm_scraper, 'read_cnipa_session', return_value=None))
        patches.enter_context(patch.object(patent_mitm_scraper, 'observe_cnipa_api_response'))
        self.logger = patches.enter_context(patch.object(patent_mitm_scraper, 'DetectionLogger')).return_value
        self.logger.get_processed_applications.return_value = {APPLICATION_NO}
        self.scraper = patent_mitm_scraper.PatentMITMScraper()

    def search_response(self):
        return _make_flow(SEARCH_URL, body=_json_body({
            'code': 200,
            'data': {'records': [{'zhuanlisqh': APPLICATION_NO, 'zhuanlimc': '测试专利'}]},
        }))

    def test_processed_patent_filter_does_not_suppress_current_search_receipt(self):
        attempt = detail_attempt.begin_detail_attempt(APPLICATION_NO)
        search_flow = self.search_response()

        self.scraper.request(search_flow)
        self.scraper.response(search_flow)
        detail_search.wait_for_detail_search_target(attempt)

        self.logger.get_processed_applications.assert_called_once()
        self.assertFalse((self.scratch / 'patent.json').exists())
        self.assertEqual(search_flow.metadata['cnipa_detail_attempt'], attempt)

    def test_request_without_attempt_cannot_be_bound_when_response_arrives(self):
        search_flow = self.search_response()
        self.scraper.request(search_flow)
        attempt = detail_attempt.begin_detail_attempt(APPLICATION_NO)

        self.scraper.response(search_flow)

        self.assertNotIn('cnipa_detail_attempt', search_flow.metadata)
        with self.assertRaises(detail_search.DetailSearchRetryableError):
            detail_search.wait_for_detail_search_target(attempt)

    def test_delayed_search_response_cannot_confirm_or_overwrite_a_later_attempt(self):
        first_attempt = detail_attempt.begin_detail_attempt(APPLICATION_NO)
        stale_search_flow = self.search_response()
        self.scraper.request(stale_search_flow)
        current_attempt = detail_attempt.begin_detail_attempt(APPLICATION_NO)

        self.scraper.response(stale_search_flow)

        self.assertEqual(stale_search_flow.metadata['cnipa_detail_attempt'], first_attempt)
        with self.assertRaises(detail_search.DetailSearchRetryableError):
            detail_search.wait_for_detail_search_target(current_attempt)

        current_search_flow = self.search_response()
        self.scraper.request(current_search_flow)
        self.scraper.response(current_search_flow)
        confirmed_cache = read_json_cache(str(self.scratch / 'search.json'))
        self.scraper.response(stale_search_flow)

        detail_search.wait_for_detail_search_target(current_attempt)
        self.assertEqual(read_json_cache(str(self.scratch / 'search.json')), confirmed_cache)

    def test_observing_search_keeps_request_url_headers_and_body_unchanged(self):
        attempt = detail_attempt.begin_detail_attempt(APPLICATION_NO)
        search_flow = self.search_response()
        search_flow.request = http.Request.make(
            'POST', SEARCH_URL + '?test-query=unchanged',
            content=b'{"test-query":"unchanged"}',
            headers={
                'Content-Type': 'application/json',
                'Authorization': 'test-credential',
                'X-Test-Header': 'unchanged',
            },
        )
        original_request = search_flow.request.get_state()

        self.scraper.request(search_flow)
        self.scraper.response(search_flow)

        detail_search.wait_for_detail_search_target(attempt)
        self.assertEqual(search_flow.request.get_state(), original_request)

    def test_both_collectors_bind_before_query_and_wait_for_receipt_before_click(self):
        for collector_module, collect_one, endpoint, payload in (
            (collect_fees, collect_fees.collect_one_fee, 'fyxx', FYXX_RESPONSE),
            (collect_fwxx, collect_fwxx.collect_one_fwxx, 'fwxx', FWXX_RESPONSE),
        ):
            with self.subTest(collector=collector_module.__name__), ExitStack() as patches:
                driver = MagicMock()
                driver.window_handles = ['search']
                driver.execute_script.side_effect = AssertionError('must not execute page scripts')
                source_reads = patches.enter_context(patch.object(
                    type(driver), 'page_source', new_callable=PropertyMock, create=True,
                    side_effect=AssertionError('must not read the browser document'),
                ))
                driver.close.side_effect = lambda: driver.window_handles.remove('detail')
                timeline = []
                patches.enter_context(patch.object(collector_module, 'is_browser_alive', return_value=True))
                patches.enter_context(patch.object(collector_module.time, 'sleep'))
                input_service = patches.enter_context(patch.object(collector_module, 'InputService'))

                def submit_query(*_args, **_kwargs):
                    attempt = detail_attempt.read_detail_attempt_marker()
                    self.assertEqual(attempt['application_no'], APPLICATION_NO)
                    self.assertEqual(timeline, [])
                    timeline.append('query')
                    search_flow = self.search_response()
                    self.scraper.request(search_flow)
                    self.scraper.response(search_flow)

                def confirm_search(attempt):
                    detail_search.wait_for_detail_search_target(attempt)
                    timeline.append('search-confirmed')

                def click_detail_or_menu(x, y, **_kwargs):
                    if (x, y) == (5, 6):
                        self.assertEqual(timeline, ['query', 'search-confirmed'])
                        timeline.append('detail-click')
                        driver.window_handles.append('detail')
                        identity_flow = sqxx_flow(APPLICATION_NO)
                        self.scraper.request(identity_flow)
                        self.scraper.response(identity_flow)
                    else:
                        self.assertEqual((x, y), (7, 8))
                        timeline.append('menu-click')
                        fields_flow = _make_flow(
                            f'https://cpquery.cponline.cnipa.gov.cn/api/view/gn/{endpoint}',
                            body=_json_body(payload),
                        )
                        self.scraper.request(fields_flow)
                        self.scraper.response(fields_flow)

                patches.enter_context(patch.object(
                    collector_module, 'wait_for_detail_search_target', side_effect=confirm_search,
                ))
                input_service.type_in_search.side_effect = submit_query
                input_service.move_and_click.side_effect = click_detail_or_menu

                collected_fields = collect_one(driver, APPLICATION_NO, 1, 2, 3, 4, 5, 6, 7, 8)

                self.assertTrue(collected_fields)
                self.assertEqual(timeline, ['query', 'search-confirmed', 'detail-click', 'menu-click'])
                driver.execute_script.assert_not_called()
                source_reads.assert_not_called()
                driver.refresh.assert_not_called()
                self.assertEqual(driver.window_handles, ['search'])
                self.assertIsNone(detail_attempt.read_detail_attempt_marker())

    def test_search_timeout_clears_attempt_without_clicking_or_refreshing(self):
        for collector_module, collect_one, retryable_error in (
            (collect_fees, collect_fees.collect_one_fee, detail_search.DetailSearchRetryableError),
            (collect_fwxx, collect_fwxx.collect_one_fwxx, collect_fwxx.FwxxCollectionRetryableError),
        ):
            with self.subTest(collector=collector_module.__name__), ExitStack() as patches:
                driver = MagicMock()
                driver.window_handles = ['search']
                patches.enter_context(patch.object(collector_module, 'is_browser_alive', return_value=True))
                input_service = patches.enter_context(patch.object(collector_module, 'InputService'))

                def submit_unanswered_query(*_args, **_kwargs):
                    self.assertEqual(detail_attempt.read_detail_attempt_marker()['application_no'], APPLICATION_NO)

                input_service.type_in_search.side_effect = submit_unanswered_query
                with self.assertRaises(retryable_error):
                    collect_one(driver, APPLICATION_NO, 1, 2, 3, 4, 5, 6, 7, 8)

                input_service.move_and_click.assert_not_called()
                driver.refresh.assert_not_called()
                driver.execute_script.assert_not_called()
                self.assertIsNone(detail_attempt.read_detail_attempt_marker())
