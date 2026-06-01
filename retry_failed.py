#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
重试失败记录工具

功能：从 patents.db 中找出采集失败的记录（status_code != 200 或关键字段为空），
     将其从 DB 中删除，让 main_automation.py 的断点续传机制重新处理它们。

用法：
    python retry_failed.py          # 查看失败记录（不修改）
    python retry_failed.py --run    # 移除失败记录，然后可以重跑
"""

import sys
from settings import PATENTS_DB_FILE


def find_failed_records(db):
    """
    失败标准：
    1. status_code != 200（包括 0、None、403 等）
    2. 有 error_message
    3. status_code == 200 但 zhuanlimc 为空（采集不完整）
    """
    records = db.get_all_records()
    failed = []
    for r in records:
        is_failed = (
            r.get('status_code') != 200
            or r.get('error_message')
            or (r.get('status_code') == 200 and not r.get('zhuanlimc'))
        )
        if is_failed:
            failed.append(r)
    return failed


def main():
    run_mode = '--run' in sys.argv

    from db_manager import PatentsDB
    db = PatentsDB(PATENTS_DB_FILE)

    all_records = db.get_all_records()
    print(f"📊 总记录数: {len(all_records)}")

    failed = find_failed_records(db)
    print(f"❌ 失败记录: {len(failed)} 条\n")

    if not failed:
        print("✅ 没有失败记录，无需重试！")
        return

    print(f"{'序号':<5} {'申请号':<25} {'状态码':<8} {'原因'}")
    print("-" * 80)
    for idx, r in enumerate(failed, 1):
        app_no = r.get('application_no', 'N/A')
        status = r.get('status_code', 'N/A')
        reason = r.get('error_message') or r.get('response_summary') or '未知'
        print(f"{idx:<5} {app_no:<25} {status:<8} {str(reason)[:40]}")

    if not run_mode:
        print(f"\n💡 如需重试这 {len(failed)} 条失败记录，请运行:")
        print(f"   python retry_failed.py --run")
        print(f"\n   然后执行:")
        print(f"   USE_MITM_PROXY=true python main_automation.py --test {len(failed)}")
        return

    # 重置失败记录：清空 status_code 和 error_message，
    # main_automation.py 断点续传会视其为"未采集"并重新处理
    print(f"\n🔧 正在重置失败记录...")
    reset = 0
    for r in failed:
        app_no = r.get('application_no')
        if app_no:
            db.update_fields(app_no, {'status_code': None, 'error_message': None,
                                      'zhuanlimc': None, 'anjianywzt': None})
            reset += 1

    print(f"✅ 已重置 {reset} 条失败记录（保留申请号，清空采集结果）")
    print(f"\n🚀 现在可以重跑:")
    print(f"   USE_MITM_PROXY=true python main_automation.py --test {reset}")


if __name__ == '__main__':
    main()
