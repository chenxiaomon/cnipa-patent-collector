import io
import runpy
import unittest
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from unittest.mock import MagicMock, PropertyMock, patch

import collect_fwxx
import detail_search
import settings


class TestFwxxDetailWindow(unittest.TestCase):
    def setUp(self):
        patches = ExitStack()
        self.addCleanup(patches.close)
        self.driver = MagicMock()
        self.driver.page_source = '<table><tr><td>2022114339006</td></tr></table>'
        patches.enter_context(patch.object(collect_fwxx, 'is_browser_alive', return_value=True))
        patches.enter_context(patch.object(collect_fwxx, 'wait_for_detail_search_target'))
        self.check_login = patches.enter_context(patch.object(collect_fwxx, 'raise_if_cnipa_login_required'))
        patches.enter_context(patch.object(detail_search, 'raise_if_cnipa_login_required', self.check_login))
        self.input_service = patches.enter_context(patch.object(collect_fwxx, 'InputService'))
        self.sleep = patches.enter_context(patch.object(collect_fwxx.time, 'sleep'))
        self.monotonic = patches.enter_context(patch.object(collect_fwxx.time, 'monotonic', return_value=0))
        patches.enter_context(patch.object(detail_search, 'FWXX_DETAIL_OPEN_TIMEOUT', 15))
        patches.enter_context(patch.object(collect_fwxx, 'clear_cache_key'))
        patches.enter_context(patch.object(collect_fwxx, 'begin_detail_attempt', return_value={
            'application_no': '2022114339006', 'attempt_id': 'current-attempt',
        }))
        self.clear_attempt = patches.enter_context(patch.object(collect_fwxx, 'clear_matching_detail_attempt'))
        patches.enter_context(patch.object(detail_search, 'clear_matching_detail_attempt', self.clear_attempt))
        self.confirm_identity = patches.enter_context(patch.object(collect_fwxx, 'wait_for_detail_identity'))
        self.poll_fields = patches.enter_context(patch.object(collect_fwxx, 'poll_cache_for_key', return_value={
            'fwxx_list': [], 'detail_attempt_id': 'current-attempt',
        }))
        self.window_handles = PropertyMock()
        type(self.driver).window_handles = self.window_handles

    def test_delayed_detail_tab_is_collected_without_clicking_link_again(self):
        self.window_handles.side_effect = [
            ['search'], ['search'], ['search'], ['search', 'detail'],
            ['search', 'detail'], ['search'],
        ]

        collected_fields = collect_fwxx.collect_one_fwxx(
            self.driver, '2022114339006', 1, 2, 3, 4, 5, 6, 7, 8,
        )

        self.assertEqual(collected_fields, {'fwxx_list': []})
        self.assertEqual([click.args for click in self.input_service.move_and_click.call_args_list], [(5, 6), (7, 8)])
        self.confirm_identity.assert_called_once()
        self.poll_fields.assert_called_once()
        self.assertEqual([pause.args for pause in self.sleep.call_args_list].count((0.2,)), 2)
        self.driver.close.assert_called_once()
        self.driver.switch_to.window.assert_called_with('search')
        self.clear_attempt.assert_called_once_with('current-attempt')

    def test_login_expiry_while_waiting_for_detail_is_not_retried(self):
        self.window_handles.return_value = ['search']
        self.check_login.side_effect = [None, collect_fwxx.CNIPALoginRequired('需要重新登录')]

        with self.assertRaisesRegex(collect_fwxx.CNIPALoginRequired, '需要重新登录'):
            collect_fwxx.collect_one_fwxx(self.driver, '2022114339006', 1, 2, 3, 4, 5, 6, 7, 8)

        self.confirm_identity.assert_not_called()
        self.poll_fields.assert_not_called()
        self.driver.refresh.assert_not_called()
        self.clear_attempt.assert_called_once_with('current-attempt')

    def test_login_expiry_during_cache_poll_closes_detail_and_propagates(self):
        self.window_handles.side_effect = [
            ['search'], ['search', 'detail'], ['search', 'detail'], ['search'],
        ]
        self.poll_fields.side_effect = collect_fwxx.CNIPALoginRequired('需要重新登录')

        with self.assertRaisesRegex(collect_fwxx.CNIPALoginRequired, '需要重新登录'):
            collect_fwxx.collect_one_fwxx(self.driver, '2022114339006', 1, 2, 3, 4, 5, 6, 7, 8)

        self.driver.close.assert_called_once()
        self.driver.refresh.assert_not_called()
        self.clear_attempt.assert_called_once_with('current-attempt')

    def test_missing_detail_tab_recovers_search_page_before_returning_failure(self):
        self.window_handles.return_value = ['search']
        self.monotonic.side_effect = [0, 0, 15]
        self.driver.refresh.side_effect = lambda: self.clear_attempt.assert_called_once_with('current-attempt')

        with self.assertRaisesRegex(collect_fwxx.FwxxCollectionRetryableError, '未打开新标签页'):
            collect_fwxx.collect_one_fwxx(self.driver, '2022114339006', 1, 2, 3, 4, 5, 6, 7, 8)

        self.driver.refresh.assert_called_once()
        self.driver.switch_to.window.assert_called_with('search')
        self.input_service.move_and_click.assert_called_once()
        self.confirm_identity.assert_not_called()
        self.poll_fields.assert_not_called()
        self.driver.close.assert_not_called()
        self.clear_attempt.assert_called_once_with('current-attempt')

    def test_tab_arriving_during_refresh_is_closed_before_retry_is_allowed(self):
        self.monotonic.side_effect = [0, 15]
        self.window_handles.side_effect = [
            ['search'], ['search'], ['search', 'late-detail'], ['search'],
        ]

        with self.assertRaisesRegex(collect_fwxx.FwxxCollectionRetryableError, '未打开新标签页'):
            collect_fwxx.collect_one_fwxx(self.driver, '2022114339006', 1, 2, 3, 4, 5, 6, 7, 8)

        self.driver.refresh.assert_called_once()
        self.driver.switch_to.window.assert_any_call('late-detail')
        self.driver.close.assert_called_once()
        self.driver.switch_to.window.assert_called_with('search')
        self.confirm_identity.assert_not_called()
        self.poll_fields.assert_not_called()
        self.clear_attempt.assert_called_once_with('current-attempt')

    def test_refresh_failure_prevents_retry(self):
        self.monotonic.side_effect = [0, 15]
        self.window_handles.return_value = ['search']
        self.driver.refresh.side_effect = collect_fwxx.WebDriverException('session lost')

        with self.assertRaisesRegex(collect_fwxx.DetailCollectionFatalError, '无法恢复搜索页'):
            collect_fwxx.collect_one_fwxx(self.driver, '2022114339006', 1, 2, 3, 4, 5, 6, 7, 8)

        self.confirm_identity.assert_not_called()
        self.poll_fields.assert_not_called()

    def test_additional_tab_after_recovery_prevents_retry(self):
        self.monotonic.side_effect = [0, 15]
        self.window_handles.side_effect = [
            ['search'], ['search'], ['search', 'late-detail'], ['search', 'another-detail'],
        ]

        with self.assertRaisesRegex(collect_fwxx.DetailCollectionFatalError, '恢复后仍有额外标签页'):
            collect_fwxx.collect_one_fwxx(self.driver, '2022114339006', 1, 2, 3, 4, 5, 6, 7, 8)

        self.confirm_identity.assert_not_called()
        self.poll_fields.assert_not_called()

    def test_click_exception_after_attempt_started_recovers_before_retry(self):
        self.window_handles.return_value = ['search']
        self.input_service.move_and_click.side_effect = RuntimeError('click interrupted')
        self.driver.refresh.side_effect = lambda: self.clear_attempt.assert_called_once_with('current-attempt')

        with self.assertRaisesRegex(collect_fwxx.FwxxCollectionRetryableError, 'click interrupted'):
            collect_fwxx.collect_one_fwxx(self.driver, '2022114339006', 1, 2, 3, 4, 5, 6, 7, 8)

        self.driver.refresh.assert_called_once()
        self.confirm_identity.assert_not_called()
        self.poll_fields.assert_not_called()
        self.clear_attempt.assert_called_once_with('current-attempt')

    def test_identity_timeout_discards_detail_and_returns_recoverable_failure(self):
        self.window_handles.side_effect = [
            ['search'], ['search', 'detail'], ['search', 'detail'], ['search'],
        ]
        self.confirm_identity.side_effect = collect_fwxx.DetailIdentityTimeout('未收到官方申请号')
        self.driver.refresh.side_effect = lambda: self.clear_attempt.assert_called_once_with('current-attempt')

        with self.assertRaisesRegex(collect_fwxx.FwxxCollectionRetryableError, '未收到官方申请号'):
            collect_fwxx.collect_one_fwxx(self.driver, '2022114339006', 1, 2, 3, 4, 5, 6, 7, 8)

        self.input_service.move_and_click.assert_called_once()
        self.poll_fields.assert_not_called()
        self.driver.refresh.assert_called_once()
        self.driver.close.assert_called_once()
        self.driver.switch_to.window.assert_called_with('search')
        self.clear_attempt.assert_called_once_with('current-attempt')

    def test_identity_timeout_with_failed_recovery_still_stops(self):
        self.window_handles.side_effect = [
            ['search'], ['search', 'detail'], ['search', 'detail'], ['search'],
        ]
        self.confirm_identity.side_effect = collect_fwxx.DetailIdentityTimeout('未收到官方申请号')
        self.driver.refresh.side_effect = collect_fwxx.WebDriverException('refresh failed')

        with self.assertRaisesRegex(collect_fwxx.DetailCollectionFatalError, '无法恢复搜索页'):
            collect_fwxx.collect_one_fwxx(self.driver, '2022114339006', 1, 2, 3, 4, 5, 6, 7, 8)

        self.input_service.move_and_click.assert_called_once()
        self.poll_fields.assert_not_called()

    def test_batch_moves_to_next_application_after_two_identity_timeouts(self):
        from argparse import Namespace
        from pathlib import Path
        from tempfile import TemporaryDirectory

        import collection_checkpoint
        import db_manager

        self.window_handles.return_value = ['search']
        self.input_service.move_and_click.side_effect = lambda x, y, **wait: (
            self.window_handles.return_value.append('detail') if (x, y) == (5, 6) else None
        )
        self.driver.close.side_effect = lambda: self.window_handles.return_value.remove('detail')
        self.confirm_identity.side_effect = [
            collect_fwxx.DetailIdentityTimeout('未收到官方申请号'),
            collect_fwxx.DetailIdentityTimeout('未收到官方申请号'),
            None,
        ]

        with TemporaryDirectory() as temporary_directory, ExitStack() as patches:
            checkpoint_path = Path(temporary_directory) / 'checkpoint.txt'
            patches.enter_context(patch.object(collect_fwxx, 'FWXX_COLLECTION_CHECKPOINT_FILE', checkpoint_path))
            patches.enter_context(patch.object(collection_checkpoint, 'COLLECTION_BATCHES_DIR', Path(temporary_directory) / 'batches'))
            patches.enter_context(patch.object(collect_fwxx, 'load_target_applications', return_value=['2022114339006', '2022114363024']))
            coordinates = patches.enter_context(patch.object(collect_fwxx, 'CoordinateService'))
            coordinates.load_search_coordinates.return_value = (1, 2, 3, 4)
            coordinates.load_fwxx_coordinates.return_value = (5, 6, 7, 8)
            browser = patches.enter_context(patch.object(collect_fwxx, 'BrowserService'))
            browser.launch_and_login.return_value = self.driver
            patches.enter_context(patch.object(collect_fwxx, 'countdown'))
            persisted_fields = patches.enter_context(patch.object(collect_fwxx, 'persist_fwxx_fields', return_value=True))
            patches.enter_context(patch.object(collect_fwxx, 'DetectionLogger'))
            patches.enter_context(patch.object(db_manager, 'PatentsDB'))
            failure_streak = patches.enter_context(patch.object(collect_fwxx, 'CollectionFailureStreak')).return_value

            with self.assertRaisesRegex(RuntimeError, '采集失败 1 条'):
                collect_fwxx._run_fwxx_collection(Namespace(test=None, url='https://example.invalid'))

            self.assertEqual(
                [query.args[4] for query in self.input_service.type_in_search.call_args_list],
                ['2022114339006', '2022114339006', '2022114363024'],
            )
            persisted_fields.assert_called_once_with('2022114363024', {'fwxx_list': []})
            self.assertEqual(self.driver.refresh.call_count, 2)
            self.assertEqual(self.driver.close.call_count, 3)
            self.poll_fields.assert_called_once()
            failure_streak.record_failure.assert_called_once()
            failure_streak.record_success.assert_called_once()
            self.assertEqual(checkpoint_path.read_text(), '2022114339006\n')
            batch_id = collection_checkpoint.list_collection_batches()[0]['id']
            saved_batch = collection_checkpoint.read_collection_batch(batch_id)
            self.assertEqual(saved_batch['status'], 'failed')
            self.assertEqual([item['status'] for item in saved_batch['items']], ['failed', 'success'])

    def test_mouse_emergency_stop_does_not_retry_or_refresh(self):
        self.window_handles.return_value = ['search']
        self.input_service.move_and_click.side_effect = collect_fwxx.pyautogui.FailSafeException()

        with self.assertRaisesRegex(collect_fwxx.DetailCollectionFatalError, '鼠标紧急停止'):
            collect_fwxx.collect_one_fwxx(self.driver, '2022114339006', 1, 2, 3, 4, 5, 6, 7, 8)

        self.driver.refresh.assert_not_called()
        self.confirm_identity.assert_not_called()
        self.poll_fields.assert_not_called()
        self.clear_attempt.assert_called_once_with('current-attempt')

    def test_multiple_detail_tabs_stop_immediately(self):
        self.window_handles.side_effect = [['search'], ['search', 'detail-a', 'detail-b']]

        with self.assertRaisesRegex(collect_fwxx.DetailCollectionFatalError, '2 个新标签页'):
            collect_fwxx.collect_one_fwxx(self.driver, '2022114339006', 1, 2, 3, 4, 5, 6, 7, 8)

        self.sleep.assert_not_called()
        self.input_service.move_and_click.assert_called_once()
        self.confirm_identity.assert_not_called()
        self.poll_fields.assert_not_called()
        self.driver.close.assert_not_called()
        self.clear_attempt.assert_called_once_with('current-attempt')

    def test_lost_search_tab_cannot_authorize_detail_collection(self):
        self.window_handles.side_effect = [['search'], ['detail']]

        with self.assertRaisesRegex(collect_fwxx.DetailCollectionFatalError, '搜索页已丢失'):
            collect_fwxx.collect_one_fwxx(self.driver, '2022114339006', 1, 2, 3, 4, 5, 6, 7, 8)

        self.sleep.assert_not_called()
        self.input_service.move_and_click.assert_called_once()
        self.confirm_identity.assert_not_called()
        self.poll_fields.assert_not_called()
        self.clear_attempt.assert_called_once_with('current-attempt')

    def test_driver_disconnect_while_waiting_stops_collection(self):
        self.window_handles.side_effect = [
            ['search'], ['search'], collect_fwxx.WebDriverException('session lost'),
        ]

        with self.assertRaisesRegex(collect_fwxx.DetailCollectionFatalError, '浏览器连接失效'):
            collect_fwxx.collect_one_fwxx(self.driver, '2022114339006', 1, 2, 3, 4, 5, 6, 7, 8)

        self.input_service.move_and_click.assert_called_once()
        self.confirm_identity.assert_not_called()
        self.poll_fields.assert_not_called()
        self.clear_attempt.assert_called_once_with('current-attempt')

    def test_second_tab_arriving_after_identity_check_prevents_returning_fields(self):
        self.window_handles.side_effect = [
            ['search'], ['search', 'detail'],
            ['search', 'detail', 'late-detail'], ['search', 'late-detail'],
        ]

        with self.assertRaisesRegex(collect_fwxx.DetailCollectionFatalError, '未恢复唯一搜索页'):
            collect_fwxx.collect_one_fwxx(self.driver, '2022114339006', 1, 2, 3, 4, 5, 6, 7, 8)

        self.confirm_identity.assert_called_once()
        self.poll_fields.assert_called_once()
        self.driver.close.assert_called_once()
        self.clear_attempt.assert_called_once_with('current-attempt')


