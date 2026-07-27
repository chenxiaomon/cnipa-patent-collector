#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
网络更新检查脚本

统一检查本地代码是否落后于 GitHub 远端，输出结构化 JSON 供 Dashboard 解析。
两种检查方式自动选择：
  - git 模式（有 .git 且 git 可用）：git fetch + 对比 HEAD..origin/main 的提交
  - HTTP 模式（无 git）：拉取远端 VERSION 文件与本地 VERSION 对比

用法：
  python check_update.py           # 检查并打印 JSON（最后一行为 JSON）

输出 JSON 结构：
  {
    "has_update": bool,
    "method": "git" | "http",
    "local_version": str,
    "remote_version": str | None,     # http 模式有值
    "pending_commits": [str, ...],    # git 模式有值
    "error": str | None
  }
"""

import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request

if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from settings import BASE_DIR, VERSION_FILE, GITHUB_BRANCH, raw_file_urls
from code_release_safety import CodeReleaseVerificationError, parse_calendar_version

_BRANCH = GITHUB_BRANCH
_CWD = str(BASE_DIR)


def _read_local_version() -> str:
    """读取本地 VERSION 文件内容，缺失时返回 'unknown'。"""
    try:
        return VERSION_FILE.read_text(encoding='utf-8').strip() or 'unknown'
    except OSError:
        return 'unknown'


def _find_git() -> str | None:
    """返回 git 可执行文件路径；找不到返回 None（不退出，区别于 upgrade.py）。"""
    found = shutil.which('git')
    if found:
        return found
    if sys.platform == 'win32':
        candidates = [
            r'C:\Program Files\Git\cmd\git.exe',
            r'C:\Program Files (x86)\Git\cmd\git.exe',
            os.path.join(os.environ.get('LOCALAPPDATA', ''), r'Programs\Git\cmd\git.exe'),
        ]
        for path in candidates:
            if os.path.isfile(path):
                return path
    return None


def _git_available() -> str | None:
    """git 检查可用的前提：git 可执行 + 当前是 git 仓库。满足返回 git 路径。"""
    git = _find_git()
    if not git:
        return None
    if not (BASE_DIR / '.git').exists():
        return None
    return git


def check_via_git(git: str) -> dict:
    """git 模式：fetch 后对比本地 HEAD 与 origin/<branch> 的待拉取提交。"""
    def run(args: list[str]) -> subprocess.CompletedProcess:
        return subprocess.run(
            [git] + args, text=True, capture_output=True,
            cwd=_CWD, encoding='utf-8', errors='replace',
        )

    fetch = run(['fetch', 'origin'])
    if fetch.returncode != 0:
        return {
            "has_update": False, "method": "git",
            "local_version": _read_local_version(), "remote_version": None,
            "pending_commits": [],
            "error": f"git fetch 失败：{fetch.stderr.strip()}",
        }

    # 解析上游 ref（默认 origin/main）
    upstream = run(['rev-parse', '--abbrev-ref', '--symbolic-full-name', '@{u}']).stdout.strip()
    if not upstream:
        upstream = f'origin/{_BRANCH}'

    log = run(['log', f'HEAD..{upstream}', '--oneline'])
    commits = [line for line in log.stdout.splitlines() if line.strip()]

    return {
        "has_update": bool(commits),
        "method": "git",
        "local_version": _read_local_version(),
        "remote_version": None,
        "pending_commits": commits,
        "error": None,
    }


def check_via_http() -> dict:
    """HTTP 模式：拉取远端 VERSION 与本地对比（无 git 环境的兜底）。

    依次尝试 GitHub 原站和国内镜像，任一成功即返回；全部失败才报错。
    """
    local = _read_local_version()
    remote = None
    last_error = None
    for url in raw_file_urls('VERSION'):
        try:
            req = urllib.request.Request(url, headers={'Cache-Control': 'no-cache'})
            with urllib.request.urlopen(req, timeout=10) as resp:
                remote = resp.read().decode('utf-8').strip()
            if remote:
                break

        except (urllib.error.URLError, OSError) as exc:
            last_error = exc
            continue

    if not remote:
        return {
            "has_update": False, "method": "http",
            "local_version": local, "remote_version": None,
            "pending_commits": [],
            "error": f"所有更新源均无法访问（最后错误：{last_error}）",
        }

    try:
        local_version_order = parse_calendar_version(local)
        remote_version_order = parse_calendar_version(remote)
    except CodeReleaseVerificationError as exc:
        return {
            "has_update": False, "method": "http",
            "local_version": local, "remote_version": remote,
            "pending_commits": [],
            "error": str(exc),
        }

    return {
        "has_update": remote_version_order > local_version_order,
        "method": "http",
        "local_version": local,
        "remote_version": remote,
        "pending_commits": [],
        "error": None,
    }


def check_update() -> dict:
    """统一入口：优先 git，回退 HTTP。"""
    git = _git_available()
    if git:
        return check_via_git(git)
    return check_via_http()


def main() -> None:
    result = check_update()

    # 人类可读摘要（前几行）
    if result["error"]:
        print(f"[!] 检查失败：{result['error']}")
    elif result["has_update"]:
        if result["method"] == "git":
            print(f"[✓] 发现新版本：{len(result['pending_commits'])} 个待拉取提交")
            for line in result["pending_commits"][:10]:
                print(f"    {line}")
        else:
            print(f"[✓] 发现新版本：{result['local_version']} → {result['remote_version']}")
    else:
        print(f"[✓] 已是最新版本（{result['local_version']}）")

    # 最后一行输出 JSON，供 Dashboard 解析
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
