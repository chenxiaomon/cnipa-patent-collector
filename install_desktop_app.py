#!/usr/bin/env python3
"""Install a local macOS application icon that launches this project's Python environment."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile

from settings import BASE_DIR, DESKTOP_APP_DIRECTORY, DESKTOP_ICON_ICNS_FILE, MACOS_DESKTOP_LAUNCHER_SOURCE


DESKTOP_BUNDLE_ID = 'local.cnipa.patent-collector.desktop'


def install_desktop_app(destination: Path) -> None:
    """Publish a complete new bundle, or update only files owned by our existing bundle."""
    if destination.suffix != '.app':
        raise ValueError('桌面入口路径必须以 .app 结尾')
    if destination.is_symlink():
        raise ValueError('不能覆盖符号链接，请选择新的 .app 路径')
    if destination.exists():
        existing_plist = destination / 'Contents' / 'Info.plist'
        with existing_plist.open('rb') as existing_file:
            existing_identity = plistlib.load(existing_file)
        if (
            existing_identity.get('CFBundleIdentifier') != DESKTOP_BUNDLE_ID
            or existing_identity.get('CNIPAProjectDirectory') != str(BASE_DIR.resolve())
        ):
            raise ValueError('目标已有其他应用或其他项目的入口，未作覆盖')
    icon_bytes = DESKTOP_ICON_ICNS_FILE.read_bytes()
    bundle_identity = {
        'CFBundleIdentifier': DESKTOP_BUNDLE_ID,
        'CFBundleName': 'CNIPA 专利采集',
        'CFBundleDisplayName': 'CNIPA 专利采集',
        'CFBundleExecutable': 'cnipa-desktop',
        'CFBundleIconFile': 'CNIPA.icns',
        'CFBundlePackageType': 'APPL',
        'CFBundleVersion': '1',
        'NSHighResolutionCapable': True,
        'LSApplicationCategoryType': 'public.app-category.productivity',
        'CNIPAProjectDirectory': str(BASE_DIR.resolve()),
    }
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.cnipa-icon-', dir=destination.parent) as staging_directory:
        staged_bundle = Path(staging_directory) / destination.name
        for relative_path, file_bytes in (
            ('Contents/Info.plist', plistlib.dumps(bundle_identity)),
            ('Contents/Resources/CNIPA.icns', icon_bytes),
        ):
            staged_file = staged_bundle / relative_path
            staged_file.parent.mkdir(parents=True, exist_ok=True)
            staged_file.write_bytes(file_bytes)
        executable_path = staged_bundle / 'Contents/MacOS/cnipa-desktop'
        executable_path.parent.mkdir(parents=True, exist_ok=True)
        # LaunchServices expects a native executable; Python itself remains the project interpreter.
        subprocess.run(
            ['/usr/bin/clang', str(MACOS_DESKTOP_LAUNCHER_SOURCE), '-framework', 'CoreFoundation',
             '-O2', '-Wall', '-Wextra', '-o', str(executable_path)],
            check=True,
        )
        executable_path.chmod(0o755)
        if destination.exists():
            for relative_path in ('Contents/Resources/CNIPA.icns', 'Contents/MacOS/cnipa-desktop', 'Contents/Info.plist'):
                os.replace(staged_bundle / relative_path, destination / relative_path)
        else:
            os.replace(staged_bundle, destination)


def main() -> int:
    parser = argparse.ArgumentParser(description='在 Mac 桌面安装带图标的 CNIPA 专利采集入口')
    parser.add_argument('--destination', type=Path, default=DESKTOP_APP_DIRECTORY,
                        help='应用入口位置，默认安装到桌面')
    arguments = parser.parse_args()
    if sys.platform != 'darwin':
        parser.error('此入口安装程序仅用于 macOS；Windows 请继续使用 desktop.bat')
    try:
        install_desktop_app(arguments.destination.expanduser().absolute())
    except (OSError, ValueError, plistlib.InvalidFileException, subprocess.CalledProcessError) as error:
        print(f'安装桌面图标失败：{error}', file=sys.stderr)
        return 1
    print(f'桌面图标已安装：{arguments.destination}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
