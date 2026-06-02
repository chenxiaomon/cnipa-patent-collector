#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
无需 git 的代码更新工具

用法：
  python fetch_update.py          # 从 GitHub 下载最新代码
  python fetch_update.py --check  # 仅检查网络是否可用，不下载

适用场景：
  git 未安装或未加入 PATH 时，用本脚本替代 upgrade.py 更新代码文件。
  注意：sync.py push/pull 仍需要 git，请参考安装提示。
"""

import os
import sys
import urllib.request
import urllib.error

if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

_REPO   = "chenxiaomon/cnipa-patent-collector"
_BRANCH = "main"
_RAW    = f"https://raw.githubusercontent.com/{_REPO}/{_BRANCH}"

# 需要同步的代码文件（不含数据文件）
_FILES = [
    "fetch_update.py",
    "upgrade.py",
    "sync.py",
    "sync_from_jsonl.py",
    "settings.py",
    "db_manager.py",
    "detection_logger.py",
    "cache_utils.py",
    "main_automation.py",
    "collect_fwxx.py",
    "web_dashboard.py",
    "update_by_strategy.py",
    "browser_utils.py",
    "browser_service.py",
    "input_service.py",
    "coordinate_service.py",
    "patent_mitm_scraper.py",
    "start_mitm_proxy.py",
    "start_mitm_public_search.py",
    "retry_failed.py",
    "import_from_cache.py",
    "import_public_search.py",
    "export_public_search.py",
    "merge_detection_logs.py",
    "validate_results.py",
    "analyze_collection_status.py",
    "auto_paginate.py",
    "mitm_addon_public_search.py",
]

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def _download(filename: str, timeout: int = 30) -> bool:
    url = f"{_RAW}/{filename}"
    local = os.path.join(_BASE_DIR, filename)
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            content = resp.read()
        tmp = local + '.tmp'
        with open(tmp, 'wb') as f:
            f.write(content)
        os.replace(tmp, local)
        print(f"  [✓] {filename}")
        return True
    except urllib.error.HTTPError as e:
        if e.code == 404:
            # 文件在远端不存在（可能是旧机器有而新版移除了），跳过
            print(f"  [-] {filename}（远端不存在，跳过）")
        else:
            print(f"  [✗] {filename}：HTTP {e.code}")
        return False
    except urllib.error.URLError as e:
        print(f"  [✗] {filename}：网络错误 {e.reason}")
        return False
    except OSError as e:
        print(f"  [✗] {filename}：写入失败 {e}")
        return False


def _check_network() -> bool:
    try:
        urllib.request.urlopen(f"{_RAW}/settings.py", timeout=10)
        return True
    except Exception:
        return False


def cmd_check() -> None:
    print("检查网络连通性…")
    if _check_network():
        print(f"[✓] 可访问 GitHub（{_RAW}）")
    else:
        print("[✗] 无法访问 GitHub，请检查网络或代理设置")
        sys.exit(1)


def cmd_update() -> None:
    print("=" * 60)
    print("⬇  从 GitHub 下载最新代码")
    print("=" * 60)

    if not _check_network():
        print("[✗] 无法访问 GitHub，请检查网络或代理设置")
        sys.exit(1)

    success, skipped, failed = 0, 0, 0
    for f in _FILES:
        ok = _download(f)
        if ok:
            success += 1
        else:
            # 404 跳过不算失败
            failed += 1

    print()
    print(f"[完成] 成功 {success} 个，失败/跳过 {failed} 个")
    print()

    if failed == 0:
        print("[✓] 代码已是最新版本")
    else:
        print("[!] 部分文件下载失败，请检查网络后重试")

    print()
    print("─" * 60)
    print("⚠  数据同步（sync.py push/pull）仍需要 git")
    print("   如果尚未安装 git，请下载安装：")
    print("   https://git-scm.com/download/win")
    print("   安装时勾选「Add Git to PATH」，安装后重新打开终端")
    print("─" * 60)


if __name__ == '__main__':
    if '--check' in sys.argv:
        cmd_check()
    else:
        cmd_update()
