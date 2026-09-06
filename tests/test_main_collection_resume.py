import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

import main_automation
from collection_health import CollectionFailureStreakExceeded
from db_manager import PENDING_STATUS_CODE, PatentsDB
from detection_logger import DetectionLogger, DetectionRecord
from retry_failed import failed_retry_records


class TestExceptionalSearchRetry(unittest.TestCase):
    def test_query_exception_is_persisted_as_retryable_failure(self):
        with TemporaryDirectory() as temporary_directory:
            database_file = Path(temporary_directory) / 'patents.db'
            with patch('detection_logger.PATENTS_DB_FILE', database_file):
                logger = DetectionLogger(str(Path(temporary_directory) / 'records.jsonl'))
            patent_db = PatentsDB(database_file)
            patent_db.upsert({
                'application_no': '202310411762X',
                'status_code': PENDING_STATUS_CODE,
            })
            patent_db.upsert({
                'application_no': '2024110065970',
                'status_code': None,
            })

            with (
                patch('main_automation.is_browser_alive', return_value=True),
                patch('main_automation.clear_cache_key'),
                patch('main_automation.InputService.type_in_search', side_effect=RuntimeError('query failed')),
            ):
                attempted_record = main_automation.search_application(
                    MagicMock(), '202310411762X', 1, 2, 3, 4, logger
                )

            self.assertEqual(attempted_record.status_code, 0)
            retry_records = failed_retry_records(patent_db)
            self.assertEqual([record['application_no'] for record in retry_records], ['202310411762X'])
            self.assertEqual(retry_records[0]['error_message'], 'query failed')

    def test_browser_closed_before_query_does_not_create_failure_record(self):
        logger = MagicMock()
        with patch('main_automation.is_browser_alive', return_value=False):
            attempted_record = main_automation.search_application(
                MagicMock(), '202310411762X', 1, 2, 3, 4, logger
            )

        self.assertIsNone(attempted_record)
        logger.add_record.assert_not_called()
        logger.upsert_record.assert_not_called()


