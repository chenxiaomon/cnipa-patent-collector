import io
import subprocess
import unittest
from unittest.mock import patch

import check_update


class HttpVersionComparisonTests(unittest.TestCase):
    def test_dashboard_update_check_uses_the_http_installation_channel(self):
        with patch.object(check_update, 'check_via_http', return_value={'method': 'http'}) as check_http:
            with patch.object(check_update, 'check_via_git') as check_git, patch.object(
                check_update, '_git_available', return_value='git'
            ):
                check = check_update.check_update()

        self.assertEqual(check, {'method': 'http'})
        check_http.assert_called_once_with()
        check_git.assert_not_called()

    def test_failed_git_comparison_is_not_reported_as_up_to_date(self):
        commands = [
            subprocess.CompletedProcess([], 0, stdout='', stderr=''),
            subprocess.CompletedProcess([], 0, stdout='origin/missing\n', stderr=''),
            subprocess.CompletedProcess([], 128, stdout='', stderr='bad revision'),
        ]
        with patch.object(check_update.subprocess, 'run', side_effect=commands):
            check = check_update.check_via_git('git')
        self.assertIn('bad revision', check['error'])

    def check_versions(self, local_version: str, remote_version: str) -> dict:
        with (
            patch.object(check_update, '_read_local_version', return_value=local_version),
            patch.object(check_update, 'raw_file_urls', return_value=['http://updates/VERSION']),
            patch.object(
                check_update.urllib.request,
                'urlopen',
                return_value=io.BytesIO(remote_version.encode('utf-8')),
            ),
        ):
            return check_update.check_via_http()

    def test_remote_older_version_is_not_an_update(self):
        check = self.check_versions('2026.07.27', '2026.07.21')

        self.assertFalse(check['has_update'])

    def test_remote_newer_version_is_an_update(self):
        check = self.check_versions('2026.07.21', '2026.07.27')

        self.assertTrue(check['has_update'])

    def test_equal_version_is_not_an_update(self):
        check = self.check_versions('2026.07.27', '2026.07.27')

        self.assertFalse(check['has_update'])

    def test_non_padded_local_version_is_rejected(self):
        check = self.check_versions('2026.7.9', '2026.07.10')

        self.assertFalse(check['has_update'])
        self.assertIsNotNone(check['error'])

    def test_invalid_remote_version_does_not_offer_an_update(self):
        check = self.check_versions('2026.07.27', 'not-a-version')

        self.assertFalse(check['has_update'])

    def test_invalid_remote_version_reports_an_error(self):
        check = self.check_versions('2026.07.27', 'not-a-version')

        self.assertIsNotNone(check['error'])


if __name__ == '__main__':
    unittest.main()
