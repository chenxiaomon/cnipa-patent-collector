#!/usr/bin/env python
# -*- coding: utf-8 -*-

import plistlib
import unittest
from pathlib import Path
from subprocess import CompletedProcess
from tempfile import TemporaryDirectory
from unittest.mock import patch

import browser_utils
from browser_utils import (
    _find_matching_chromedriver,
    _get_chrome_major_version,
    _get_macos_chrome_major_version,
    _major_version_from_text,
    create_driver_with_retry,
    load_credentials,
)


class TestChromeVersionParsing(unittest.TestCase):
    def test_parses_full_chrome_version(self):
        self.assertEqual(_major_version_from_text('148.0.7778.179'), 148)

    def test_parses_chrome_version_command_output(self):
        self.assertEqual(_major_version_from_text('Google Chrome 148.0.7778.179'), 148)

    def test_returns_none_without_version(self):
        self.assertIsNone(_major_version_from_text('Google Chrome'))


class TestCredentialLoading(unittest.TestCase):
    def test_env_file_overrides_process_environment(self):
        with TemporaryDirectory() as tmp_dir:
            Path(tmp_dir, '.env').write_text(
                'CNIPA_USERNAME=file-user\nCNIPA_PASSWORD=file-pass\n',
                encoding='utf-8',
            )
            with patch('browser_utils.os.path.dirname', return_value=tmp_dir):
                with patch.dict('browser_utils.os.environ', {
                    'CNIPA_USERNAME': 'env-user',
                    'CNIPA_PASSWORD': 'env-pass',
                }, clear=False):
                    self.assertEqual(load_credentials(), ('file-user', 'file-pass'))

    def test_process_environment_used_without_env_file(self):
        with TemporaryDirectory() as tmp_dir:
            with patch('browser_utils.os.path.dirname', return_value=tmp_dir):
                with patch.dict('browser_utils.os.environ', {
                    'CNIPA_USERNAME': 'env-user',
                    'CNIPA_PASSWORD': 'env-pass',
                }, clear=False):
                    self.assertEqual(load_credentials(), ('env-user', 'env-pass'))


def _make_chrome_app_bundle(root_dir: str, plist_version: str | None) -> str:
    """造一个最小 Google Chrome.app 目录；plist_version 为 None 时不写 Info.plist。"""
    app_bundle = Path(root_dir, 'Google Chrome.app')
    (app_bundle / 'Contents' / 'MacOS').mkdir(parents=True)
    if plist_version is not None:
        with open(app_bundle / 'Contents' / 'Info.plist', 'wb') as plist_stream:
            plistlib.dump({'CFBundleShortVersionString': plist_version}, plist_stream)
    (app_bundle / 'Contents' / 'MacOS' / 'Google Chrome').write_bytes(b'')
    return str(app_bundle)


class TestMacosChromeVersion(unittest.TestCase):
    def test_reads_major_version_from_info_plist(self):
        with TemporaryDirectory() as tmp_dir:
            app_bundle = _make_chrome_app_bundle(tmp_dir, '150.0.7871.187')
            with patch.object(browser_utils, '_MACOS_CHROME_APP_BUNDLES', [app_bundle]):
                self.assertEqual(_get_macos_chrome_major_version(), 150)

    def test_falls_back_to_binary_version_without_info_plist(self):
        with TemporaryDirectory() as tmp_dir:
            app_bundle = _make_chrome_app_bundle(tmp_dir, None)
            with patch.object(browser_utils, '_MACOS_CHROME_APP_BUNDLES', [app_bundle]):
                # Chrome 自报版本带尾随空格，交给既有解析函数处理
                with patch('browser_utils.subprocess.run', return_value=CompletedProcess(
                    args=[], returncode=0, stdout='Google Chrome 150.0.7871.187 \n',
                )):
                    self.assertEqual(_get_macos_chrome_major_version(), 150)

    def test_returns_none_when_no_chrome_installed(self):
        with TemporaryDirectory() as tmp_dir:
            missing_bundle = str(Path(tmp_dir, 'Nowhere.app'))
            with patch.object(browser_utils, '_MACOS_CHROME_APP_BUNDLES', [missing_bundle]):
                self.assertIsNone(_get_macos_chrome_major_version())

    def test_darwin_uses_macos_probe_instead_of_linux_commands(self):
        with patch('browser_utils.sys.platform', 'darwin'):
            with patch(
                'browser_utils._get_macos_chrome_major_version', return_value=150
            ) as macos_probe:
                with patch('browser_utils.subprocess.run') as linux_command:
                    self.assertEqual(_get_chrome_major_version(), 150)
        macos_probe.assert_called_once()
        linux_command.assert_not_called()

    def test_windows_still_prefers_registry_version(self):
        with patch('browser_utils.sys.platform', 'win32'):
            with patch(
                'browser_utils._get_windows_chrome_major_version', return_value=148
            ):
                with patch('browser_utils._get_macos_chrome_major_version') as macos_probe:
                    self.assertEqual(_get_chrome_major_version(), 148)
        macos_probe.assert_not_called()


