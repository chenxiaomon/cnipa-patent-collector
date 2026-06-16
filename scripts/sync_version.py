#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
版本号同步脚本（仿 cockpit-tools 的 sync-version.js）

VERSION 文件是项目唯一版本真相源。本脚本把它同步到 pyproject.toml，
保证两处一致；CI 用 --check 模式校验，不一致则失败。

用法：
  python scripts/sync_version.py          # 把 VERSION 写入 pyproject.toml
  python scripts/sync_version.py --check  # 仅校验两者一致（CI 用，不一致退出 1）

发版流程：改 VERSION 文件 → 跑本脚本 → 提交。
"""

import re
import sys
from pathlib import Path

# 脚本在 scripts/ 下，项目根是上一级
_ROOT = Path(__file__).resolve().parent.parent
_VERSION_FILE = _ROOT / 'VERSION'
_PYPROJECT = _ROOT / 'pyproject.toml'

# 匹配 [project] 段内的 version = "x.y.z"（行首，避免误匹配依赖里的版本约束）
_VERSION_RE = re.compile(r'^(version\s*=\s*")([^"]*)(")', re.MULTILINE)


def read_version() -> str:
    """读取 VERSION 文件的版本号（忽略空行和 # 注释行）。"""
    for line in _VERSION_FILE.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if line and not line.startswith('#'):
            return line
    raise ValueError(f"{_VERSION_FILE} 中找不到有效版本号")


def read_pyproject_version() -> str:
    """读取 pyproject.toml 中 [project] 的 version 字段。"""
    text = _PYPROJECT.read_text(encoding='utf-8')
    match = _VERSION_RE.search(text)
    if not match:
        raise ValueError("pyproject.toml 中找不到 version 字段")
    return match.group(2)


def write_pyproject_version(version: str) -> bool:
    """把 version 写入 pyproject.toml，返回是否发生改动。"""
    text = _PYPROJECT.read_text(encoding='utf-8')
    new_text, n = _VERSION_RE.subn(rf'\g<1>{version}\g<3>', text, count=1)
    if n == 0:
        raise ValueError("pyproject.toml 中找不到 version 字段，无法写入")
    if new_text == text:
        return False
    _PYPROJECT.write_text(new_text, encoding='utf-8')
    return True


def main() -> None:
    version = read_version()
    check_only = '--check' in sys.argv[1:]

    if check_only:
        current = read_pyproject_version()
        if current == version:
            print(f"[✓] 版本一致：{version}")
            sys.exit(0)
        print(f"[✗] 版本不一致：VERSION={version}  pyproject.toml={current}")
        print("    请运行：python scripts/sync_version.py")
        sys.exit(1)

    changed = write_pyproject_version(version)
    if changed:
        print(f"[✓] 已同步 pyproject.toml → {version}")
    else:
        print(f"[✓] pyproject.toml 已是 {version}，无需改动")


if __name__ == '__main__':
    main()
