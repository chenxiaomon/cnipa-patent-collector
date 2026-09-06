import json
import os
import plistlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import environment_diagnostics as diagnostics


class TestEnvironmentDiagnostics(unittest.TestCase):
    def setUp(self):
        temporary_project = tempfile.TemporaryDirectory(prefix='cnipa-diagnostics-')
        self.addCleanup(temporary_project.cleanup)
        self.project_root = Path(temporary_project.name)
        self.database_path = self.project_root / 'patents.db'
        self.search_path = self.project_root / 'config.json'
        self.detail_path = self.project_root / 'config_fwxx.json'
        for constant_name, constant_value in (
            ('PATENTS_DB_FILE', self.database_path),
            ('CONFIG_FILE', self.search_path),
            ('CONFIG_FWXX_FILE', self.detail_path),
            ('MANUAL_CHROMEDRIVER_DIRS', (self.project_root / 'chromedriver-test',)),
        ):
            setting_patch = patch.object(diagnostics, constant_name, constant_value)
            setting_patch.start()
            self.addCleanup(setting_patch.stop)

    def test_selected_python_reports_its_runtime_and_imports_in_subprocess(self):
        with patch.object(diagnostics, '_DEPENDENCIES', (('json', 'distribution-not-installed'),)):
            checks, cache_directory = diagnostics._inspect_python(sys.executable)

        self.assertEqual(checks[0]['details']['executable'], sys.executable)
        self.assertEqual(checks[1]['details']['importable'], True)
        self.assertIsNone(checks[1]['details']['version'])
        self.assertIsNone(cache_directory)

    def test_windows_dependency_filter_accepts_probe_subset(self):
        with patch.object(diagnostics, '_DEPENDENCIES', (('json', 'distribution-not-installed'),)), patch.object(
            diagnostics.sys, 'platform', 'win32',
        ):
            checks, cache_directory = diagnostics._inspect_python(sys.executable)

        self.assertEqual([check['id'] for check in checks], ['python', 'dependency_json'])
        self.assertIsNone(cache_directory)

    def test_missing_selected_python_does_not_fall_back_to_dashboard(self):
        checks, _ = diagnostics._inspect_python(str(self.project_root / 'missing-python'))

        self.assertEqual(checks[0]['status'], 'error')
        self.assertEqual(checks[0]['details']['executable'], str(self.project_root / 'missing-python'))
        self.assertTrue(all(check['status'] == 'unknown' for check in checks[1:]))

    def test_dependency_exception_does_not_reveal_exception_message(self):
        isolated_import = diagnostics._PYTHON_PROBE.replace(
            'importlib.import_module(module_name)',
            '(_ for _ in ()).throw(RuntimeError("password=secret-api-token"))',
        )
        with patch.object(diagnostics, '_DEPENDENCIES', (('json', 'distribution-not-installed'),)), patch.object(
            diagnostics, '_PYTHON_PROBE', isolated_import,
        ):
            checks, _ = diagnostics._inspect_python(sys.executable)

        self.assertEqual(checks[1]['status'], 'error')
        self.assertEqual(checks[1]['details']['exception_type'], 'RuntimeError')
        self.assertNotIn('secret-api-token', json.dumps(checks))

    def test_timeout_preserves_completed_dependency_checks(self):
        emitted_lines = '\n'.join(json.dumps(probe) for probe in (
            {'id': 'python', 'version': '3.11.9', 'executable': 'collection-python', 'supported': True, 'recommended': True},
            {'id': 'dependency_selenium', 'version': '4.1.0', 'module': 'selenium', 'importable': True},
        ))
        with patch.object(diagnostics.subprocess, 'run', side_effect=subprocess.TimeoutExpired(
            ['collection-python'], 25, output=emitted_lines.encode(),
        )) as run_probe:
            checks, _ = diagnostics._inspect_python('collection-python')

        self.assertEqual(run_probe.call_args.args[0][0], 'collection-python')
        self.assertEqual(checks[0]['status'], 'ok')
        self.assertEqual(checks[1]['status'], 'ok')
        self.assertEqual(checks[2]['status'], 'unknown')

    def test_macos_reads_bundle_metadata_without_launching_chrome(self):
        applications_directory = self.project_root / 'Applications'
        bundle_contents = applications_directory / 'Google Chrome.app' / 'Contents'
        bundle_contents.mkdir(parents=True)
        with (bundle_contents / 'Info.plist').open('wb') as plist_stream:
            plistlib.dump({'CFBundleShortVersionString': '140.0.7339.81'}, plist_stream)
        real_open = Path.open

        def read_local_plist(path, *arguments, **keywords):
            if str(path).startswith('/Applications/'):
                raise FileNotFoundError(path)
            return real_open(path, *arguments, **keywords)

        with patch.object(diagnostics.Path, 'home', return_value=self.project_root), patch.object(
            Path, 'open', read_local_plist,
        ), patch.object(diagnostics.subprocess, 'run') as launch:
            installation = diagnostics._macos_chrome_version()

        self.assertEqual(installation['version'], '140.0.7339.81')
        launch.assert_not_called()

    def test_windows_reads_registry_without_running_chrome_executable(self):
        registry_key = Mock()
        registry_key.__enter__ = Mock(return_value=registry_key)
        registry_key.__exit__ = Mock(return_value=False)
        registry = SimpleNamespace(
            HKEY_CURRENT_USER='current-user', HKEY_LOCAL_MACHINE='local-machine',
            OpenKey=Mock(return_value=registry_key),
            QueryValueEx=Mock(return_value=('140.0.7339.81', 1)),
        )
        with patch.dict(sys.modules, {'winreg': registry}), patch.object(diagnostics.subprocess, 'run') as launch:
            installation = diagnostics._windows_chrome_version()

        self.assertEqual(installation['version'], '140.0.7339.81')
        launch.assert_not_called()

    def test_driver_check_requires_matching_major_and_uses_version_only(self):
        driver_directory = self.project_root / 'chromedriver-test'
        driver_directory.mkdir()
        driver_name = 'chromedriver.exe' if sys.platform == 'win32' else 'chromedriver'
        driver_path = driver_directory / driver_name
        driver_path.touch()
        with patch.object(diagnostics.subprocess, 'run', return_value=SimpleNamespace(
            returncode=0, stdout='ChromeDriver 139.0.7258.1 (build)',
        )) as version_query, patch.object(diagnostics.glob, 'glob', return_value=[]):
            check = diagnostics._inspect_chromedriver('140.0.7339.81', None)

        self.assertEqual(check['status'], 'error')
        self.assertEqual(version_query.call_args.args[0], [str(driver_path), '--version'])
        with patch.object(diagnostics.subprocess, 'run', return_value=SimpleNamespace(
            returncode=0, stdout='ChromeDriver 140.0.7339.1 (build)',
        )), patch.object(diagnostics.glob, 'glob', return_value=[]):
            check = diagnostics._inspect_chromedriver('140.0.7339.81', None)
        self.assertEqual(check['status'], 'ok')

    def test_absent_driver_does_not_download_or_start_anything(self):
        with patch.object(diagnostics.glob, 'glob', return_value=[]), patch.object(
            diagnostics.subprocess, 'run',
        ) as launch:
            check = diagnostics._inspect_chromedriver('140.0.7339.81', None)

        self.assertEqual(check['status'], 'warning')
        launch.assert_not_called()

    def test_remote_proxy_is_not_contacted_or_disclosed(self):
        for configured_address in ('203.0.113.8', 'proxy.example.com', 'user:password@proxy.example.com'):
            with self.subTest(address=configured_address), patch.object(
                diagnostics, 'MITM_HOST', configured_address,
            ), patch.object(diagnostics.socket, 'create_connection') as connect:
                check = diagnostics._inspect_proxy('proxy_main', 'Main proxy', 8083)

            self.assertEqual(check['status'], 'unknown')
            self.assertNotIn(configured_address, json.dumps(check))
            connect.assert_not_called()

    def test_loopback_connection_is_only_tcp_and_bounded(self):
        with patch.object(diagnostics, 'MITM_HOST', 'localhost'), patch.object(
            diagnostics.socket, 'create_connection',
        ) as connect:
            check = diagnostics._inspect_proxy('proxy_main', 'Main proxy', 8083)

        self.assertEqual(check['status'], 'ok')
        connect.assert_called_once_with(('127.0.0.1', 8083), timeout=0.5)
        connect.return_value.__enter__.return_value.send.assert_not_called()

    def test_closed_proxy_is_warning(self):
        with patch.object(diagnostics, 'MITM_HOST', '127.0.0.1'), patch.object(
            diagnostics.socket, 'create_connection', side_effect=ConnectionRefusedError,
        ):
            check = diagnostics._inspect_proxy('proxy_main', 'Main proxy', 8083)

        self.assertEqual(check['status'], 'warning')
        self.assertFalse(check['details']['reachable'])

    def test_coordinate_errors_do_not_disclose_unrelated_configuration(self):
        for invalid_coordinates in (
            [], {'input_x': True, 'input_y': 100}, {'input_x': 0, 'input_y': 0},
            {'input_x': '10', 'input_y': 100},
        ):
            with self.subTest(coordinates=invalid_coordinates):
                if isinstance(invalid_coordinates, dict):
                    invalid_coordinates['password'] = 'secret-api-token'
                self.search_path.write_text(json.dumps(invalid_coordinates), encoding='utf-8')
                check = diagnostics._inspect_coordinates(
                    'coordinates_search', 'Search', self.search_path, (('input_x', 'input_y'),),
                )
                self.assertEqual(check['status'], 'warning')
                self.assertNotIn('secret-api-token', json.dumps(check))

    def test_coordinate_negative_monitor_positions_are_valid(self):
        self.search_path.write_text(json.dumps({
            'input_x': -100, 'input_y': 200, 'button_x': -200, 'button_y': 300,
        }), encoding='utf-8')
        pairs = (('input_x', 'input_y'), ('button_x', 'button_y'))
        check = diagnostics._inspect_coordinates('coordinates_search', 'Search', self.search_path, pairs)
        self.assertEqual(check['status'], 'ok')

    def test_detail_diagnostic_checks_every_supported_coordinate_pair(self):
        detail_coordinates = {
            'link_x': 100, 'link_y': 200,
            'fwxx_menu_x': 300, 'fwxx_menu_y': 400,
            'fee_menu_x': 0, 'fee_menu_y': 0,
        }
        self.detail_path.write_text(json.dumps(detail_coordinates), encoding='utf-8')

        check = diagnostics._inspect_coordinates(
            'coordinates_detail', 'Detail', self.detail_path,
            diagnostics.DETAIL_COORDINATE_PAIRS,
        )

        self.assertEqual(check['status'], 'warning')
        self.assertIn('fee_menu', check['summary'])

    def test_storage_probe_preserves_database_and_leaves_no_temporary_file(self):
        original_bytes = b'not a sqlite database: must never be opened by sqlite'
        self.database_path.write_bytes(original_bytes)
        with patch('sqlite3.connect', side_effect=AssertionError('must not open database')):
            check = diagnostics._inspect_database_storage()

        self.assertEqual(check['status'], 'ok')
        self.assertFalse(check['details']['sqlite_write_tested'])
        self.assertEqual(self.database_path.read_bytes(), original_bytes)
        self.assertEqual(list(self.project_root.iterdir()), [self.database_path])

    def test_storage_probe_does_not_create_a_missing_database(self):
        check = diagnostics._inspect_database_storage()

        self.assertEqual(check['status'], 'ok')
        self.assertEqual(check['details']['existing_files'], [])
        self.assertFalse(self.database_path.exists())

    def test_database_directory_permission_failure_is_reported_without_exception_text(self):
        with patch.object(diagnostics.tempfile, 'TemporaryFile', side_effect=PermissionError('secret-api-token')):
            check = diagnostics._inspect_database_storage()

        self.assertEqual(check['status'], 'error')
        self.assertEqual(check['details']['exception_type'], 'PermissionError')
        self.assertNotIn('secret-api-token', json.dumps(check))

    def test_readonly_existing_database_or_wal_is_reported(self):
        self.database_path.touch()
        wal_path = Path(str(self.database_path) + '-wal')
        wal_path.touch()
        real_access = os.access

        with patch.object(diagnostics.os, 'access', side_effect=lambda path, mode: (
            False if path == wal_path else real_access(path, mode)
        )):
            check = diagnostics._inspect_database_storage()

        self.assertEqual(check['status'], 'error')
        self.assertEqual(check['details']['existing_files'][1]['writable'], False)

    def test_complete_report_is_downloadable_json_for_this_machine_only(self):
        dependency_measurements = {
            'module': 'selenium', 'version': '4.32.0', 'importable': False,
            'exception_type': 'ImportError',
        }
        dependency_check = {
            'id': 'dependency_selenium', 'title': 'selenium', 'status': 'error',
            'summary': '导入失败 · 4.32.0', 'details': dependency_measurements, 'suggestion': '',
        }
        self.database_path.touch()
        with patch.object(diagnostics, '_inspect_python', return_value=([dependency_check], None)) as python_probe, patch.object(
            diagnostics, '_inspect_chrome', return_value={
                'id': 'chrome', 'title': 'Chrome', 'status': 'unknown', 'details': {},
                'summary': 'Unknown', 'suggestion': '',
            },
        ), patch.object(diagnostics, 'MITM_HOST', 'remote.example.com'), patch.object(
            diagnostics.glob, 'glob', return_value=[],
        ):
            report = diagnostics.run_environment_diagnostics('collection-python')

        python_probe.assert_called_once_with('collection-python')
        self.assertEqual(json.loads(json.dumps(report))['scope'], 'local_machine')
        self.assertEqual(report['schema_version'], 1)
        self.assertTrue(all(set(('id', 'title', 'status', 'summary', 'details', 'suggestion')) <= check.keys() for check in report['checks']))
        self.assertTrue(all(isinstance(check['details'], list) for check in report['checks']))
        self.assertTrue(all(isinstance(check['measurements'], dict) for check in report['checks']))
        self.assertNotIn('remote.example.com', json.dumps(report))
        by_check_id = {check['id']: check for check in report['checks']}
        self.assertEqual(by_check_id['dependency_selenium']['details'], ['异常类型：ImportError'])
        self.assertEqual(by_check_id['dependency_selenium']['measurements'], dependency_measurements)
        self.assertEqual(by_check_id['chrome']['details'], [])
        self.assertEqual(by_check_id['chromedriver']['details'], [])
        self.assertEqual(by_check_id['proxy_main']['details'], [f'代理端口：{diagnostics.MITM_PORT}'])
        self.assertEqual(by_check_id['database_storage']['details'], [
            '数据目录：' + str(self.project_root), '文件权限：patents.db（可写）',
        ])


if __name__ == '__main__':
    unittest.main()
