import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import Mock, patch

from selenium.common.exceptions import StaleElementReferenceException, TimeoutException
from selenium.webdriver.support.ui import WebDriverWait

import browser_service
from browser_service import BrowserService, LoginConfirmationRequired


class TestPrivateLoginConfirmation(unittest.TestCase):
    def setUp(self):
        patches = ExitStack()
        self.addCleanup(patches.close)
        temporary_directory = patches.enter_context(tempfile.TemporaryDirectory())
        self.login_flag = Path(temporary_directory) / 'login_ready.flag'
        self.browser = Mock()
        self.browser.current_url = browser_service.CNIPA_URL
        self.browser.execute_script.return_value = True
        self.browser.find_elements.return_value = []
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
        patches.enter_context(patch.object(
            browser_service, 'WebDriverWait',
            side_effect=lambda browser, timeout, **keywords: WebDriverWait(browser, 0, **keywords),
        ))

    def test_operator_confirmation_allows_ready_private_page(self):
        opened_browser = BrowserService.launch_and_login(browser_service.CNIPA_URL)
        self.assertIs(opened_browser, self.browser)
        self.confirmation.assert_called_once()
        self.browser.quit.assert_not_called()
        self.alert.assert_not_called()

    def test_visible_login_form_rejects_operator_confirmation(self):
        password_input = Mock()
        password_input.is_displayed.return_value = True
        self.browser.find_elements.return_value = [password_input]
        with self.assertRaises(LoginConfirmationRequired):
            BrowserService.launch_and_login(browser_service.CNIPA_URL)
        self.browser.quit.assert_called_once()
        self.assertEqual(self.alert.call_args.args[0], 'login_required')

    def test_hidden_login_controls_do_not_block_confirmed_page(self):
        hidden_input = Mock()
        hidden_input.is_displayed.return_value = False
        self.browser.find_elements.return_value = [hidden_input]
        self.assertIs(BrowserService.launch_and_login(browser_service.CNIPA_URL), self.browser)

    def test_page_rerender_during_confirmation_is_retried(self):
        login_input = Mock()
        login_input.is_displayed.side_effect = [StaleElementReferenceException(), False]
        self.browser.find_elements.return_value = [login_input]
        with patch.object(
            browser_service, 'WebDriverWait',
            side_effect=lambda browser, timeout, **keywords: WebDriverWait(browser, 1, **keywords),
        ):
            self.assertIs(BrowserService.launch_and_login(browser_service.CNIPA_URL), self.browser)
        self.assertEqual(login_input.is_displayed.call_count, 2)
        self.alert.assert_not_called()

    def test_continuously_stale_page_requires_login_confirmation(self):
        login_input = Mock()
        login_input.is_displayed.side_effect = StaleElementReferenceException()
        self.browser.find_elements.return_value = [login_input]
        with self.assertRaises(LoginConfirmationRequired):
            BrowserService.launch_and_login(browser_service.CNIPA_URL)
        self.assertEqual(self.alert.call_args.args[0], 'login_required')
        self.browser.quit.assert_called_once()

    def test_unconfirmed_ready_page_times_out_without_returning_browser(self):
        self.stdin.isatty.return_value = False
        with self.assertRaises(LoginConfirmationRequired):
            BrowserService.launch_and_login(browser_service.CNIPA_URL)
        self.browser.quit.assert_called_once()
        self.confirmation.assert_not_called()
        self.assertEqual(self.alert.call_args.args[0], 'login_required')

    def test_stale_confirmation_does_not_allow_next_login(self):
        self.stdin.isatty.return_value = False
        self.login_flag.touch()
        with self.assertRaises(LoginConfirmationRequired):
            BrowserService.launch_and_login(browser_service.CNIPA_URL)
        self.assertFalse(self.login_flag.exists())
        self.browser.quit.assert_called_once()

    def test_fresh_dashboard_confirmation_allows_ready_page(self):
        self.stdin.isatty.return_value = False
        with patch.object(browser_service, 'CNIPA_LOGIN_WAIT_SECONDS', 10), patch.object(
            browser_service.time, 'sleep', side_effect=lambda seconds: self.login_flag.touch()
        ):
            opened_browser = BrowserService.launch_and_login(browser_service.CNIPA_URL)
        self.assertIs(opened_browser, self.browser)
        self.assertFalse(self.login_flag.exists())
        self.confirmation.assert_not_called()

    def test_unready_or_foreign_page_cannot_pass_confirmation(self):
        for page_url, page_ready in (
            ('chrome-error://chromewebdata/', True),
            (browser_service.CNIPA_URL, False),
        ):
            with self.subTest(page_url=page_url, page_ready=page_ready):
                self.browser.current_url = page_url
                self.browser.execute_script.return_value = page_ready
                with self.assertRaises(LoginConfirmationRequired):
                    BrowserService._verify_confirmed_login(self.browser)

    def test_closed_stdin_does_not_confirm_login(self):
        self.confirmation.side_effect = EOFError
        with self.assertRaises(LoginConfirmationRequired):
            BrowserService.launch_and_login(browser_service.CNIPA_URL)
        self.browser.quit.assert_called_once()

    def test_virtual_captcha_cannot_bypass_login_confirmation(self):
        self.stdin.isatty.return_value = False
        with patch.object(browser_service, 'USE_VIRTUAL_DISPLAY', True), patch.object(
            browser_service, 'load_credentials', return_value=('operator', 'password')
        ), patch.object(browser_service, 'auto_fill_login', return_value=True), patch.object(
            BrowserService, '_show_virtual_screenshot'
        ):
            with self.assertRaises(LoginConfirmationRequired):
                BrowserService.launch_and_login(browser_service.CNIPA_URL)
        self.confirmation.assert_not_called()
        self.browser.quit.assert_called_once()

    def test_navigation_failure_closes_driver_before_returning_it(self):
        self.browser.get.side_effect = TimeoutException('page stalled')
        with self.assertRaisesRegex(RuntimeError, '未加载完成'):
            BrowserService.launch_and_login(browser_service.CNIPA_URL)
        self.browser.quit.assert_called_once()
        self.alert.assert_not_called()


if __name__ == '__main__':
    unittest.main()
