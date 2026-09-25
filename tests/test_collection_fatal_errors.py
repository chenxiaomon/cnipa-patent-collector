#!/usr/bin/env python
# -*- coding: utf-8 -*-

import unittest
from argparse import Namespace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, PropertyMock, call, patch

import collect_fees
import collect_fwxx
import collection_checkpoint
from collection_checkpoint import list_collection_batches, read_collection_batch


def collection_arguments() -> Namespace:
    return Namespace(
        test=None,
        input=None,
        app=None,
        force=False,
        url="https://example.invalid",
    )


class TestCollectionFatalErrors(unittest.TestCase):
    def setUp(self):
        temporary_directory = TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        self.checkpoint_file = Path(temporary_directory.name) / 'resume.txt'
        directory_patch = patch.object(collection_checkpoint, 'COLLECTION_BATCHES_DIR', Path(temporary_directory.name) / 'batches')
        directory_patch.start()
        self.addCleanup(directory_patch.stop)
        for collector, constant in (
            (collect_fwxx, 'FWXX_COLLECTION_CHECKPOINT_FILE'),
            (collect_fees, 'FEE_COLLECTION_CHECKPOINT_FILE'),
        ):
            checkpoint_patch = patch.object(collector, constant, self.checkpoint_file)
            checkpoint_patch.start()
            self.addCleanup(checkpoint_patch.stop)
            coordinate_patch = patch.object(collector, 'CoordinateService')
            coordinates = coordinate_patch.start()
            self.addCleanup(coordinate_patch.stop)
            coordinates.load_search_coordinates.return_value = (1, 2, 3, 4)
            coordinates.load_fwxx_coordinates.return_value = (5, 6, 7, 8)
            coordinates.load_detail_link_coordinates.return_value = (5, 6)
            coordinates.load_fee_menu_coordinates.return_value = (7, 8)

    @patch(
        "collect_fwxx.BrowserService.launch_and_login",
        side_effect=RuntimeError("browser failed"),
    )
    @patch("collect_fwxx.load_target_applications", return_value=["A"])
    def test_fwxx_batch_propagates_browser_startup_failure(
        self,
        _load_targets,
        _launch_browser,
    ):
        with self.assertRaisesRegex(RuntimeError, "browser failed"):
            collect_fwxx._run_fwxx_collection(collection_arguments())
        self.assertEqual(self.checkpoint_file.read_text(encoding='utf-8'), 'A\n')

    @patch(
        "collect_fees.BrowserService.launch_and_login",
        side_effect=RuntimeError("browser failed"),
    )
    @patch("collect_fees.load_fee_dataset_targets", return_value=["A"])
    def test_fee_batch_propagates_browser_startup_failure(
        self,
        _load_targets,
        _launch_browser,
    ):
        with patch.object(collect_fees, 'PatentsDB') as fee_database:
            fee_database.return_value.get_record.return_value = {'application_no': 'A'}
            with self.assertRaisesRegex(RuntimeError, "browser failed"):
                collect_fees._run_fee_collection(collection_arguments())
        self.assertEqual(self.checkpoint_file.read_text(encoding='utf-8'), 'A\n')

    def test_fwxx_single_collection_propagates_browser_exit_before_query(self):
        with patch('collect_fwxx.is_browser_alive', return_value=False):
            with self.assertRaises(collect_fwxx.DetailCollectionFatalError):
                collect_fwxx.collect_one_fwxx(MagicMock(), 'A', 1, 2, 3, 4, 5, 6, 7, 8)

    def test_fee_single_collection_propagates_browser_exit_before_query(self):
        with patch('collect_fees.is_browser_alive', return_value=False):
            with self.assertRaises(collect_fees.DetailCollectionFatalError):
                collect_fees.collect_one_fee(MagicMock(), 'A', 1, 2, 3, 4, 5, 6, 7, 8)

    def test_fwxx_driver_failure_before_detail_page_stops_batch(self):
        driver = MagicMock()
        type(driver).window_handles = PropertyMock(side_effect=collect_fwxx.WebDriverException('session lost'))
        with patch('collect_fwxx.is_browser_alive', return_value=True):
            with self.assertRaises(collect_fwxx.DetailCollectionFatalError):
                collect_fwxx.collect_one_fwxx(driver, 'A', 1, 2, 3, 4, 5, 6, 7, 8)

    def test_fee_driver_failure_before_detail_page_stops_batch(self):
        driver = MagicMock()
        type(driver).window_handles = PropertyMock(side_effect=collect_fees.WebDriverException('session lost'))
        with patch('collect_fees.is_browser_alive', return_value=True):
            with self.assertRaises(collect_fees.DetailCollectionFatalError):
                collect_fees.collect_one_fee(driver, 'A', 1, 2, 3, 4, 5, 6, 7, 8)


