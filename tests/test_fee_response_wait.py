"""Fee responses from one detail attempt must finish before closing its tab."""

import unittest
from contextlib import ExitStack
from unittest.mock import patch

import collect_fees


APPLICATION_NO = '202310411762X'
ATTEMPT_ID = 'current-fee-attempt'


class FeeResponseWaitTests(unittest.TestCase):
    def setUp(self):
        patches = ExitStack()
        self.addCleanup(patches.close)
        self.elapsed = 0.0
        self.cache_snapshots = [{}]
        patches.enter_context(patch.object(collect_fees, 'FWXX_CACHE_POLL_TIMEOUT', 10))
        patches.enter_context(patch.object(collect_fees.time, 'monotonic', side_effect=lambda: self.elapsed))

        def advance_clock(seconds):
            self.elapsed += seconds

        self.sleep = patches.enter_context(patch.object(collect_fees.time, 'sleep', side_effect=advance_clock))
        self.read_cache = patches.enter_context(patch.object(collect_fees, 'read_json_cache'))
        self.read_cache.side_effect = lambda _cache_path: (
            self.cache_snapshots.pop(0) if len(self.cache_snapshots) > 1 else self.cache_snapshots[0]
        )
        self.check_login = patches.enter_context(patch.object(collect_fees, 'raise_if_cnipa_login_required'))

    def test_waits_for_three_required_responses_from_the_current_attempt(self):
        payable = {'detail_attempt_id': ATTEMPT_ID, 'payable_fee_records': []}
        paid = {**payable, 'paid_fee_records': [{'fee_name': '申请费', 'amount': '900.00'}]}
        complete = {**paid, 'fee_receipt_dispatch_records': []}
        self.cache_snapshots = [
            {APPLICATION_NO: payable},
            {APPLICATION_NO: paid},
            {APPLICATION_NO: complete},
        ]

        collected = collect_fees.wait_for_fee_snapshot(APPLICATION_NO, ATTEMPT_ID)

        self.assertEqual(collected, complete)
        self.assertEqual(self.read_cache.call_count, 3)
        self.assertGreater(self.elapsed, 0)
        self.assertLess(self.elapsed, collect_fees.FWXX_CACHE_POLL_TIMEOUT)

    def test_empty_required_lists_are_complete_without_optional_late_fees(self):
        complete = {
            'detail_attempt_id': ATTEMPT_ID,
            'payable_fee_records': [],
            'paid_fee_records': [],
            'fee_receipt_dispatch_records': [],
        }
        self.cache_snapshots = [{APPLICATION_NO: complete}]

        self.assertEqual(collect_fees.wait_for_fee_snapshot(APPLICATION_NO, ATTEMPT_ID), complete)
        self.sleep.assert_not_called()

    def test_old_attempt_cannot_satisfy_wait_even_with_all_fee_sections(self):
        old_complete = {
            'detail_attempt_id': 'previous-attempt',
            'payable_fee_records': [],
            'paid_fee_records': [],
            'fee_receipt_dispatch_records': [],
        }
        current_complete = {**old_complete, 'detail_attempt_id': ATTEMPT_ID}
        self.cache_snapshots = [
            {APPLICATION_NO: old_complete},
            {APPLICATION_NO: current_complete},
        ]

        self.assertEqual(collect_fees.wait_for_fee_snapshot(APPLICATION_NO, ATTEMPT_ID), current_complete)
        self.assertEqual(self.read_cache.call_count, 2)

    def test_timeout_preserves_latest_partial_from_this_attempt_without_merging_snapshots(self):
        first = {'detail_attempt_id': ATTEMPT_ID, 'payable_fee_records': []}
        latest = {'detail_attempt_id': ATTEMPT_ID, 'paid_fee_records': []}
        old_complete = {
            'detail_attempt_id': 'previous-attempt',
            'payable_fee_records': [],
            'paid_fee_records': [],
            'fee_receipt_dispatch_records': [],
        }
        self.cache_snapshots = [
            {APPLICATION_NO: first},
            {APPLICATION_NO: latest},
            {APPLICATION_NO: old_complete},
        ]
        with patch.object(collect_fees, 'FWXX_CACHE_POLL_TIMEOUT', 2):
            collected = collect_fees.wait_for_fee_snapshot(APPLICATION_NO, ATTEMPT_ID)

        self.assertEqual(collected, latest)
        self.assertNotIn('payable_fee_records', collected)
        self.assertNotIn('fee_receipt_dispatch_records', collected)
        self.assertEqual(self.elapsed, 2)

    def test_absent_current_attempt_returns_none_at_deadline(self):
        self.cache_snapshots = [{APPLICATION_NO: {
            'detail_attempt_id': 'previous-attempt',
            'payable_fee_records': [],
            'paid_fee_records': [],
            'fee_receipt_dispatch_records': [],
        }}]
        with patch.object(collect_fees, 'FWXX_CACHE_POLL_TIMEOUT', 1):
            self.assertIsNone(collect_fees.wait_for_fee_snapshot(APPLICATION_NO, ATTEMPT_ID))
        self.assertEqual(self.elapsed, 1)

    def test_null_required_section_does_not_close_the_tab_early(self):
        incomplete = {
            'detail_attempt_id': ATTEMPT_ID,
            'payable_fee_records': [],
            'paid_fee_records': None,
            'fee_receipt_dispatch_records': [],
        }
        complete = {**incomplete, 'paid_fee_records': []}
        self.cache_snapshots = [
            {APPLICATION_NO: incomplete},
            {APPLICATION_NO: complete},
        ]

        self.assertEqual(collect_fees.wait_for_fee_snapshot(APPLICATION_NO, ATTEMPT_ID), complete)
        self.assertEqual(self.read_cache.call_count, 2)

    def test_login_expiry_during_wait_is_propagated_instead_of_returning_partial(self):
        self.cache_snapshots = [{APPLICATION_NO: {
            'detail_attempt_id': ATTEMPT_ID, 'payable_fee_records': [],
        }}]
        self.check_login.side_effect = [None, collect_fees.CNIPALoginRequired('登录过期')]

        with self.assertRaisesRegex(collect_fees.CNIPALoginRequired, '登录过期'):
            collect_fees.wait_for_fee_snapshot(APPLICATION_NO, ATTEMPT_ID)
        self.assertLess(self.elapsed, collect_fees.FWXX_CACHE_POLL_TIMEOUT)

    def test_login_expiry_at_timeout_boundary_still_prevents_retry(self):
        self.cache_snapshots = [{APPLICATION_NO: {
            'detail_attempt_id': ATTEMPT_ID, 'payable_fee_records': [],
        }}]

        def expire_at_deadline():
            if self.elapsed >= 1:
                raise collect_fees.CNIPALoginRequired('截止时登录过期')

        self.check_login.side_effect = expire_at_deadline
        with patch.object(collect_fees, 'FWXX_CACHE_POLL_TIMEOUT', 1):
            with self.assertRaisesRegex(collect_fees.CNIPALoginRequired, '截止时登录过期'):
                collect_fees.wait_for_fee_snapshot(APPLICATION_NO, ATTEMPT_ID)


if __name__ == '__main__':
    unittest.main()
