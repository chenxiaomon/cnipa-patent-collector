"""Inspect the collection runtime on this machine without starting collection."""

import glob
import ipaddress
import json
import os
import platform
import plistlib
import re
import shutil
import socket
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from settings import (
    CONFIG_FILE, CONFIG_FWXX_FILE, MANUAL_CHROMEDRIVER_DIRS,
    MITM_HOST, MITM_PORT, PATENTS_DB_FILE, PUBLIC_MITM_PORT,
)


_DEPENDENCIES = (
    ('selenium', 'selenium'),
    ('undetected_chromedriver', 'undetected-chromedriver'),
    ('pyautogui', 'PyAutoGUI'),
    ('mitmproxy', 'mitmproxy'),
    ('pandas', 'pandas'),
    ('openpyxl', 'openpyxl'),
    ('setuptools', 'setuptools'),
    ('pyvirtualdisplay', 'PyVirtualDisplay'),
)

_PYTHON_PROBE = '''
import contextlib
import importlib
import importlib.metadata
import io
import json
import platform
import sys

print(json.dumps({"id": "python", "version": platform.python_version(),
    "executable": sys.executable, "supported": sys.version_info >= (3, 10),
    "recommended": sys.version_info[:2] == (3, 11)}), flush=True)
for module_name, distribution_name in json.loads(sys.argv[1]):
    if module_name == "pyvirtualdisplay" and sys.platform == "win32":
        continue
    dependency = {"id": "dependency_" + module_name, "module": module_name,
        "distribution": distribution_name, "version": None, "importable": False}
    try:
        dependency["version"] = importlib.metadata.version(distribution_name)
    except importlib.metadata.PackageNotFoundError:
        pass
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            imported_module = importlib.import_module(module_name)
        dependency["importable"] = True
        if module_name == "undetected_chromedriver":
            dependency["driver_cache_directory"] = imported_module.Patcher.data_path
    except BaseException as exception:
        dependency["exception_type"] = type(exception).__name__
    print(json.dumps(dependency), flush=True)
'''


def _inspect_python(python_executable: str) -> tuple[list[dict], str | None]:
    expected_probes = dict(_DEPENDENCIES)
    if sys.platform == 'win32':
        expected_probes.pop('pyvirtualdisplay')
    probe_output = ''
    execution_error = None
    try:
        completed_probe = subprocess.run(
            [python_executable, '-I', '-B', '-c', _PYTHON_PROBE, json.dumps(_DEPENDENCIES)],
            capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=25,
        )
        probe_output = completed_probe.stdout
        if completed_probe.returncode:
            execution_error = 'Python 子进程异常退出'
    except subprocess.TimeoutExpired as exception:
        captured_output = exception.stdout or ''
        probe_output = (
            captured_output.decode('utf-8', errors='replace')
            if isinstance(captured_output, bytes) else captured_output
        )
        execution_error = '依赖导入超过 25 秒，未完成项目标记为未知'
    except (OSError, ValueError):
        execution_error = '无法执行采集 Python，请检查项目 .venv 是否安装完整'

    received_probes = {}
    for output_line in probe_output.splitlines():
        try:
            probe = json.loads(output_line)
        except json.JSONDecodeError:
            continue
        if isinstance(probe, dict) and isinstance(probe.get('id'), str):
            received_probes[probe['id']] = probe

    python_probe = received_probes.get('python')
    if python_probe:
        python_status = 'ok' if python_probe['recommended'] else 'warning'
        if not python_probe['supported']:
            python_status = 'error'
        checks = [{
            'id': 'python', 'title': '采集 Python', 'status': python_status,
            'summary': 'Python ' + python_probe['version'],
            'details': {
                'executable': python_probe['executable'],
                'version': python_probe['version'],
                'supported': python_probe['supported'],
            },
            'suggestion': '' if python_probe['recommended'] else '部署脚本使用 Python 3.11；建议通过 setup.bat 或 uv sync --frozen --python 3.11 安装。',
        }]
    else:
        checks = [{
            'id': 'python', 'title': '采集 Python', 'status': 'error',
            'summary': execution_error or '采集 Python 未返回有效版本信息',
            'details': {'executable': python_executable},
            'suggestion': '在运行 Dashboard 的这台机器上安装项目 .venv 后重新诊断。',
        }]

    driver_cache_directory = None
    for module_name, distribution_name in expected_probes.items():
        check_id = 'dependency_' + module_name
        dependency = received_probes.get(check_id)
        if dependency is None:
            checks.append({
                'id': check_id, 'title': distribution_name, 'status': 'unknown',
                'summary': execution_error or '未完成依赖导入检查',
                'details': {'module': module_name, 'version': None, 'importable': None},
                'suggestion': '先修复采集 Python，再重新诊断。',
            })
            continue
        dependency.pop('id')
        if module_name == 'undetected_chromedriver':
            driver_cache_directory = dependency.pop('driver_cache_directory', None)
        checks.append({
            'id': check_id, 'title': distribution_name,
            'status': 'ok' if dependency['importable'] else 'error',
            'summary': ('可导入' if dependency['importable'] else '导入失败')
                       + (' · ' + dependency['version'] if dependency['version'] else ''),
            'details': dependency,
            'suggestion': '' if dependency['importable'] else (
                '在本机登录桌面会话中运行；无显示设备或桌面权限不足也会导致 PyAutoGUI 导入失败。'
                if module_name == 'pyautogui'
                else '使用项目 setup.bat 或 uv sync --frozen --python 3.11 修复依赖。'
            ),
        })
    return checks, driver_cache_directory


