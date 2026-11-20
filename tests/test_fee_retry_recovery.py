"""Fee rounds attempt each case once and retain failures for later rounds."""

import io
import unittest
from argparse import Namespace
from contextlib import ExitStack, redirect_stdout
from functools import partial
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, Mock, patch

import collect_fees
import collection_checkpoint
import collection_health
import detail_search
from db_manager import PatentsDB
from detail_attempt import DetailCollectionFatalError, DetailIdentityTimeout


FIRST_APPLICATION = '202310411762X'
NEXT_APPLICATION = '2022114363024'
UNREGISTERED_APPLICATION = '2022114339006'
COMPLETE_FEE_SNAPSHOT = {
    'payable_fee_records': [],
    'paid_fee_records': [],
    'fee_receipt_dispatch_records': [],
    'fee_snapshot_at': '2026-09-23T08:00:00Z',
}


class FeeDetailRecoveryTests(unittest.TestCase):
    def setUp(self):
        patches = ExitStack()
        self.addCleanup(patches.close)
        self.driver = MagicMock()
        self.driver.window_handles = ['search']
        self.driver.current_window_handle = 'search'
        self.timeline = []
        self.elapsed = 0.0
        self.after_sleep = lambda: None

        def advance_clock(seconds):
            self.elapsed += seconds
            self.after_sleep()

        def select_window(handle):
            self.driver.current_window_handle = handle

        def open_detail(x, y, **_wait):
            if (x, y) == (5, 6):
                self.driver.window_handles.append('detail')

        self.driver.switch_to.window.side_effect = select_window
        self.driver.close.side_effect = lambda: self.driver.window_handles.remove(self.driver.current_window_handle)
        self.driver.refresh.side_effect = lambda: self.timeline.append('refresh')
        patches.enter_context(patch.object(collect_fees.time, 'sleep', side_effect=advance_clock))
        patches.enter_context(patch.object(collect_fees.time, 'monotonic', side_effect=lambda: self.elapsed))
        patches.enter_context(patch.object(detail_search, 'FWXX_DETAIL_OPEN_TIMEOUT', 1))
        patches.enter_context(patch.object(detail_search, 'raise_if_cnipa_login_required'))
        patches.enter_context(patch.object(collect_fees, 'raise_if_cnipa_login_required'))
        patches.enter_context(patch.object(collect_fees, 'is_browser_alive', return_value=True))
        patches.enter_context(patch.object(collect_fees, 'clear_cache_key'))
        self.search_target = patches.enter_context(patch.object(collect_fees, 'wait_for_detail_search_target'))
        self.input_service = patches.enter_context(patch.object(collect_fees, 'InputService'))
        self.input_service.move_and_click.side_effect = open_detail
        patches.enter_context(patch.object(collect_fees, 'begin_detail_attempt', return_value={
            'application_no': FIRST_APPLICATION, 'attempt_id': 'fee-attempt',
        }))
        revoke_attempt = Mock(side_effect=lambda _attempt: self.timeline.append('revoke'))
        patches.enter_context(patch.object(collect_fees, 'clear_matching_detail_attempt', revoke_attempt))
        patches.enter_context(patch.object(detail_search, 'clear_matching_detail_attempt', revoke_attempt))
        self.confirm_identity = patches.enter_context(patch.object(collect_fees, 'wait_for_detail_identity'))
        self.wait_for_fees = patches.enter_context(patch.object(collect_fees, 'wait_for_fee_snapshot', return_value={
            **COMPLETE_FEE_SNAPSHOT, 'detail_attempt_id': 'fee-attempt',
        }))
        self.collect_fee = partial(
            collect_fees.collect_one_fee, self.driver, FIRST_APPLICATION, 1, 2, 3, 4, 5, 6, 7, 8,
        )

    def test_delayed_detail_tab_is_awaited_without_a_second_link_click(self):
        self.input_service.move_and_click.side_effect = None

        def reveal_delayed_tab():
            if self.elapsed >= 0.4 and not self.confirm_identity.called:
                if self.driver.window_handles == ['search']:
                    self.driver.window_handles.append('detail')

        self.after_sleep = reveal_delayed_tab

        self.assertEqual(self.collect_fee(), COMPLETE_FEE_SNAPSHOT)
        self.search_target.assert_called_once_with({
            'application_no': FIRST_APPLICATION, 'attempt_id': 'fee-attempt',
        })
        self.assertEqual(
            [clicked.args for clicked in self.input_service.move_and_click.call_args_list],
            [(5, 6), (7, 8)],
        )
        self.assertEqual(self.driver.window_handles, ['search'])
        self.driver.refresh.assert_not_called()

    def test_no_detail_timeout_revokes_attempt_and_restores_search_before_next_case(self):
        self.input_service.move_and_click.side_effect = None

        with self.assertRaisesRegex(detail_search.DetailSearchRetryableError, '0 个'):
            self.collect_fee()

        self.assertEqual(self.timeline[:2], ['revoke', 'refresh'])
        self.assertEqual(self.driver.window_handles, ['search'])
        self.driver.switch_to.window.assert_called_with('search')
        self.input_service.move_and_click.assert_called_once()
        self.confirm_identity.assert_not_called()
        self.wait_for_fees.assert_not_called()

    def test_identity_timeout_closes_unverified_detail_before_next_case(self):
        self.confirm_identity.side_effect = DetailIdentityTimeout('未收到官方申请号')

        with self.assertRaisesRegex(detail_search.DetailSearchRetryableError, '未收到官方申请号'):
            self.collect_fee()

        self.assertEqual(self.timeline[:2], ['revoke', 'refresh'])
        self.driver.close.assert_called_once()
        self.assertEqual(self.driver.window_handles, ['search'])
        self.input_service.move_and_click.assert_called_once()
        self.wait_for_fees.assert_not_called()

    def test_failed_recovery_after_identity_timeout_still_stops_the_batch(self):
        self.confirm_identity.side_effect = DetailIdentityTimeout('未收到官方申请号')
        self.driver.refresh.side_effect = collect_fees.WebDriverException('refresh disconnected')

        with self.assertRaisesRegex(DetailCollectionFatalError, '无法恢复搜索页'):
            self.collect_fee()
        self.wait_for_fees.assert_not_called()

    def test_two_detail_windows_are_fatal_without_refreshing_an_ambiguous_page(self):
        self.input_service.move_and_click.side_effect = lambda *_args, **_wait: (
            self.driver.window_handles.extend(['detail', 'unexpected-detail'])
        )

        with self.assertRaisesRegex(DetailCollectionFatalError, '2 个新标签页'):
            self.collect_fee()
        self.driver.refresh.assert_not_called()
        self.confirm_identity.assert_not_called()
        self.wait_for_fees.assert_not_called()

    def test_official_identity_mismatch_remains_fatal(self):
        self.confirm_identity.side_effect = DetailCollectionFatalError('详情页申请号不匹配')

        with self.assertRaisesRegex(DetailCollectionFatalError, '申请号不匹配'):
            self.collect_fee()
        self.driver.refresh.assert_not_called()
        self.wait_for_fees.assert_not_called()

    def test_mouse_failsafe_does_not_become_a_retry(self):
        self.input_service.move_and_click.side_effect = collect_fees.pyautogui.FailSafeException('emergency stop')

        with self.assertRaisesRegex(DetailCollectionFatalError, '鼠标紧急停止'):
            self.collect_fee()
        self.driver.refresh.assert_not_called()
        self.wait_for_fees.assert_not_called()

    def test_late_extra_tab_prevents_returning_a_valid_fee_payload(self):
        def receive_fees(*_args):
            self.driver.window_handles.append('late-detail')
            return {**COMPLETE_FEE_SNAPSHOT, 'detail_attempt_id': 'fee-attempt'}

        self.wait_for_fees.side_effect = receive_fees

        with self.assertRaisesRegex(DetailCollectionFatalError, '未恢复唯一搜索页'):
            self.collect_fee()

    def test_partial_payload_keeps_unknown_sections_and_reports_which_are_missing(self):
        self.wait_for_fees.return_value = {
            'detail_attempt_id': 'fee-attempt',
            'payable_fee_records': [],
            'paid_fee_records': None,
            'fee_snapshot_at': COMPLETE_FEE_SNAPSHOT['fee_snapshot_at'],
            'unrelated_response_field': 'must not be persisted',
        }
        output = io.StringIO()

        with redirect_stdout(output):
            collected = self.collect_fee()

        self.assertEqual(collected['payable_fee_records'], [])
        self.assertIsNone(collected['paid_fee_records'])
        self.assertNotIn('fee_receipt_dispatch_records', collected)
        self.assertNotIn('detail_attempt_id', collected)
        self.assertNotIn('unrelated_response_field', collected)
        self.assertIn('已缴费', output.getvalue())
        self.assertIn('收据发文', output.getvalue())
        self.assertIn('不完整', output.getvalue())
        self.assertEqual(self.driver.window_handles, ['search'])