class DetailBatchInterruptionCases:
    def setUp(self):
        temporary_directory = TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        self.checkpoint_file = Path(temporary_directory.name) / 'resume.txt'
        self._patch(collection_checkpoint, 'COLLECTION_BATCHES_DIR', Path(temporary_directory.name) / 'batches')
        self._patch(self.collector, self.checkpoint_constant, self.checkpoint_file)
        self._patch(self.collector, self.target_loader, return_value=['A', 'B'])
        coordinates = self._patch(self.collector, 'CoordinateService')
        coordinates.load_search_coordinates.return_value = (1, 2, 3, 4)
        coordinates.load_fwxx_coordinates.return_value = (5, 6, 7, 8)
        coordinates.load_detail_link_coordinates.return_value = (5, 6)
        coordinates.load_fee_menu_coordinates.return_value = (7, 8)
        self.browser_service = self._patch(self.collector, 'BrowserService')
        self._patch(self.collector, 'countdown')
        self.browser_alive = self._patch(self.collector, 'is_browser_alive', return_value=True)
        self.collect_one = self._patch(self.collector, self.single_collection, return_value={'captured': True})
        self.persist_fields = self._patch(self.collector, self.persistence_operation, return_value=self.stored_snapshot)
        self.logger_class = self._patch(self.collector, 'DetectionLogger')
        self._patch(self.collector, 'PatentsDB')
        import db_manager
        self._patch(db_manager, 'PatentsDB')

    def _patch(self, owner, attribute, *args, **kwargs):
        attribute_patch = patch.object(owner, attribute, *args, **kwargs)
        mocked_attribute = attribute_patch.start()
        self.addCleanup(attribute_patch.stop)
        return mocked_attribute

    def test_browser_closed_before_first_application_stops_batch(self):
        self.browser_alive.return_value = False

        with self.assertRaises(self.collector.DetailCollectionFatalError):
            getattr(self.collector, self.batch_collection)(collection_arguments())

        self.collect_one.assert_not_called()
        self.assertEqual(self.checkpoint_file.read_text(encoding='utf-8'), 'A\nB\n')
        self.browser_service.launch_and_login.return_value.quit.assert_called_once()

    def test_browser_closed_after_success_preserves_only_unfinished_targets(self):
        self.browser_alive.side_effect = [True, False]

        with self.assertRaises(self.collector.DetailCollectionFatalError):
            getattr(self.collector, self.batch_collection)(collection_arguments())

        self.assertEqual(self.collect_one.call_count, 1)
        self.assertEqual(self.checkpoint_file.read_text(encoding='utf-8'), 'B\n')
        self.browser_service.launch_and_login.return_value.quit.assert_called_once()

    def test_fatal_error_during_collection_preserves_current_application(self):
        self.collect_one.side_effect = [
            {'captured': True},
            self.collector.DetailCollectionFatalError('detail session lost'),
        ]

        with self.assertRaisesRegex(self.collector.DetailCollectionFatalError, 'detail session lost'):
            getattr(self.collector, self.batch_collection)(collection_arguments())

        self.assertEqual(self.checkpoint_file.read_text(encoding='utf-8'), 'B\n')
        self.browser_service.launch_and_login.return_value.quit.assert_called_once()

    def test_user_interrupt_preserves_current_application_and_propagates(self):
        self.collect_one.side_effect = [{'captured': True}, KeyboardInterrupt()]

        with self.assertRaises(KeyboardInterrupt):
            getattr(self.collector, self.batch_collection)(collection_arguments())

        self.assertEqual(self.checkpoint_file.read_text(encoding='utf-8'), 'B\n')
        self.browser_service.launch_and_login.return_value.quit.assert_called_once()
        batch = read_collection_batch(list_collection_batches()[0]['id'])
        self.assertEqual([item['status'] for item in batch['items']], ['success', 'interrupted'])

    def test_failed_application_stays_in_resume_list_after_later_success(self):
        self.collect_one.side_effect = [None, {'captured': True}]

        with self.assertRaisesRegex(RuntimeError, '采集失败 1 条'):
            getattr(self.collector, self.batch_collection)(collection_arguments())

        self.assertEqual(self.collect_one.call_count, 2)
        self.logger_class.return_value.export_to_excel.assert_called_once()
        self.assertEqual(self.checkpoint_file.read_text(encoding='utf-8'), 'A\n')
        batch = read_collection_batch(list_collection_batches()[0]['id'])
        self.assertEqual([item['status'] for item in batch['items']], ['failed', 'success'])
        self.assertTrue(batch['items'][0]['reason'])

    def test_resume_keeps_original_batch_and_bypasses_target_loaders(self):
        arguments = collection_arguments()
        arguments.test = 1
        getattr(self.collector, self.batch_collection)(arguments)
        batch_id = list_collection_batches()[0]['id']
        self.assertEqual(read_collection_batch(batch_id)['status'], 'paused')

        arguments.test = None
        arguments.resume_batch = batch_id
        getattr(self.collector, self.batch_collection)(arguments)

        getattr(self.collector, self.target_loader).assert_called_once()
        self.assertEqual([call.kwargs['application_no'] for call in self.collect_one.call_args_list], ['A', 'B'])
        self.assertEqual(len(list_collection_batches()), 1)
        completed = read_collection_batch(batch_id)
        self.assertEqual((completed['status'], len(completed['runs'])), ('completed', 2))

    def test_successful_batch_returns_normally_after_export(self):
        getattr(self.collector, self.batch_collection)(collection_arguments())

        self.assertEqual(self.collect_one.call_count, 2)
        self.logger_class.return_value.export_to_excel.assert_called_once()
        self.assertEqual(self.checkpoint_file.read_text(encoding='utf-8'), '')

    def test_limited_success_preserves_unselected_application_in_same_resume_file(self):
        self.checkpoint_file.write_text('202310411762X\n2024110065970\n', encoding='utf-8')
        arguments = collection_arguments()
        arguments.input = str(self.checkpoint_file)
        arguments.force = True
        arguments.test = 1

        getattr(self.collector, self.batch_collection)(arguments)

        self.assertEqual(self.collect_one.call_count, 1)
        self.logger_class.return_value.export_to_excel.assert_called_once()
        self.assertEqual(self.checkpoint_file.read_text(encoding='utf-8'), '2024110065970\n')

    def test_limited_failure_preserves_failed_and_unselected_applications(self):
        self.checkpoint_file.write_text('202310411762X\n2024110065970\n', encoding='utf-8')
        arguments = collection_arguments()
        arguments.input = str(self.checkpoint_file)
        arguments.force = True
        arguments.test = 1
        self.collect_one.return_value = None

        with self.assertRaisesRegex(RuntimeError, '采集失败 1 条'):
            getattr(self.collector, self.batch_collection)(arguments)

        self.assertEqual(self.collect_one.call_count, 1)
        self.assertEqual(
            self.checkpoint_file.read_text(encoding='utf-8'),
            '202310411762X\n2024110065970\n',
        )

    def test_persistence_failure_and_unattempted_application_both_remain(self):
        self.persist_fields.return_value = None
        self.browser_alive.side_effect = [True, False]

        with self.assertRaises(self.collector.DetailCollectionFatalError):
            getattr(self.collector, self.batch_collection)(collection_arguments())

        self.assertEqual(self.checkpoint_file.read_text(encoding='utf-8'), 'A\nB\n')

    def test_initial_checkpoint_failure_prevents_browser_start(self):
        with patch.object(Path, 'replace', side_effect=OSError('disk unavailable')):
            with self.assertRaisesRegex(OSError, 'disk unavailable'):
                getattr(self.collector, self.batch_collection)(collection_arguments())

        self.browser_service.launch_and_login.assert_not_called()