def _version_number(version_text: str) -> str | None:
    version_match = re.search(r'\b\d+\.\d+\.\d+\.\d+\b', version_text)
    return version_match.group() if version_match else None


def _windows_chrome_version() -> dict:
    try:
        import winreg
    except ImportError:
        return {}
    for registry_root, registry_path in (
        (winreg.HKEY_CURRENT_USER, r'Software\Google\Chrome\BLBeacon'),
        (winreg.HKEY_LOCAL_MACHINE, r'Software\Google\Chrome\BLBeacon'),
        (winreg.HKEY_LOCAL_MACHINE, r'Software\WOW6432Node\Google\Chrome\BLBeacon'),
    ):
        try:
            with winreg.OpenKey(registry_root, registry_path) as registry_key:
                chrome_version, _ = winreg.QueryValueEx(registry_key, 'version')
            parsed_version = _version_number(str(chrome_version))
            if parsed_version:
                return {'version': parsed_version, 'source': 'Windows Chrome BLBeacon 注册表'}
        except OSError:
            continue
    return {}


def _macos_chrome_version() -> dict:
    for app_bundle in (
        Path('/Applications/Google Chrome.app'),
        Path.home() / 'Applications' / 'Google Chrome.app',
        Path('/Applications/Google Chrome Beta.app'),
        Path('/Applications/Chromium.app'),
    ):
        try:
            with (app_bundle / 'Contents' / 'Info.plist').open('rb') as plist_stream:
                bundle_manifest = plistlib.load(plist_stream)
            if not isinstance(bundle_manifest, dict):
                continue
            parsed_version = _version_number(str(bundle_manifest.get('CFBundleShortVersionString', '')))
            if parsed_version:
                return {'version': parsed_version, 'source': str(app_bundle / 'Contents' / 'Info.plist')}
        except (OSError, ValueError, plistlib.InvalidFileException):
            continue
    return {}


def _linux_chrome_version() -> dict:
    executable = next((
        executable_path
        for command in ('google-chrome', 'google-chrome-stable', 'chromium-browser', 'chromium')
        if (executable_path := shutil.which(command))
    ), None)
    if not executable:
        return {}
    # Package metadata is read without running the browser binary.
    for package_name in ('google-chrome-stable', 'chromium', 'chromium-browser'):
        try:
            package_query = subprocess.run(
                ['dpkg-query', '-W', '-f=${Version}', package_name],
                capture_output=True, text=True, timeout=2,
            )
            chrome_version = _version_number(package_query.stdout)
            if package_query.returncode == 0 and chrome_version:
                return {'version': chrome_version, 'source': 'dpkg-query: ' + package_name, 'executable': executable}
        except (OSError, subprocess.TimeoutExpired):
            break
    return {'version': None, 'executable': executable}


def _inspect_chrome() -> dict:
    if sys.platform == 'win32':
        installation = _windows_chrome_version()
    elif sys.platform == 'darwin':
        installation = _macos_chrome_version()
    else:
        installation = _linux_chrome_version()
    return {
        'id': 'chrome', 'title': 'Chrome 版本',
        'status': 'ok' if installation.get('version') else 'unknown',
        'summary': installation.get('version') or '未能从本机安装元数据确认 Chrome 版本',
        'details': installation,
        'suggestion': '' if installation.get('version') else '在本机 Chrome 的“关于 Chrome”中确认版本；本次诊断未启动浏览器。',
    }