class TestFwxxFatalExit(unittest.TestCase):
    def test_cli_reports_interruption_once_and_preserves_batch(self):
        import tempfile
        from pathlib import Path

        import browser_service
        import browser_utils
        import collection_checkpoint
        import coordinate_service
        import db_manager

        with tempfile.TemporaryDirectory() as temporary_directory, ExitStack() as patches:
            checkpoint_path = Path(temporary_directory) / 'checkpoint.txt'
            batch_directory = Path(temporary_directory) / 'batches'
            patches.enter_context(patch.object(settings, 'FWXX_COLLECTION_CHECKPOINT_FILE', checkpoint_path))
            patches.enter_context(patch.object(settings, 'USE_MITM_PROXY', True))
            patches.enter_context(patch.object(collection_checkpoint, 'COLLECTION_BATCHES_DIR', batch_directory))
            patches.enter_context(patch.object(browser_utils, 'raise_system_exit_on_sigterm'))
            coordinates = patches.enter_context(patch.object(coordinate_service, 'CoordinateService'))
            coordinates.load_search_coordinates.return_value = (1, 2, 3, 4)
            coordinates.load_fwxx_coordinates.return_value = (5, 6, 7, 8)
            patent_database = patches.enter_context(patch.object(db_manager, 'PatentsDB'))
            patent_database.return_value.get_summary.return_value = {'rejection': 2}
            patent_database.return_value.fwxx_uncollected_app_nos.return_value = ['2022114339006', '2022114363024']
            browser = patches.enter_context(patch.object(browser_service, 'BrowserService'))
            browser.launch_and_login.side_effect = collect_fwxx.DetailCollectionFatalError('详情页测试中断')
            patches.enter_context(patch('sys.argv', [str(settings.BASE_DIR / 'collect_fwxx.py')]))
            output = io.StringIO()

            with redirect_stdout(output), redirect_stderr(output), self.assertRaises(SystemExit) as stopped:
                runpy.run_path(str(settings.BASE_DIR / 'collect_fwxx.py'), run_name='__main__')

            self.assertEqual(stopped.exception.code, 1)
            self.assertEqual(output.getvalue().count('详情页测试中断'), 1)
            self.assertNotIn('Traceback', output.getvalue())
            self.assertNotIn('[✓] 程序结束', output.getvalue())
            self.assertIn('续跑命令:', output.getvalue())
            self.assertEqual(checkpoint_path.read_text(encoding='utf-8'), '2022114339006\n2022114363024\n')
            saved_batch = collection_checkpoint.read_collection_batch(next(batch_directory.glob('*.json')).stem)
            self.assertEqual(saved_batch['status'], 'interrupted')
            self.assertEqual(saved_batch['runs'][-1]['stop_reason'], '详情页测试中断')