class TestChromedriverDiscovery(unittest.TestCase):
    def test_reuses_cached_driver_when_major_version_matches(self):
        with TemporaryDirectory() as cache_dir:
            cached_driver = Path(cache_dir, 'undetected_chromedriver')
            cached_driver.write_bytes(b'')
            with patch('browser_utils.uc.Patcher.data_path', cache_dir):
                with patch(
                    'browser_utils._get_chromedriver_major_version', return_value=150
                ):
                    self.assertEqual(_find_matching_chromedriver(150), str(cached_driver))

    def test_ignores_cached_driver_of_other_major_version(self):
        with TemporaryDirectory() as cache_dir:
            Path(cache_dir, 'undetected_chromedriver').write_bytes(b'')
            with patch('browser_utils.uc.Patcher.data_path', cache_dir):
                with patch(
                    'browser_utils._get_chromedriver_major_version', return_value=148
                ):
                    self.assertIsNone(_find_matching_chromedriver(150))

    def test_returns_none_without_known_chrome_version(self):
        with patch('browser_utils.glob.glob') as driver_search:
            self.assertIsNone(_find_matching_chromedriver(None))
        driver_search.assert_not_called()


class TestDriverCreation(unittest.TestCase):
    def test_refuses_to_launch_when_mitm_proxy_is_down(self):
        with patch('browser_utils.check_mitm_proxy', return_value=False):
            with patch('browser_utils.uc.Chrome') as chrome:
                with self.assertRaises(RuntimeError) as raised:
                    create_driver_with_retry(use_mitm=True)
        self.assertIn('MITM 代理', str(raised.exception))
        chrome.assert_not_called()

    def test_passes_matching_driver_path_instead_of_version(self):
        with patch('browser_utils._get_chrome_major_version', return_value=150):
            with patch(
                'browser_utils._find_matching_chromedriver', return_value='/tmp/chromedriver'
            ):
                with patch('browser_utils.uc.Chrome') as chrome:
                    create_driver_with_retry(use_mitm=False)
        self.assertEqual(chrome.call_args.kwargs['driver_executable_path'], '/tmp/chromedriver')
        self.assertNotIn('version_main', chrome.call_args.kwargs)

    def test_passes_chrome_major_version_without_local_driver(self):
        with patch('browser_utils._get_chrome_major_version', return_value=150):
            with patch('browser_utils._find_matching_chromedriver', return_value=None):
                with patch('browser_utils.uc.Chrome') as chrome:
                    create_driver_with_retry(use_mitm=False)
        self.assertEqual(chrome.call_args.kwargs['version_main'], 150)
        self.assertNotIn('driver_executable_path', chrome.call_args.kwargs)

    def test_rechecks_driver_path_on_every_attempt(self):
        # 第 1 次尝试可能刚把匹配版本下载进 uc 缓存，第 2 次必须重新探测才能复用
        with patch('browser_utils.time.sleep'):
            with patch('browser_utils._get_chrome_major_version', return_value=150):
                with patch(
                    'browser_utils._find_matching_chromedriver',
                    side_effect=[None, '/tmp/chromedriver'],
                ) as driver_search:
                    with patch(
                        'browser_utils.uc.Chrome', side_effect=[OSError('boom'), object()]
                    ):
                        create_driver_with_retry(max_retries=2, use_mitm=False)
        self.assertEqual(driver_search.call_count, 2)

    def test_failure_message_names_chrome_version_and_manual_driver_dir(self):
        with patch('browser_utils.time.sleep'):
            with patch('browser_utils._get_chrome_major_version', return_value=150):
                with patch('browser_utils._find_matching_chromedriver', return_value=None):
                    with patch('browser_utils.uc.Chrome', side_effect=OSError('boom')):
                        with self.assertRaises(RuntimeError) as raised:
                            create_driver_with_retry(max_retries=1, use_mitm=False)
        message = str(raised.exception)
        self.assertIn('150', message)
        self.assertIn(browser_utils._manual_chromedriver_dir_name(), message)
        self.assertIn('boom', message)


if __name__ == '__main__':
    unittest.main()