def _inspect_chromedriver(chrome_version: str | None, cache_directory: str | None) -> dict:
    executable_name = 'chromedriver.exe' if sys.platform == 'win32' else 'chromedriver'
    candidates = [directory / executable_name for directory in MANUAL_CHROMEDRIVER_DIRS]
    if cache_directory:
        candidates.extend(Path(cache_directory).glob('undetected_chromedriver*'))
    if sys.platform == 'win32':
        local_app_directory = os.environ.get('LOCALAPPDATA')
        if local_app_directory:
            candidates.extend(Path(filename) for filename in glob.glob(str(
                Path(local_app_directory) / 'Temp' / 'chromedriver-win64-*' / 'chromedriver-win64' / 'chromedriver.exe'
            )))
    elif sys.platform.startswith('linux'):
        candidates.extend(Path(filename) for filename in glob.glob(
            '/tmp/chromedriver-linux64-*/chromedriver-linux64/chromedriver'
        ))
    driver_versions = []
    for candidate in dict.fromkeys(candidates):
        driver_version = None
        exception_type = None
        try:
            if not candidate.is_file():
                continue
            version_query = subprocess.run(
                [str(candidate), '--version'], capture_output=True, text=True, timeout=2,
            )
            if version_query.returncode == 0:
                driver_version = _version_number(version_query.stdout)
        except (OSError, subprocess.TimeoutExpired) as exception:
            exception_type = type(exception).__name__
        driver_versions.append({
            'path': str(candidate), 'version': driver_version,
            'exception_type': exception_type,
        })
    matching_driver = next((
        driver for driver in driver_versions
        if driver['version'] and chrome_version
        and driver['version'].split('.')[0] == chrome_version.split('.')[0]
    ), None)
    if matching_driver:
        status, summary = 'ok', '已有与 Chrome 主版本匹配的驱动：' + matching_driver['version']
    elif not driver_versions:
        status, summary = 'warning', '未找到可复用的本机 ChromeDriver'
    elif not chrome_version or not any(driver['version'] for driver in driver_versions):
        status, summary = 'unknown', '无法确认 Chrome 与 ChromeDriver 的主版本是否匹配'
    else:
        status, summary = 'error', '本机已有驱动均不匹配 Chrome 主版本'
    return {
        'id': 'chromedriver', 'title': 'ChromeDriver', 'status': status, 'summary': summary,
        'details': {'chrome_version': chrome_version, 'candidates': driver_versions},
        'suggestion': '' if matching_driver else '准备与本机 Chrome 主版本相同的 ChromeDriver；采集启动时也可能自动下载，但本次诊断不下载或启动驱动服务。',
    }


def _inspect_proxy(check_id: str, title: str, port: int) -> dict:
    local_address = MITM_HOST.strip()
    if local_address.lower() == 'localhost':
        local_address = '127.0.0.1'
    try:
        is_loopback = ipaddress.ip_address(local_address).is_loopback
    except ValueError:
        is_loopback = False
    if not is_loopback:
        return {
            'id': check_id, 'title': title, 'status': 'unknown',
            'summary': '代理配置不是回环地址，已跳过网络探测',
            'details': {'port': port, 'reachable': None},
            'suggestion': '本诊断只检查运行 Dashboard 的本机；远程代理需在对应机器确认。',
        }
    reachable = False
    try:
        with socket.create_connection((local_address, port), timeout=0.5):
            reachable = True
    except (OSError, OverflowError):
        pass
    return {
        'id': check_id, 'title': title, 'status': 'ok' if reachable else 'warning',
        'summary': '本机 TCP 端口可连接' if reachable else '本机 TCP 端口未连接',
        'details': {'host': local_address, 'port': port, 'reachable': reachable},
        'suggestion': 'TCP 可连接不代表代理拦截或 HTTPS 证书已配置成功。' if reachable else '需要采集时先启动对应代理，再重新诊断。',
    }


def _inspect_coordinates(check_id: str, title: str, coordinate_path: Path, pairs: tuple) -> dict:
    issues = []
    try:
        coordinate_config = json.loads(coordinate_path.read_text(encoding='utf-8'))
        if not isinstance(coordinate_config, dict):
            raise ValueError('Expected a coordinate object')
        for coordinate_x, coordinate_y in pairs:
            coordinates = (coordinate_config.get(coordinate_x), coordinate_config.get(coordinate_y))
            if not all(type(value) is int for value in coordinates):
                issues.append(coordinate_x.removesuffix('_x') + ' 坐标缺失或不是整数')
            elif coordinates == (0, 0):
                issues.append(coordinate_x.removesuffix('_x') + ' 仍是 (0, 0) 占位值')
    except FileNotFoundError:
        issues.append('坐标配置文件不存在')
    except (OSError, ValueError, UnicodeError):
        issues.append('坐标配置无法读取或 JSON 格式无效')
    return {
        'id': check_id, 'title': title, 'status': 'warning' if issues else 'ok',
        'summary': '；'.join(issues) if issues else '坐标格式检查通过',
        'details': {'path': str(coordinate_path), 'issues': issues},
        'suggestion': '在这台机器的实际采集窗口重新录制相关坐标。' if issues else '未操作鼠标或核验屏幕位置；分辨率、缩放或窗口位置变化后仍需重新录制。',
    }


