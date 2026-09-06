import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
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

    def check_versions(self, local_version: str, remote_version: str, local_revision=0, remote_revision=0) -> dict:
        manifest = json.dumps({
            'manifest_version': 1,
            'release': {'version': remote_version, 'revision': remote_revision},
            'files': [{'path': name, 'sha256': '0' * 64} for name in ('VERSION', 'RELEASE_REVISION')],
        }).encode('utf-8')
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / 'VERSION').write_text(local_version, encoding='utf-8')
            (root / 'RELEASE_REVISION').write_text(json.dumps(local_revision), encoding='utf-8')
            with (
                patch.object(check_update, 'BASE_DIR', root),
                patch.object(check_update, 'raw_file_urls', return_value=['http://updates/release_manifest.json']),
                patch.object(check_update.urllib.request, 'urlopen', return_value=io.BytesIO(manifest)),
            ):
                return check_update.check_via_http()

    def test_same_day_new_revision_is_an_update(self):
        check = self.check_versions('2026.09.06', '2026.09.06', 1, 2)
        self.assertTrue(check['has_update'])
        self.assertEqual((check['local_revision'], check['remote_revision']), (1, 2))

    def test_same_day_older_revision_is_not_an_update(self):
        self.assertFalse(self.check_versions('2026.09.06', '2026.09.06', 2, 1)['has_update'])

    def test_new_calendar_date_takes_priority_over_revision(self):
        self.assertTrue(self.check_versions('2026.09.06', '2026.09.07', 10, 0)['has_update'])

    def test_invalid_revision_is_reported_as_error(self):
        for revision in (-1, True, 1.5, '2', None):
            with self.subTest(revision=revision):
                check = self.check_versions('2026.09.06', '2026.09.06', 0, revision)
                self.assertFalse(check['has_update'])
                self.assertIsNotNone(check['error'])

    def test_bad_manifest_uses_next_mirror_and_legacy_local_revision_is_zero(self):
        manifest = json.dumps({
            'manifest_version': 1,
            'release': {'version': '2026.09.06', 'revision': 1},
            'files': [{'path': name, 'sha256': '0' * 64} for name in ('VERSION', 'RELEASE_REVISION')],
        }).encode('utf-8')
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / 'VERSION').write_text('2026.09.06', encoding='utf-8')
            with (
                patch.object(check_update, 'BASE_DIR', root),
                patch.object(check_update, 'raw_file_urls', return_value=['http://first/manifest', 'http://second/manifest']),
                patch.object(check_update.urllib.request, 'urlopen', side_effect=[io.BytesIO(b'[]'), io.BytesIO(manifest)]) as download,
            ):
                check = check_update.check_via_http()
        self.assertTrue(check['has_update'])
        self.assertEqual(check['local_revision'], 0)
        self.assertEqual(download.call_count, 2)

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
