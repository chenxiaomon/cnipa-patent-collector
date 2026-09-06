import unittest
from unittest.mock import patch

import web_dashboard
from code_release_safety import CodeReleaseVerificationError, CodeReleaseVersion


class DashboardReleaseStatusTests(unittest.TestCase):
    def test_full_reconciliation_invokes_the_replica_sync_command(self):
        with patch.object(web_dashboard, 'resolve_task_python', return_value='python'):
            spec = web_dashboard.build_job_spec('sync_reconcile_master', {})
        self.assertEqual(spec['command'], ['python', '-u', 'sync_pull_from_master.py', '--full'])

    def test_installation_needs_restart_until_the_running_release_matches(self):
        earlier = CodeReleaseVersion('2026.09.06', 1)
        newer = CodeReleaseVersion('2026.09.06', 2)
        with patch.object(web_dashboard.CodeReleaseVersion, 'read', return_value=newer):
            with patch.object(web_dashboard, '_RUNNING_CODE_RELEASE', earlier):
                waiting = web_dashboard.dashboard_release_status()
            with patch.object(web_dashboard, '_RUNNING_CODE_RELEASE', newer):
                restarted = web_dashboard.dashboard_release_status()
        self.assertEqual(waiting['running'], '2026.09.06 r1')
        self.assertEqual(waiting['installed'], '2026.09.06 r2')
        self.assertTrue(waiting['restart_required'])
        self.assertFalse(restarted['restart_required'])

    def test_rollback_also_requires_restart(self):
        with (
            patch.object(web_dashboard, '_RUNNING_CODE_RELEASE', CodeReleaseVersion('2026.09.06', 2)),
            patch.object(web_dashboard.CodeReleaseVersion, 'read', return_value=CodeReleaseVersion('2026.09.06', 1)),
        ):
            self.assertTrue(web_dashboard.dashboard_release_status()['restart_required'])

    def test_damaged_version_file_reports_an_error_instead_of_breaking_summary(self):
        with patch.object(web_dashboard.CodeReleaseVersion, 'read', side_effect=CodeReleaseVerificationError('Invalid revision')):
            status = web_dashboard.dashboard_release_status()
        self.assertEqual(status['error'], 'Invalid revision')
        self.assertIsNone(status['installed'])
        self.assertFalse(status['restart_required'])
