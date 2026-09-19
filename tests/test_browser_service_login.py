import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import Mock, PropertyMock, patch

from selenium.common.exceptions import TimeoutException

import browser_service
from browser_service import BrowserService, LoginConfirmationRequired
from settings import CNIPA_URL


class TestPrivateLoginConfirmation(unittest.TestCase):
    def setUp(self):
        patches = ExitStack()
        self.addCleanup(patches.close)
        temporary_directory = patches.enter_context(tempfile.TemporaryDirectory())
        self.login_flag = Path(temporary_directory) / 'login_ready.flag'
        self.browser = Mock()
        self.browser.get_window_rect.return_value = {
            'x': browser_service.BROWSER_WINDOW_X,
            'y': browser_service.BROWSER_WINDOW_Y,
            'width': browser_service.BROWSER_WINDOW_WIDTH,
            'height': browser_service.BROWSER_WINDOW_HEIGHT,
        }
        self.current_url = patches.enter_context(patch.object(
            type(self.browser), 'current_url', new_callable=PropertyMock, create=True,
            side_effect=AssertionError('Login confirmation must not inspect the browser URL'),
        ))
        self.page_source = patches.enter_context(patch.object(
            type(self.browser), 'page_source', new_callable=PropertyMock, create=True,
            side_effect=AssertionError('Login confirmation must not inspect the page source'),
        ))
        self.stdin = patches.enter_context(patch.object(browser_service.sys, 'stdin'))
        self.stdin.isatty.return_value = True
        self.confirmation = patches.enter_context(patch('builtins.input', return_value=''))
        self.alert = patches.enter_context(patch.object(browser_service, 'record_collection_alert'))
        patches.enter_context(patch.object(browser_service, 'LOGIN_READY_FLAG_FILE', self.login_flag))
        patches.enter_context(patch.object(browser_service, 'CNIPA_LOGIN_WAIT_SECONDS', 0))
        patches.enter_context(patch.object(browser_service, 'USE_VIRTUAL_DISPLAY', False))
        patches.enter_context(patch.object(browser_service, 'load_credentials', return_value=('', '')))
        patches.enter_context(patch.object(browser_service, 'create_driver_with_retry', return_value=self.browser))
        patches.enter_context(patch.object(browser_service.time, 'sleep'))

    def test_terminal_confirmation_does_not_probe_the_logged_in_page(self):
        opened_browser = BrowserService.launch_and_login(CNIPA_URL)
        self.assertIs(opened_browser, self.browser)
        self.confirmation.assert_called_once()
        self.current_url.assert_not_called()
        self.page_source.assert_not_called()
        self.browser.execute_script.assert_not_called()
        self.browser.find_element.assert_not_called()
        self.browser.find_elements.assert_not_called()
        self.browser.quit.assert_not_called()
        self.alert.assert_not_called()

    def test_unconfirmed_ready_page_times_out_without_returning_browser(self):
        self.stdin.isatty.return_value = False
        with self.assertRaises(LoginConfirmationRequired):
            BrowserService.launch_and_login(CNIPA_URL)
        self.browser.quit.assert_called_once()
        self.confirmation.assert_not_called()
        self.assertEqual(self.alert.call_args.args[0], 'login_required')

    def test_stale_confirmation_does_not_allow_next_login(self):
        self.stdin.isatty.return_value = False
        self.login_flag.touch()
        with self.assertRaises(LoginConfirmationRequired):
            BrowserService.launch_and_login(CNIPA_URL)
        self.assertFalse(self.login_flag.exists())
        self.browser.quit.assert_called_once()

    def test_fresh_dashboard_confirmation_does_not_probe_the_logged_in_page(self):
        self.stdin.isatty.return_value = False
        with patch.object(browser_service, 'CNIPA_LOGIN_WAIT_SECONDS', 10), patch.object(
            browser_service.time, 'sleep', side_effect=lambda seconds: self.login_flag.touch()
        ):
            opened_browser = BrowserService.launch_and_login(CNIPA_URL)
        self.assertIs(opened_browser, self.browser)
        self.assertFalse(self.login_flag.exists())
        self.confirmation.assert_not_called()
        self.current_url.assert_not_called()
        self.page_source.assert_not_called()
        self.browser.execute_script.assert_not_called()
        self.browser.find_element.assert_not_called()
        self.browser.find_elements.assert_not_called()
        self.browser.quit.assert_not_called()
        self.alert.assert_not_called()

    def test_closed_stdin_does_not_confirm_login(self):
        self.confirmation.side_effect = EOFError
        with self.assertRaises(LoginConfirmationRequired):
            BrowserService.launch_and_login(CNIPA_URL)
        self.browser.quit.assert_called_once()

    def test_virtual_captcha_cannot_bypass_login_confirmation(self):
        self.stdin.isatty.return_value = False
        with patch.object(browser_service, 'USE_VIRTUAL_DISPLAY', True), patch.object(
            browser_service, 'load_credentials', return_value=('operator', 'password')
        ), patch.object(browser_service, 'auto_fill_login', return_value=True), patch.object(
            BrowserService, '_show_virtual_screenshot'
        ):
            with self.assertRaises(LoginConfirmationRequired):
                BrowserService.launch_and_login(CNIPA_URL)
        self.confirmation.assert_not_called()
        self.browser.quit.assert_called_once()

    def test_navigation_failure_closes_driver_before_returning_it(self):
        self.browser.get.side_effect = TimeoutException('page stalled')
        with self.assertRaisesRegex(RuntimeError, '未加载完成'):
            BrowserService.launch_and_login(CNIPA_URL)
        self.browser.quit.assert_called_once()
        self.alert.assert_not_called()


if __name__ == '__main__':
    unittest.main()