class FeeBatchSinglePassAccountingTests(unittest.TestCase):
    def setUp(self):
        patches = ExitStack()
        self.addCleanup(patches.close)
        temporary_directory = patches.enter_context(TemporaryDirectory(prefix='cnipa-fee-retry-'))
        self.scratch = Path(temporary_directory)
        self.checkpoint_path = self.scratch / 'checkpoint.txt'
        self.database_path = self.scratch / 'patents.db'
        self.db = PatentsDB(self.database_path)
        for application_no in (FIRST_APPLICATION, NEXT_APPLICATION):
            self.db.upsert({'application_no': application_no, 'timestamp': '2026-09-01T00:00:00Z'})
        patches.enter_context(patch.object(collect_fees, 'PATENTS_DB_FILE', self.database_path))
        patches.enter_context(patch.object(collect_fees, 'FEE_COLLECTION_CHECKPOINT_FILE', self.checkpoint_path))
        patches.enter_context(patch.object(collect_fees, 'DETECTION_LOG_JSONL_FILE', self.scratch / 'export.jsonl'))
        patches.enter_context(patch.object(collect_fees, 'FEE_UNMATCHED_FILE', str(self.scratch / 'unmatched.json')))
        patches.enter_context(patch.object(collection_checkpoint, 'COLLECTION_BATCHES_DIR', self.scratch / 'batches'))
        coordinates = patches.enter_context(patch.object(collect_fees, 'CoordinateService'))
        coordinates.load_search_coordinates.return_value = (1, 2, 3, 4)
        coordinates.load_detail_link_coordinates.return_value = (5, 6)
        coordinates.load_fee_menu_coordinates.return_value = (7, 8)
        self.browser = patches.enter_context(patch.object(collect_fees, 'BrowserService'))
        patches.enter_context(patch.object(collect_fees, 'countdown'))
        patches.enter_context(patch.object(collect_fees, 'is_browser_alive', return_value=True))
        patches.enter_context(patch.object(collect_fees, 'DetectionLogger'))
        patches.enter_context(patch.object(collect_fees.time, 'sleep'))
        self.collect_fee = patches.enter_context(patch.object(collect_fees, 'collect_one_fee'))

    def run_batch(self, application_nos):
        with collection_checkpoint.CollectionBatch.create('fees', self.checkpoint_path, application_nos) as checkpoint:
            self.batch_id = checkpoint.id
            collect_fees._collect_fee_batch(Namespace(test=None, url='https://example.invalid'), checkpoint)

    def test_navigation_failure_is_attempted_once_then_next_application_succeeds(self):
        self.collect_fee.side_effect = [
            detail_search.DetailSearchRetryableError('详情未打开'),
            COMPLETE_FEE_SNAPSHOT,
        ]

        with self.assertRaisesRegex(RuntimeError, '采集失败 1 条'):
            self.run_batch([FIRST_APPLICATION, NEXT_APPLICATION])

        self.assertEqual(
            [attempt.kwargs['application_no'] for attempt in self.collect_fee.call_args_list],
            [FIRST_APPLICATION, NEXT_APPLICATION],
        )
        failures = self.db.failed_collection_targets('fees')
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0]['application_no'], FIRST_APPLICATION)
        self.assertEqual(failures[0]['attempt_count'], 1)
        self.assertEqual(failures[0]['reason'], '详情未打开')
        self.assertEqual(self.db.get_record(NEXT_APPLICATION)['paid_fee_records'], [])
        self.assertEqual(self.checkpoint_path.read_text(), FIRST_APPLICATION + '\n')
        saved_batch = collection_checkpoint.read_collection_batch(self.batch_id)
        self.assertEqual([item['status'] for item in saved_batch['items']], ['failed', 'success'])

    def test_later_rounds_only_retry_failures_and_remove_them_after_success(self):
        self.collect_fee.side_effect = [
            detail_search.DetailSearchRetryableError('详情页尚未打开'), COMPLETE_FEE_SNAPSHOT,
        ]

        with self.assertRaisesRegex(RuntimeError, '采集失败 1 条'):
            self.run_batch([FIRST_APPLICATION, NEXT_APPLICATION])

        retry_targets = collect_fees.load_failed_fee_targets()
        self.assertEqual(retry_targets, [FIRST_APPLICATION])
        self.assertEqual(self.db.failed_collection_targets('fees')[0]['attempt_count'], 1)
        self.collect_fee.side_effect = None
        self.collect_fee.return_value = None

        with self.assertRaisesRegex(RuntimeError, '采集失败 1 条'):
            self.run_batch(retry_targets)

        self.assertEqual(self.db.failed_collection_targets('fees')[0]['attempt_count'], 2)
        retry_targets = collect_fees.load_failed_fee_targets()
        self.assertEqual(retry_targets, [FIRST_APPLICATION])
        self.collect_fee.return_value = COMPLETE_FEE_SNAPSHOT

        self.run_batch(retry_targets)

        self.assertEqual(
            [attempt.kwargs['application_no'] for attempt in self.collect_fee.call_args_list],
            [FIRST_APPLICATION, NEXT_APPLICATION, FIRST_APPLICATION, FIRST_APPLICATION],
        )
        self.assertEqual(self.db.failed_collection_targets('fees'), [])
        self.assertEqual(collect_fees.load_failed_fee_targets(), [])
        self.assertEqual(self.db.get_record(FIRST_APPLICATION)['paid_fee_records'], [])
        self.assertEqual(self.checkpoint_path.read_text(), '')
        self.assertEqual(collection_checkpoint.read_collection_batch(self.batch_id)['status'], 'completed')

    @patch.object(collection_health, 'WATCHDOG_FAILURE_THRESHOLD', 1)
    @patch.object(collection_health, 'record_collection_alert')
    def test_consecutive_ordinary_failures_do_not_stop_remaining_cases(self, record_alert):
        last_application = '2024103465076'
        self.db.upsert({'application_no': last_application, 'timestamp': '2026-09-01T00:00:00Z'})
        self.collect_fee.side_effect = [
            detail_search.DetailSearchRetryableError('详情页尚未打开'),
            None,
            COMPLETE_FEE_SNAPSHOT,
        ]

        with self.assertRaisesRegex(RuntimeError, '采集失败 2 条'):
            self.run_batch([FIRST_APPLICATION, NEXT_APPLICATION, last_application])

        self.assertEqual(
            [attempt.kwargs['application_no'] for attempt in self.collect_fee.call_args_list],
            [FIRST_APPLICATION, NEXT_APPLICATION, last_application],
        )
        failures = self.db.failed_collection_targets('fees')
        self.assertEqual({failure['application_no'] for failure in failures}, {FIRST_APPLICATION, NEXT_APPLICATION})
        self.assertEqual([failure['attempt_count'] for failure in failures], [1, 1])
        self.assertEqual(self.db.get_record(last_application)['paid_fee_records'], [])
        saved_batch = collection_checkpoint.read_collection_batch(self.batch_id)
        self.assertEqual([item['status'] for item in saved_batch['items']], ['failed', 'failed', 'success'])
        record_alert.assert_not_called()

    def test_unregistered_only_batch_never_opens_browser_or_creates_patent(self):
        with self.assertRaisesRegex(RuntimeError, '未建档'):
            self.run_batch([UNREGISTERED_APPLICATION])

        self.browser.launch_and_login.assert_not_called()
        self.collect_fee.assert_not_called()
        self.assertIsNone(self.db.get_record(UNREGISTERED_APPLICATION))
        failures = self.db.failed_collection_targets('fees')
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0]['reason'], 'not_found_in_db')
        self.assertEqual(failures[0]['attempt_count'], 1)
        self.assertEqual(self.checkpoint_path.read_text(), UNREGISTERED_APPLICATION + '\n')

    def test_mixed_batch_skips_unregistered_target_and_collects_registered_target(self):
        self.collect_fee.return_value = COMPLETE_FEE_SNAPSHOT

        with self.assertRaisesRegex(RuntimeError, '采集失败 1 条'):
            self.run_batch([UNREGISTERED_APPLICATION, NEXT_APPLICATION])

        self.collect_fee.assert_called_once()
        self.assertEqual(self.collect_fee.call_args.kwargs['application_no'], NEXT_APPLICATION)
        self.assertIsNone(self.db.get_record(UNREGISTERED_APPLICATION))
        self.assertEqual(self.db.get_record(NEXT_APPLICATION)['paid_fee_records'], [])
        self.assertEqual(self.checkpoint_path.read_text(), UNREGISTERED_APPLICATION + '\n')

    def test_login_expiry_stops_without_retry_or_ordinary_failure_record(self):
        self.collect_fee.side_effect = collect_fees.CNIPALoginRequired('登录已失效')

        with self.assertRaisesRegex(collect_fees.CNIPALoginRequired, '登录已失效'):
            self.run_batch([FIRST_APPLICATION, NEXT_APPLICATION])

        self.collect_fee.assert_called_once()
        self.assertEqual(self.db.failed_collection_targets('fees'), [])
        saved_batch = collection_checkpoint.read_collection_batch(self.batch_id)
        self.assertEqual([item['status'] for item in saved_batch['items']], ['interrupted', 'pending'])

    def test_fatal_identity_mismatch_stops_without_trying_the_next_application(self):
        self.collect_fee.side_effect = DetailCollectionFatalError('详情页申请号不匹配')

        with self.assertRaisesRegex(DetailCollectionFatalError, '申请号不匹配'):
            self.run_batch([FIRST_APPLICATION, NEXT_APPLICATION])

        self.collect_fee.assert_called_once()
        self.assertEqual(self.db.failed_collection_targets('fees'), [])
        self.assertEqual(self.checkpoint_path.read_text(), FIRST_APPLICATION + '\n' + NEXT_APPLICATION + '\n')

    def test_partial_fee_response_is_saved_once_and_retains_specific_missing_sections(self):
        self.collect_fee.return_value = {
            'payable_fee_records': [], 'fee_snapshot_at': COMPLETE_FEE_SNAPSHOT['fee_snapshot_at'],
        }

        with self.assertRaisesRegex(RuntimeError, '采集失败 1 条'):
            self.run_batch([FIRST_APPLICATION])

        self.collect_fee.assert_called_once()
        stored_record = self.db.get_record(FIRST_APPLICATION)
        self.assertEqual(stored_record['payable_fee_records'], [])
        self.assertIsNone(stored_record['paid_fee_records'])
        self.assertIsNone(stored_record['fee_receipt_dispatch_records'])
        self.assertEqual(self.db.failed_collection_targets('fees')[0]['reason'], 'incomplete_fee_payload')
        saved_batch = collection_checkpoint.read_collection_batch(self.batch_id)
        self.assertIn('已缴费', saved_batch['items'][0]['reason'])
        self.assertIn('收据发文', saved_batch['items'][0]['reason'])
        self.assertEqual(self.checkpoint_path.read_text(), FIRST_APPLICATION + '\n')


if __name__ == '__main__':
    unittest.main()
