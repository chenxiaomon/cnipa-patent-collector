#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
跨机数据同步脚本：从 JSONL 备份导入到本地 patents.db

使用场景：
  另一台机器在本机做 git pull 后，把携带其采集数据的
  data/results/detection_log.jsonl 一并带来，
  运行本脚本即可将对方的数据 upsert 进本地数据库。

  - 重复申请号：以 JSONL 中的数据覆盖本地（upsert），
    时间戳字段保留 JSONL 原值（不覆盖为当前时间）。
  - 仅本地有的申请号：不受影响，原样保留。

使用方式：
  python sync_from_jsonl.py                    # 从默认路径 data/results/detection_log.jsonl 导入
  python sync_from_jsonl.py path/to/other.jsonl # 从指定文件导入
  python sync_from_jsonl.py --dry              # 预览模式，只统计不写入
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from settings import DETECTION_LOG_JSONL_FILE, PATENTS_DB_FILE
from db_manager import PatentsDB
from cache_utils import normalize_app_no
from machine_identity import MachineRoleConfigurationError, confirm_master_merge


def sync_from_jsonl(source: Path, dry_run: bool = False) -> dict:
    """
    将 JSONL 文件中的记录 upsert 进本地 patents.db。

    Returns:
        {"new": int, "updated": int, "skipped": int, "bad_lines": int}
    """
    if not source.exists():
        print(f"[!] 源文件不存在: {source}")
        return {"new": 0, "updated": 0, "skipped": 0, "bad_lines": 0}

    db = PatentsDB(PATENTS_DB_FILE)
    existing = db.get_all_app_nos()

    new_count = 0
    updated_count = 0
    skipped_count = 0
    bad_lines = 0
    to_upsert = []

    print(f"\n[*] 读取: {source}")
    with open(source, encoding='utf-8') as f:
        for lineno, raw in enumerate(f, 1):
            raw = raw.strip()
            if not raw:
                continue
            try:
                record = json.loads(raw)
            except json.JSONDecodeError:
                bad_lines += 1
                continue

            app_no = normalize_app_no(record.get('application_no', ''))
            if not app_no:
                skipped_count += 1
                continue

            record['application_no'] = app_no
            if app_no in existing:
                updated_count += 1
            else:
                new_count += 1

            to_upsert.append(record)

    print(f"[*] 解析完成: {len(to_upsert)} 条有效记录，{bad_lines} 行格式错误，{skipped_count} 条无效申请号")
    print(f"    其中: {new_count} 条新增 / {updated_count} 条覆盖更新")

    if dry_run:
        print("\n[预览模式] 未写入数据库")
        return {"new": new_count, "updated": updated_count, "skipped": skipped_count, "bad_lines": bad_lines}

    confirm_master_merge(db.summarize_record_import(to_upsert))

    if to_upsert:
        inserted = db.upsert_batch(to_upsert)
        print(f"[✓] 已写入: {inserted} 条")
    else:
        print("[!] 无数据可写入")

    return {"new": new_count, "updated": updated_count, "skipped": skipped_count, "bad_lines": bad_lines}


def main() -> None:
    args = sys.argv[1:]
    dry_run = '--dry' in args
    args = [a for a in args if a != '--dry']

    if args:
        source = Path(args[0])
    else:
        source = DETECTION_LOG_JSONL_FILE

    print("\n" + "=" * 70)
    print("🔄 跨机 JSONL 数据同步导入")
    print("=" * 70)
    if dry_run:
        print("[预览模式] 只统计，不写入数据库")
    print(f"源文件: {source}")

    try:
        stats = sync_from_jsonl(source, dry_run=dry_run)
    except MachineRoleConfigurationError as exc:
        print(f"\n[✗] {exc}")
        sys.exit(2)

    print("\n" + "=" * 70)
    print("📊 同步统计")
    print("=" * 70)
    print(f"  ✓ 新增:   {stats['new']} 条")
    print(f"  ↻ 更新:   {stats['updated']} 条（已有记录覆盖）")
    print(f"  ✗ 无效:   {stats['skipped']} 条（申请号为空）")
    print(f"  ⚠ 损坏行: {stats['bad_lines']} 行（JSON 解析失败）")
    print("=" * 70)

    if stats['new'] == 0 and stats['updated'] == 0 and not dry_run:
        print("\n[!] 无新数据写入")
        sys.exit(1)
    else:
        print(f"\n✅ {'预览完成' if dry_run else '导入完成'}")
        sys.exit(0)


if __name__ == '__main__':
    main()
