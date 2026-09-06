import json
import os
import shutil
import subprocess
import tempfile
import unittest
import venv
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent


@unittest.skipUnless(os.name == 'nt', 'Windows batch entrypoints require cmd.exe')
class TestWindowsEntrypoints(unittest.TestCase):
    def setUp(self):
        temporary_project = tempfile.TemporaryDirectory(prefix='cnipa windows ')
        self.addCleanup(temporary_project.cleanup)
        self.project_root = Path(temporary_project.name)
        for batch_name in ('setup.bat', 'run.bat', 'launch_browser.bat', 'upgrade.bat'):
            shutil.copy2(PROJECT_ROOT / batch_name, self.project_root / batch_name)
        self.command_environment = os.environ.copy()
        self.command_environment['PATH'] = os.pathsep.join((
            str(self.project_root),
            str(Path(os.environ['SystemRoot']) / 'System32'),
        ))
        self.command_environment.pop('MITM_PORT', None)

    def run_batch(self, batch_name, *arguments):
        return subprocess.run(
            [os.environ['COMSPEC'], '/d', '/c', batch_name, *arguments],
            cwd=self.project_root,
            env=self.command_environment,
            input='\n',
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=25,
        )

    def test_setup_requires_uv(self):
        completed = self.run_batch('setup.bat')
        self.assertNotEqual(completed.returncode, 0)
        self.assertNotIn('[OK]', completed.stdout)

    def test_setup_does_not_report_success_after_installation_failure(self):
        (self.project_root / 'uv.cmd').write_text('@exit /b 17\n', encoding='ascii')

        completed = self.run_batch('setup.bat')

        self.assertNotEqual(completed.returncode, 0)
        self.assertNotIn('[OK]', completed.stdout)

    def test_setup_installs_the_frozen_python_311_runtime(self):
        (self.project_root / 'uv.cmd').write_text(
            '@echo off\n>"%~dp0uv_arguments.txt" echo %*\nexit /b 0\n',
            encoding='ascii',
        )

        completed = self.run_batch('setup.bat')

        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertEqual(
            (self.project_root / 'uv_arguments.txt').read_text().split(),
            ['sync', '--frozen', '--python', '3.11', '--no-dev'],
        )
        self.assertIn('[OK]', completed.stdout)

    def test_collection_requires_the_project_environment(self):
        completed = self.run_batch('run.bat')
        self.assertNotEqual(completed.returncode, 0)
        self.assertFalse((self.project_root / '.mitm.log').exists())

    def test_collection_uses_project_python_without_overriding_the_proxy_port(self):
        venv.EnvBuilder(with_pip=False).create(self.project_root / '.venv')
        (self.project_root / 'start_mitm_proxy.py').write_text(
            'print("proxy", flush=True)\n', encoding='ascii',
        )
        # An executable substitute preserves batch control flow without killing processes.
        shutil.copy2(
            Path(os.environ['SystemRoot']) / 'System32' / 'where.exe',
            self.project_root / 'taskkill.exe',
        )
        (self.project_root / 'main_automation.py').write_text(
            'import json, os, pathlib, sys\n'
            'pathlib.Path("collection_launch.json").write_text(json.dumps({\n'
            '    "executable": sys.executable, "arguments": sys.argv[1:],\n'
            '    "proxy_port": os.environ.get("MITM_PORT"),\n'
            '    "use_proxy": os.environ.get("USE_MITM_PROXY"),\n'
            '}))\n'
            'raise SystemExit(13)\n',
            encoding='ascii',
        )

        completed = self.run_batch('run.bat', '--test', '2')

        self.assertEqual(completed.returncode, 13, completed.stdout + completed.stderr)
        launch = json.loads((self.project_root / 'collection_launch.json').read_text())
        self.assertEqual(
            Path(launch['executable']),
            self.project_root / '.venv' / 'Scripts' / 'python.exe',
        )
        self.assertEqual(launch['arguments'], ['--test', '2'])
        self.assertIsNone(launch['proxy_port'])
        self.assertEqual(launch['use_proxy'], 'true')

    def test_browser_delegates_to_the_python_entrypoint_and_preserves_exit_code(self):
        venv.EnvBuilder(with_pip=False).create(self.project_root / '.venv')
        (self.project_root / 'launch_browser_with_proxy.py').write_text(
            'import pathlib, sys\n'
            'pathlib.Path("browser_python.txt").write_text(sys.executable)\n'
            'raise SystemExit(19)\n',
            encoding='ascii',
        )

        completed = self.run_batch('launch_browser.bat')

        self.assertEqual(completed.returncode, 19, completed.stdout + completed.stderr)
        self.assertEqual(
            Path((self.project_root / 'browser_python.txt').read_text()),
            self.project_root / '.venv' / 'Scripts' / 'python.exe',
        )

    def test_upgrade_uses_the_project_python_http_updater_and_preserves_exit_code(self):
        venv.EnvBuilder(with_pip=False).create(self.project_root / '.venv')
        (self.project_root / 'fetch_update.py').write_text(
            'import json, pathlib, sys\n'
            'pathlib.Path("update_launch.json").write_text(json.dumps({\n'
            '    "executable": sys.executable, "arguments": sys.argv[1:],\n'
            '}))\n'
            'raise SystemExit(23)\n',
            encoding='ascii',
        )

        completed = self.run_batch('upgrade.bat', '--check')

        self.assertEqual(completed.returncode, 23, completed.stdout + completed.stderr)
        launch = json.loads((self.project_root / 'update_launch.json').read_text())
        self.assertEqual(
            Path(launch['executable']),
            self.project_root / '.venv' / 'Scripts' / 'python.exe',
        )
        self.assertEqual(launch['arguments'], ['--check'])
