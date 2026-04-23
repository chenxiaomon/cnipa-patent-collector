#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
重试失败记录工具

功能：从 detection_log.json 中移除失败的记录，
让 main_automation.py 的断点续传机制自动重新处理它们。

用法：
    python retry_failed.py          # 查看失败记录（不修改）
    python retry_failed.py --run    # 移除失败记录，然后可以重跑
"""

import json
import os
import sys
import shutil
from datetime import datetime


LOG_FILE = os.path.join(os.path.dirname(__file__), 'data', 'results', 'detection_log.json')


def load_log():
    with open(LOG_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)


def save_log(data):
    with open(LOG_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def find_failed_records(records):
    """
    识别失败记录的标准：
    1. status_code != 200（包括 0、None、403 等）
    2. 有 error_message
    3. 关键专利字段为空（zhuanlimc 为 None）
    """
    failed = []
    for i, r in enumerate(records):
        is_failed = (
            r.get('status_code') != 200
            or r.get('error_message')
            or (r.get('status_code') == 200 and not r.get('zhuanlimc'))
        )
        if is_failed:
            failed.append((i, r))
    return failed


def main():
    run_mode = '--run' in sys.argv

    if not os.path.exists(LOG_FILE):
        print(f"❌ 日志文件不存在: {LOG_FILE}")
        sys.exit(1)

    data = load_log()
    records = data.get('records', [])
    print(f"📊 总记录数: {len(records)}")

    failed = find_failed_records(records)
    print(f"❌ 失败记录: {len(failed)} 条\n")

    if not failed:
        print("✅ 没有失败记录，无需重试！")
        return

    # 显示失败记录详情
    print(f"{'序号':<5} {'申请号':<25} {'状态码':<8} {'原因'}")
    print("-" * 80)
    for idx, (i, r) in enumerate(failed, 1):
        app_no = r.get('application_no', 'N/A')
        status = r.get('status_code', 'N/A')
        reason = r.get('error_message') or r.get('response_summary') or '未知'
        print(f"{idx:<5} {app_no:<25} {status:<8} {reason[:40]}")

    if not run_mode:
        print(f"\n💡 如需重试这 {len(failed)} 条失败记录，请运行:")
        print(f"   python retry_failed.py --run")
        print(f"\n   然后执行:")
        print(f"   USE_MITM_PROXY=true python main_automation.py --test {len(failed)}")
        return

    # 执行移除
    print(f"\n🔧 正在处理...")

    # 备份
    backup_file = LOG_FILE.replace('.json', f'_backup_{datetime.now().strftime("%Y%m%d_%H%M%S")}.json')
    shutil.copy2(LOG_FILE, backup_file)
    print(f"✅ 已备份: {backup_file}")

    # 移除失败记录
    failed_indices = {i for i, r in failed}
    new_records = [r for i, r in enumerate(records) if i not in failed_indices]

    data['records'] = new_records
    save_log(data)

    removed = len(records) - len(new_records)
    print(f"✅ 已移除 {removed} 条失败记录")
    print(f"📊 剩余记录: {len(new_records)} 条")
    print(f"\n🚀 现在可以重跑:")
    print(f"   USE_MITM_PROXY=true python main_automation.py --test {removed}")


if __name__ == '__main__':
    main()