def _inspect_database_storage() -> dict:
    database_directory = PATENTS_DB_FILE.parent
    directory_writable = False
    directory_exception = None
    try:
        # This disposable sibling tests directory creation only, never SQLite content.
        with tempfile.TemporaryFile(prefix='.cnipa-diagnostic-', dir=database_directory) as probe_file:
            probe_file.write(b'cnipa directory write probe\n')
            probe_file.flush()
            os.fsync(probe_file.fileno())
        directory_writable = True
    except OSError as exception:
        directory_exception = type(exception).__name__
    existing_files = []
    try:
        for suffix in ('', '-wal', '-shm'):
            database_path = Path(str(PATENTS_DB_FILE) + suffix)
            if database_path.exists():
                existing_files.append({
                    'path': str(database_path), 'is_file': database_path.is_file(),
                    'writable': os.access(database_path, os.W_OK),
                })
    except OSError as exception:
        directory_exception = type(exception).__name__
    permissions_passed = (
        directory_writable and directory_exception is None
        and all(database_file['is_file'] and database_file['writable'] for database_file in existing_files)
    )
    return {
        'id': 'database_storage', 'title': '数据库存储权限',
        'status': 'ok' if permissions_passed else 'error',
        'summary': '目录临时写入及已有文件权限检查通过' if permissions_passed else '数据库目录或已有文件权限检查未通过',
        'details': {
            'directory': str(database_directory), 'directory_writable': directory_writable,
            'exception_type': directory_exception, 'existing_files': existing_files,
            'sqlite_write_tested': False,
        },
        'suggestion': '未打开专利数据库，未执行 SQLite 写入；文件锁、数据库完整性和实际 SQLite 写入能力仍未知。'
            if permissions_passed else '检查项目 data 目录、patents.db 及其 -wal/-shm 文件的权限；本次未打开数据库或执行 SQLite 写入。',
    }


def run_environment_diagnostics(python_executable: str) -> dict:
    """Return a local report for the explicit collection interpreter; never save it."""
    checks, cache_directory = _inspect_python(python_executable)
    chrome_check = _inspect_chrome()
    checks.extend([
        chrome_check,
        _inspect_chromedriver(chrome_check['details'].get('version'), cache_directory),
        _inspect_proxy('proxy_main', '主 MITM 代理', MITM_PORT),
        _inspect_proxy('proxy_public', '公开查询代理', PUBLIC_MITM_PORT),
        _inspect_coordinates('coordinates_search', '搜索页坐标', CONFIG_FILE, (
            ('input_x', 'input_y'), ('button_x', 'button_y'),
        )),
        _inspect_coordinates('coordinates_detail', '详情页坐标', CONFIG_FWXX_FILE, (
            ('link_x', 'link_y'), ('fwxx_menu_x', 'fwxx_menu_y'), ('fee_menu_x', 'fee_menu_y'),
        )),
        _inspect_database_storage(),
    ])
    for check in checks:
        measurements = check['details']
        check['measurements'] = measurements
        check['details'] = [
            f'{label}：{measurements[field_name]}'
            for field_name, label in (
                ('executable', '程序路径'), ('source', '版本来源'),
                ('path', '配置文件'), ('directory', '数据目录'),
                ('exception_type', '异常类型'),
            )
            if measurements.get(field_name)
        ]
        if 'port' in measurements:
            endpoint = str(measurements['port'])
            if measurements.get('host'):
                endpoint = f"{measurements['host']}:{endpoint}"
            check['details'].append('代理端口：' + endpoint)
        for candidate in measurements.get('candidates', []):
            driver_description = candidate['version'] or '版本未确认'
            if candidate['exception_type']:
                driver_description += '，' + candidate['exception_type']
            check['details'].append(f"驱动：{candidate['path']}（{driver_description}）")
        for database_file in measurements.get('existing_files', []):
            permission_description = '可写' if database_file['writable'] else '不可写'
            if not database_file['is_file']:
                permission_description = '不是普通文件'
            check['details'].append(f"文件权限：{Path(database_file['path']).name}（{permission_description}）")
    return {
        'schema_version': 1,
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'scope': 'local_machine',
        'scope_label': '仅表示执行本次诊断的电脑，不代表其他电脑状态',
        'platform': {'system': platform.system(), 'release': platform.release(), 'machine': platform.machine()},
        'checks': checks,
    }
