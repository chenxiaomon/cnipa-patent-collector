#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
代码升级脚本

用法：
  python upgrade.py          # 拉取最新代码，按需重装依赖
  python upgrade.py --check  # 仅检查是否有更新，不做任何修改
"""
import os
import subprocess
import sys

if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from settings import BASE_DIR

_CWD = str(BASE_DIR)
_DEP_FILES = {'pyproject.toml', 'requirements.txt'}


def _run(args: list[str], check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        args, text=True, capture_output=True, check=check,
        cwd=_CWD, encoding='utf-8',
    )


def _installer_cmd() -> list[str]:
    """Returns ['uv', 'sync'] if uv is available, else pip install -e ."""
    try:
        subprocess.run(['uv', '--version'], capture_output=True, check=True)
        return ['uv', 'sync']
    except (FileNotFoundError, subprocess.CalledProcessError):
        return [sys.executable, '-m', 'pip', 'install', '-e', '.']


def _upstream_ref() -> str:
    """Return the upstream tracking ref (e.g. origin/main). Falls back to origin/main."""
    r = _run(['git', 'rev-parse', '--abbrev-ref', '--symbolic-full-name', '@{u}'], check=False)
    ref = r.stdout.strip()
    return ref if ref else 'origin/main'


def check_upstream() -> list[str]:
    """Fetch origin and return list of pending commit lines. Empty = up to date."""
    _run(['git', 'fetch', 'origin'])
    upstream = _upstream_ref()
    r = _run(['git', 'log', f'HEAD..{upstream}', '--oneline'])
    return [line for line in r.stdout.splitlines() if line.strip()]


def pull_and_upgrade() -> None:
    print("正在检查远端更新…")
    try:
        pending = check_upstream()
    except subprocess.CalledProcessError as e:
        print(f"✗ git 操作失败：{e.stderr.strip()}")
        sys.exit(1)

    if not pending:
        print("✓ 已是最新，无需更新。")
        return

    print(f"\n待拉取 {len(pending)} 个提交：")
    for line in pending:
        print(f"  {line}")

    old_head = _run(['git', 'rev-parse', 'HEAD']).stdout.strip()

    print("\n正在拉取代码…")
    try:
        _run(['git', 'pull'])
    except subprocess.CalledProcessError as e:
        print(f"✗ git pull 失败：{e.stderr.strip()}")
        sys.exit(1)
    print("✓ 代码已拉取。")

    changed = set(_run(['git', 'diff', old_head, 'HEAD', '--name-only']).stdout.splitlines())
    dep_changed = changed & _DEP_FILES

    if dep_changed:
        print(f"\n依赖文件变动（{', '.join(sorted(dep_changed))}），正在重装依赖…")
        cmd = _installer_cmd()
        result = subprocess.run(cmd, cwd=_CWD, encoding='utf-8', errors='replace')
        if result.returncode == 0:
            print("✓ 依赖已更新。")
        else:
            print(f"✗ 依赖安装失败，请手动运行：{' '.join(cmd)}")
    else:
        print("✓ 无依赖变化，跳过重装。")

    if changed:
        print(f"\n本次更新文件（{len(changed)} 个）：")
        for f in sorted(changed):
            print(f"  {f}")

    print("\n升级完成。")


def main() -> None:
    if '--check' in sys.argv:
        print("正在检查远端更新…")
        try:
            pending = check_upstream()
        except subprocess.CalledProcessError as e:
            print(f"✗ git 操作失败：{e.stderr.strip()}")
            sys.exit(1)
        if pending:
            print(f"有 {len(pending)} 个待拉取提交：")
            for line in pending:
                print(f"  {line}")
        else:
            print("✓ 已是最新。")
        return

    pull_and_upgrade()


if __name__ == '__main__':
    main()