class TestFwxxBatchInterruptions(DetailBatchInterruptionCases, unittest.TestCase):
    collector = collect_fwxx
    checkpoint_constant = 'FWXX_COLLECTION_CHECKPOINT_FILE'
    target_loader = 'load_target_applications'
    single_collection = 'collect_one_fwxx'
    persistence_operation = 'persist_fwxx_fields'
    batch_collection = '_run_fwxx_collection'
    stored_snapshot = True

    def setUp(self):
        super().setUp()
        self._patch(collect_fwxx.time, 'sleep')
        self.failure_streak = self._patch(collect_fwxx, 'CollectionFailureStreak').return_value

    def test_first_failure_then_success_persists_each_application_once(self):
        first_fields = {'fwxx_list': [{'fawenmc': 'first application'}]}
        second_fields = {'fwxx_list': [{'fawenmc': 'second application'}]}
        self.collect_one.side_effect = [collect_fwxx.FwxxCollectionRetryableError('搜索尚未就绪'), first_fields, second_fields]

        collect_fwxx._run_fwxx_collection(collection_arguments())

        self.assertEqual(
            [attempt.kwargs['application_no'] for attempt in self.collect_one.call_args_list],
            ['A', 'A', 'B'],
        )
        self.assertEqual(self.persist_fields.call_args_list, [call('A', first_fields), call('B', second_fields)])
        self.failure_streak.record_failure.assert_not_called()
        self.assertEqual(self.failure_streak.record_success.call_count, 2)
        self.assertEqual(self.checkpoint_file.read_text(encoding='utf-8'), '')
        batch = read_collection_batch(list_collection_batches()[0]['id'])
        self.assertEqual(batch['status'], 'completed')
        self.assertEqual([item['attempt_count'] for item in batch['items']], [1, 1])
        self.assertEqual([attempt['status'] for attempt in batch['runs'][-1]['attempts']], ['success', 'success'])

    def test_failed_application_stays_in_resume_list_after_later_success(self):
        collected_fields = {'captured': True}
        self.collect_one.side_effect = [
            collect_fwxx.FwxxCollectionRetryableError('搜索尚未就绪'),
            collect_fwxx.FwxxCollectionRetryableError('发文缓存超时'),
            collected_fields,
        ]

        with self.assertRaisesRegex(RuntimeError, '采集失败 1 条'):
            collect_fwxx._run_fwxx_collection(collection_arguments())

        self.assertEqual(
            [attempt.kwargs['application_no'] for attempt in self.collect_one.call_args_list],
            ['A', 'A', 'B'],
        )
        self.persist_fields.assert_called_once_with('B', collected_fields)
        self.failure_streak.record_failure.assert_called_once_with()
        self.failure_streak.record_success.assert_called_once_with()
        self.logger_class.return_value.export_to_excel.assert_called_once()
        self.assertEqual(self.checkpoint_file.read_text(encoding='utf-8'), 'A\n')
        batch = read_collection_batch(list_collection_batches()[0]['id'])
        self.assertEqual(batch['status'], 'failed')
        self.assertEqual([item['status'] for item in batch['items']], ['failed', 'success'])
        self.assertEqual([item['attempt_count'] for item in batch['items']], [1, 1])
        self.assertEqual(batch['items'][0]['reason'], '第 1 次: 搜索尚未就绪；第 2 次: 发文缓存超时')
        self.assertEqual([attempt['status'] for attempt in batch['runs'][-1]['attempts']], ['failed', 'success'])

    def test_limited_failure_preserves_failed_and_unselected_applications(self):
        self.checkpoint_file.write_text('202310411762X\n2024110065970\n', encoding='utf-8')
        arguments = collection_arguments()
        arguments.input = str(self.checkpoint_file)
        arguments.force = True
        arguments.test = 1
        self.collect_one.side_effect = collect_fwxx.FwxxCollectionRetryableError('发文缓存超时')

        with self.assertRaisesRegex(RuntimeError, '采集失败 1 条'):
            collect_fwxx._run_fwxx_collection(arguments)

        self.assertEqual(
            [attempt.kwargs['application_no'] for attempt in self.collect_one.call_args_list],
            ['202310411762X', '202310411762X'],
        )
        self.failure_streak.record_failure.assert_called_once_with()
        self.failure_streak.record_success.assert_not_called()
        self.persist_fields.assert_not_called()
        self.assertEqual(
            self.checkpoint_file.read_text(encoding='utf-8'),
            '202310411762X\n2024110065970\n',
        )

    def test_fatal_error_is_not_retried_or_counted_as_recoverable_failure(self):
        self.collect_one.side_effect = collect_fwxx.DetailCollectionFatalError('official identity mismatch')

        with self.assertRaisesRegex(collect_fwxx.DetailCollectionFatalError, 'official identity mismatch'):
            collect_fwxx._run_fwxx_collection(collection_arguments())

        self.collect_one.assert_called_once()
        self.persist_fields.assert_not_called()
        self.failure_streak.record_failure.assert_not_called()
        self.failure_streak.record_success.assert_not_called()
        self.assertEqual(self.checkpoint_file.read_text(encoding='utf-8'), 'A\nB\n')
        batch = read_collection_batch(list_collection_batches()[0]['id'])
        self.assertEqual([item['status'] for item in batch['items']], ['interrupted', 'pending'])
        self.browser_service.launch_and_login.return_value.quit.assert_called_once()

    def test_failure_streak_counts_exhausted_applications_instead_of_attempts(self):
        import collection_health

        self._patch(collect_fwxx, 'CollectionFailureStreak', collection_health.CollectionFailureStreak)
        self._patch(collection_health, 'WATCHDOG_FAILURE_THRESHOLD', 2)
        record_alert = self._patch(collection_health, 'record_collection_alert')
        self._patch(collect_fwxx, 'load_target_applications', return_value=['A', 'B', 'C'])
        self.collect_one.side_effect = collect_fwxx.FwxxCollectionRetryableError('发文缓存超时')

        with self.assertRaisesRegex(collect_fwxx.CollectionFailureStreakExceeded, '连续失败 2 条'):
            collect_fwxx._run_fwxx_collection(collection_arguments())

        self.assertEqual(
            [attempt.kwargs['application_no'] for attempt in self.collect_one.call_args_list],
            ['A', 'A', 'B', 'B'],
        )
        self.persist_fields.assert_not_called()
        record_alert.assert_called_once()
        self.assertEqual(self.checkpoint_file.read_text(encoding='utf-8'), 'A\nB\nC\n')
        batch = read_collection_batch(list_collection_batches()[0]['id'])
        self.assertEqual([item['status'] for item in batch['items']], ['failed', 'failed', 'pending'])

    def test_interruption_during_second_attempt_preserves_current_application(self):
        collected_fields = {'captured': True}
        self.collect_one.side_effect = [collected_fields, collect_fwxx.FwxxCollectionRetryableError('发文缓存超时'), KeyboardInterrupt()]

        with self.assertRaises(KeyboardInterrupt):
            collect_fwxx._run_fwxx_collection(collection_arguments())

        self.assertEqual(
            [attempt.kwargs['application_no'] for attempt in self.collect_one.call_args_list],
            ['A', 'B', 'B'],
        )
        self.persist_fields.assert_called_once_with('A', collected_fields)
        self.failure_streak.record_failure.assert_not_called()
        self.failure_streak.record_success.assert_called_once_with()
        self.assertEqual(self.checkpoint_file.read_text(encoding='utf-8'), 'B\n')
        batch = read_collection_batch(list_collection_batches()[0]['id'])
        self.assertEqual(batch['status'], 'interrupted')
        self.assertEqual([item['status'] for item in batch['items']], ['success', 'interrupted'])
        self.assertEqual([item['attempt_count'] for item in batch['items']], [1, 1])
        self.assertEqual([attempt['status'] for attempt in batch['runs'][-1]['attempts']], ['success', 'interrupted'])
        self.browser_service.launch_and_login.return_value.quit.assert_called_once()


class TestFeeBatchInterruptions(DetailBatchInterruptionCases, unittest.TestCase):
    collector = collect_fees
    checkpoint_constant = 'FEE_COLLECTION_CHECKPOINT_FILE'
    target_loader = 'load_fee_dataset_targets'
    single_collection = 'collect_one_fee'
    persistence_operation = 'persist_fee_fields'
    batch_collection = '_run_fee_collection'
    stored_snapshot = {
        'payable_fee_records': [],
        'paid_fee_records': [],
        'fee_receipt_dispatch_records': [],
    }

    def setUp(self):
        super().setUp()
        collect_fees.PatentsDB.return_value.get_record.side_effect = lambda application_no: {
            'application_no': application_no,
        }


if __name__ == "__main__":
    unittest.main()
