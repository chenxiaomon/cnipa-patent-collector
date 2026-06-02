#!/usr/bin/env python
# -*- coding: utf-8 -*-

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from browser_utils import _major_version_from_text, load_credentials


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


if __name__ == '__main__':
    unittest.main()
