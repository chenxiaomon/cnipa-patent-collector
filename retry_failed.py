#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
采集失败记录重试工具。

正常流程：
    python retry_failed.py --write-list
    python main_automation.py --update-list data/retry_failed.txt

默认运行只查看失败记录，不修改数据库、不改重试清单。
"""

from __future__ import annotations

import argparse
from pathlib import Path

from cache_utils import normalize_app_no
from db_manager import PatentsDB
from settings import PATENTS_DB_FILE, RETRY_FAILED_FILE

DEFAULT_RETRY_BATCH_FILE = RETRY_FAILED_FILE.with_name('retry_batch_001.txt')


def is_failed_record(patent_record: dict) -> bool:
    status_code = patent_record.get('status_code')
    return (
        status_code != 200
        or bool(patent_record.get('error_message'))
        or (status_code == 200 and not patent_record.get('zhuanlimc'))
    )


def failed_retry_records(db: PatentsDB) -> list[dict]:
    return [record for record in db.get_all_records() if is_failed_record(record)]


def failure_reason(patent_record: dict) -> str:
    return str(
        patent_record.get('error_message')
        or patent_record.get('response_summary')
        or '未知'
    )


def retry_app_nos(failure_records: list[dict]) -> list[str]:
    normalized_app_nos: list[str] = []
    seen_app_nos: set[str] = set()
    for patent_record in failure_records:
        app_no = normalize_app_no(patent_record.get('application_no'))
        if app_no and app_no not in seen_app_nos:
            seen_app_nos.add(app_no)
            normalized_app_nos.append(app_no)
    return normalized_app_nos


def write_app_no_list(path, app_nos: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    retry_text = ''.join(f'{app_no}\n' for app_no in app_nos)
    retry_tmp_path = path.with_suffix(path.suffix + '.tmp')
    retry_tmp_path.write_text(retry_text, encoding='utf-8')
    retry_tmp_path.replace(path)


def write_failed_retry_list(app_nos: list[str]) -> None:
    write_app_no_list(RETRY_FAILED_FILE, app_nos)


def write_retry_batch(app_nos: list[str], batch_size: int, batch_file=None) -> list[str]:
    if batch_size < 1:
        raise ValueError('batch_size 必须大于 0')
    target_file = batch_file or DEFAULT_RETRY_BATCH_FILE
    batch_app_nos = app_nos[:batch_size]
    write_app_no_list(target_file, batch_app_nos)
    return batch_app_nos


def print_failed_records(failure_records: list[dict]) -> None:
    print(f"{'序号':<5} {'申请号':<25} {'状态码':<8} {'原因'}")
    print("-" * 80)
    for idx, patent_record in enumerate(failure_records, 1):
        app_no = patent_record.get('application_no', 'N/A')
        status_code = patent_record.get('status_code', 'N/A')
        reason_text = failure_reason(patent_record)
        print(f"{idx:<5} {app_no:<25} {status_code!s:<8} {reason_text[:40]}")


def reset_failed_records(db: PatentsDB, failure_records: list[dict]) -> int:
    reset_count = 0
    for patent_record in failure_records:
        app_no = patent_record.get('application_no')
        if not app_no:
            continue
        db.update_fields(app_no, {
            'status_code': None,
            'error_message': None,
            'zhuanlimc': None,
            'anjianywzt': None,
        })
        reset_count += 1
    return reset_count


def main() -> None:
    parser = argparse.ArgumentParser(description='查看或生成失败记录重试清单')
    parser.add_argument(
        '--write-list',
        action='store_true',
        help='将当前失败记录写入 data/retry_failed.txt，不修改数据库',
    )
    parser.add_argument(
        '--batch-size',
        type=int,
        default=None,
        metavar='N',
        help='同时生成前 N 条失败重试批次，默认写入 data/retry_batch_001.txt',
    )
    parser.add_argument(
        '--batch-file',
        type=str,
        default=str(DEFAULT_RETRY_BATCH_FILE),
        metavar='FILE',
        help='失败重试批次输出文件',
    )
    parser.add_argument(
        '--reset',
        action='store_true',
        help='清空失败记录的采集结果，让普通全量采集重新处理',
    )
    parser.add_argument(
        '--run',
        action='store_true',
        help='兼容旧用法，等同于 --reset',
    )
    args = parser.parse_args()

    db = PatentsDB(PATENTS_DB_FILE)
    all_records = db.get_all_records()
    failure_records = failed_retry_records(db)
    print(f"📊 总记录数: {len(all_records)}")
    print(f"❌ 失败记录: {len(failure_records)} 条\n")

    if not failure_records:
        write_failed_retry_list([])
        print(f"✅ 没有失败记录，已清空重试清单: {RETRY_FAILED_FILE}")
        return

    print_failed_records(failure_records)
    app_nos = retry_app_nos(failure_records)

    if args.batch_size is not None and args.batch_size < 1:
        parser.error('--batch-size 必须大于 0')

    if args.write_list or args.batch_size is not None:
        write_failed_retry_list(app_nos)
        print(f"\n✅ 已生成失败重试清单: {RETRY_FAILED_FILE}")
        print(f"   申请号数量: {len(app_nos)}")
        print("   说明: 这是数据库历史累计失败数，不代表刚才一批全失败。")
        if args.batch_size is not None:
            batch_file = Path(args.batch_file)
            batch_app_nos = write_retry_batch(app_nos, args.batch_size, batch_file)
            print(f"\n✅ 已生成本次重试批次: {batch_file}")
            print(f"   批次数量: {len(batch_app_nos)} / {len(app_nos)}")
            print("\n下一步运行:")
            print(f"   python main_automation.py --update-list {batch_file}")
            return
        print("\n下一步运行:")
        print(f"   python main_automation.py --update-list {RETRY_FAILED_FILE}")
        return

    if args.reset or args.run:
        reset_count = reset_failed_records(db, failure_records)
        print(f"\n✅ 已重置 {reset_count} 条失败记录")
        print("\n下一步运行:")
        print("   python main_automation.py")
        return

    print(f"\n💡 正常重试流程:")
    print("   1. python retry_failed.py --write-list")
    print(f"   2. python main_automation.py --update-list {RETRY_FAILED_FILE}")


if __name__ == '__main__':
    main()