class TestMainCollectionResume(unittest.TestCase):
    def setUp(self):
        temporary_directory = TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        self.checkpoint_file = Path(temporary_directory.name) / 'resume.txt'
        self._patch('MAIN_COLLECTION_CHECKPOINT_FILE', self.checkpoint_file)
        self._patch('FORCE_UPDATE_FLAG', Path(temporary_directory.name) / 'force.flag')
        self._patch('load_search_list', return_value=['A', 'B'])
        self.logger_class = self._patch('DetectionLogger')
        self.logger_class.return_value.get_pending_applications.return_value = ['A', 'B']
        self.logger_class.return_value.get_stats.return_value = {'total': 0}
        self.logger_class.return_value.log_file = str(Path(temporary_directory.name) / 'records.jsonl')
        self.browser_service = self._patch('BrowserService')
        self.browser_alive = self._patch('is_browser_alive', return_value=True)
        coordinates = self._patch('CoordinateService')
        coordinates.load_or_record_search_coordinates.return_value = (1, 2, 3, 4)
        self._patch('AUTOMATION_STARTUP_COUNTDOWN', 0)
        self._patch('stop_virtual_display')
        self._patch('write_collection_start_heartbeat')
        self._patch('write_collection_progress_heartbeat')
        self._patch('write_collection_stopped_heartbeat')
        sleep_patch = patch('main_automation.time.sleep')
        sleep_patch.start()
        self.addCleanup(sleep_patch.stop)
        self.search_one = self._patch('search_application')
        self.search_one.side_effect = [
            DetectionRecord(application_no='A', status_code=200),
            DetectionRecord(application_no='B', status_code=200),
        ]

    def _patch(self, attribute, *args, **kwargs):
        attribute_patch = patch.object(main_automation, attribute, *args, **kwargs)
        mocked_attribute = attribute_patch.start()
        self.addCleanup(attribute_patch.stop)
        return mocked_attribute

    def test_browser_exit_after_success_preserves_unattempted_application(self):
        self.browser_alive.side_effect = [True, False]

        with self.assertRaisesRegex(RuntimeError, '浏览器进程意外退出'):
            main_automation.run_automation()

        self.assertEqual(self.checkpoint_file.read_text(encoding='utf-8'), 'B\n')
        self.browser_service.launch_and_login.return_value.quit.assert_called_once()

    def test_browser_exit_inside_final_search_does_not_complete_batch(self):
        self.search_one.side_effect = [DetectionRecord(application_no='A', status_code=200), None]

        with self.assertRaisesRegex(RuntimeError, '本条未采集'):
            main_automation.run_automation()

        self.assertEqual(self.checkpoint_file.read_text(encoding='utf-8'), 'B\n')

    def test_user_interrupt_propagates_and_preserves_current_application(self):
        self.search_one.side_effect = [DetectionRecord(application_no='A', status_code=200), KeyboardInterrupt()]

        with self.assertRaises(KeyboardInterrupt):
            main_automation.run_automation()

        self.assertEqual(self.checkpoint_file.read_text(encoding='utf-8'), 'B\n')
        self.browser_service.launch_and_login.return_value.quit.assert_called_once()

    def test_failed_and_unattempted_applications_survive_failure_streak(self):
        self.search_one.side_effect = [DetectionRecord(application_no='A', status_code=0)]
        streak_class = self._patch('CollectionFailureStreak')
        streak_class.return_value.record_failure.side_effect = CollectionFailureStreakExceeded('stopped')

        with self.assertRaises(CollectionFailureStreakExceeded):
            main_automation.run_automation()

        self.assertEqual(self.checkpoint_file.read_text(encoding='utf-8'), 'A\nB\n')

    def test_startup_failure_preserves_whole_batch(self):
        self.browser_service.launch_and_login.side_effect = RuntimeError('startup failed')

        with self.assertRaisesRegex(RuntimeError, 'startup failed'):
            main_automation.run_automation()

        self.assertEqual(self.checkpoint_file.read_text(encoding='utf-8'), 'A\nB\n')

    def test_successful_batch_clears_resume_list(self):
        self.logger_class.return_value.get_stats.return_value = {'total': 2}
        database_class = self._patch('PatentsDB')

        main_automation.run_automation()

        self.logger_class.return_value.export_to_excel.assert_called_once()
        database_class.return_value.export_to_jsonl.assert_called_once()
        self.assertEqual(self.checkpoint_file.read_text(encoding='utf-8'), '')

    def test_partial_failure_exports_success_then_reports_failed_batch(self):
        self.search_one.side_effect = [
            DetectionRecord(application_no='A', status_code=0),
            DetectionRecord(application_no='B', status_code=200),
        ]
        self.logger_class.return_value.get_stats.return_value = {'total': 2}
        database_class = self._patch('PatentsDB')

        with self.assertRaisesRegex(RuntimeError, '采集失败 1 条'):
            main_automation.run_automation()

        self.assertEqual(self.search_one.call_count, 2)
        self.logger_class.return_value.export_to_excel.assert_called_once()
        database_class.return_value.export_to_jsonl.assert_called_once()
        self.assertEqual(self.checkpoint_file.read_text(encoding='utf-8'), 'A\n')

    def test_limited_success_preserves_unselected_application_in_same_resume_file(self):
        self.checkpoint_file.write_text('202310411762X\n2024110065970\n', encoding='utf-8')
        self.search_one.side_effect = [DetectionRecord(application_no='202310411762X', status_code=200)]

        main_automation.run_automation(test_count=1, update_list=str(self.checkpoint_file))

        self.assertEqual(self.search_one.call_count, 1)
        self.assertEqual(self.checkpoint_file.read_text(encoding='utf-8'), '2024110065970\n')

    def test_limited_failure_preserves_failed_and_unselected_applications(self):
        self.checkpoint_file.write_text('202310411762X\n2024110065970\n', encoding='utf-8')
        self.search_one.side_effect = [DetectionRecord(application_no='202310411762X', status_code=0)]

        with self.assertRaisesRegex(RuntimeError, '采集失败 1 条'):
            main_automation.run_automation(test_count=1, update_list=str(self.checkpoint_file))

        self.assertEqual(self.search_one.call_count, 1)
        self.assertEqual(
            self.checkpoint_file.read_text(encoding='utf-8'),
            '202310411762X\n2024110065970\n',
        )


if __name__ == '__main__':
    unittest.main()
