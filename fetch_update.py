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

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from settings import raw_file_urls

if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

# 需要同步的代码文件（不含数据文件）
_FILES = [
    "VERSION",
    "check_update.py",
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
    "import_agency_csv.py",
    "export_public_search.py",
    "merge_detection_logs.py",
    "validate_results.py",
    "analyze_collection_status.py",
    "auto_paginate.py",
    "mitm_addon_public_search.py",
]

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def _download(filename: str, timeout: int = 30) -> bool:
    """依次尝试各更新源下载单个文件，任一成功即写入；全部失败返回 False。

    404 视为"远端不存在"（旧机器有而新版移除），在所有源都 404 后才判定跳过。
    """
    local = os.path.join(_BASE_DIR, filename)
    last_reason = None
    all_404 = True
    for url in raw_file_urls(filename):
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
            last_reason = f"HTTP {e.code}"
            if e.code != 404:
                all_404 = False
            continue
        except urllib.error.URLError as e:
            last_reason = f"网络错误 {e.reason}"
            all_404 = False
            continue
        except OSError as e:
            print(f"  [✗] {filename}：写入失败 {e}")
            return False

    if all_404:
        # 所有源都 404：文件确实不存在于远端，跳过不算失败
        print(f"  [-] {filename}（远端不存在，跳过）")
    else:
        print(f"  [✗] {filename}：所有更新源均失败（{last_reason}）")
    return False


def _check_network() -> bool:
    """任一更新源能取到 settings.py 即视为网络可用。"""
    for url in raw_file_urls('settings.py'):
        try:
            urllib.request.urlopen(url, timeout=10)
            return True
        except Exception:
            continue
    return False


def cmd_check() -> None:
    print("检查网络连通性…")
    if _check_network():
        print("[✓] 至少一个更新源可访问")
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

    success, failed = 0, 0
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
