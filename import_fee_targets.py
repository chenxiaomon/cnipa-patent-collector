#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
费用采集数据集导入脚本

功能：从 CSV 或 Excel 名单读取申请号列，整表替换 patents.db 的 fee_targets 表。
     费用采集只采数据集内的专利（见 docs/decision-log.md D011），
     重新导入即替换整个数据集。

文件格式要求：
  - 支持 .csv / .xlsx（旧式 .xls 请先另存为 .xlsx）
  - 必须包含申请号列（列名：申请号 / application_no / app_no / zhuanlisqh / 专利申请号）
  - 编码自动识别（UTF-8 / GBK）

使用方式：
  python import_fee_targets.py 名单.csv
  python import_fee_targets.py 名单.xlsx
  python import_fee_targets.py 名单.csv --dry   # 预览模式，不写入
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from cache_utils import is_supported_cn_application_no, normalize_app_no
from db_manager import PatentsDB
from import_agency_csv import _APP_NO_COLS, _find_col, parse_file
from settings import PATENTS_DB_FILE


def import_fee_targets(source: Path, dry_run: bool = False) -> dict:
    """读取名单文件并整表替换费用采集数据集。

    Returns:
        {"imported": 本次有效申请号数, "previous": 替换前数据集大小,
         "invalid": 无效行数, "duplicates": 文件内重复数,
         "unregistered": 未建档数（patents 无行，先跑主采集才会进入待采）}
    """
    headers, rows = parse_file(source)
    col_app = _find_col(headers, _APP_NO_COLS)
    if not col_app:
        # 无表头名单：用户常给纯申请号列表，首行本身就是申请号。
        # 识别出哪一列的"表头"是合法申请号，把它当数据列，首行值也计入。
        for header in headers:
            raw_header = str(header or '').strip()
            if is_supported_cn_application_no(raw_header):
                col_app = header
                rows = [{col_app: header}] + rows
                break
    if not col_app:
        raise ValueError(
            f"未找到申请号列，当前列名：{headers}\n"
            f"支持列名：{sorted(_APP_NO_COLS)}，或直接提供无表头的纯申请号名单"
        )

    app_nos: list[str] = []
    seen: set[str] = set()
    invalid_count = 0
    duplicate_count = 0
    for row in rows:
        raw_app_no = str(row.get(col_app) or '').strip()
        normalized = normalize_app_no(raw_app_no)
        # normalize_app_no 对非申请号文本原样返回，需再按申请号格式校验
        if not is_supported_cn_application_no(raw_app_no):
            invalid_count += 1
            continue
        if normalized in seen:
            duplicate_count += 1
            continue
        seen.add(normalized)
        app_nos.append(normalized)

    db = PatentsDB(PATENTS_DB_FILE)
    registered = db.get_all_app_nos()
    unregistered = [app_no for app_no in app_nos if app_no not in registered]

    if dry_run:
        previous_count = db.fee_dataset_progress()['total']
    else:
        previous_count = db.replace_fee_targets(app_nos)['previous_count']

    return {
        'imported': len(app_nos),
        'previous': previous_count,
        'invalid': invalid_count,
        'duplicates': duplicate_count,
        'unregistered': len(unregistered),
        'unregistered_sample': unregistered[:10],
    }


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description="费用采集数据集导入（整表替换）")
    parser.add_argument('file', type=str, help='申请号名单文件（.csv / .xlsx）')
    parser.add_argument('--dry', action='store_true', help='预览模式，不写入数据库')
    args = parser.parse_args()

    source = Path(args.file)
    if not source.exists():
        print(f"[!] 文件不存在: {source}", file=sys.stderr)
        return 1

    try:
        stats = import_fee_targets(source, dry_run=args.dry)
    except ValueError as error:
        print(f"[!] {error}", file=sys.stderr)
        return 1

    mode_label = "预览（未写入）" if args.dry else "导入完成"
    print(f"\n{'=' * 60}")
    print(f"📥 费用数据集{mode_label}")
    print(f"{'=' * 60}")
    print(f"✓ 有效申请号: {stats['imported']} 个（替换原数据集 {stats['previous']} 个）")
    if stats['invalid']:
        print(f"⚠️  无效行: {stats['invalid']} 行（申请号缺失或格式不识别）")
    if stats['duplicates']:
        print(f"⚠️  文件内重复: {stats['duplicates']} 个（已去重）")
    if stats['unregistered']:
        print(f"⚠️  未建档: {stats['unregistered']} 个 —— 这些申请号在主库中无记录，")
        print("    需要先跑主采集（main_automation.py）建档后才会进入费用待采队列：")
        for app_no in stats['unregistered_sample']:
            print(f"      - {app_no}")
        if stats['unregistered'] > len(stats['unregistered_sample']):
            print(f"      ... 及其他 {stats['unregistered'] - len(stats['unregistered_sample'])} 个")
    print(f"{'=' * 60}\n")
    return 0


if __name__ == '__main__':
    sys.exit(main())
