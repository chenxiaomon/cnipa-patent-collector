#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
公开查询结果导入脚本

功能：将公开查询 MITM 代理采集的原始数据（data/raw_responses/*.json）
     导入到主数据库 patents.db。

数据来源：start_mitm_public_search.py（端口 8082）拦截的 API 响应，
          由 mitm_addon_public_search.py 写入 data/raw_responses/。

使用方式：
  python import_public_search.py          # 导入所有未处理数据，完成后归档
  python import_public_search.py --dry    # 预览模式，只统计不写入
"""

import json
import sys
import shutil
from datetime import datetime
from pathlib import Path

from cache_utils import normalize_app_no
from db_manager import PatentsDB
from detection_logger import DetectionRecord
from settings import RAW_RESPONSES_DIR, PATENTS_DB_FILE

# 归档目录：导入后把原始 JSON 移到这里，避免下次重复导入
ARCHIVE_DIR = RAW_RESPONSES_DIR.parent / 'raw_responses_imported'


def _extract_records_from_file(path: Path) -> list[dict]:
    """
    从单个 raw_responses JSON 文件中提取专利记录列表。

    支持格式：
      - {code:200, data:{records:[...]}}   ← mitm_addon_public_search 的标准格式
      - {code:200, records:[...]}
      - {code:200, data:[...]}
    """
    try:
        with open(path, encoding='utf-8') as f:
            payload = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"  [!] 读取失败 {path.name}: {e}")
        return []

    if not isinstance(payload, dict):
        return []
    if payload.get('code') != 200:
        print(f"  [-] {path.name}: API 错误 code={payload.get('code')}")
        return []

    data = payload.get('data')
    if isinstance(data, dict):
        records = data.get('records', [])
    elif isinstance(data, list):
        records = data
    else:
        records = payload.get('records', [])

    return records if isinstance(records, list) else []


def _to_detection_record(raw: dict) -> DetectionRecord | None:
    """
    将公开查询 API 单条记录转换为 DetectionRecord。

    公开查询字段与主采集字段完全对应，仅多出 mingcheng / anjianjlwz / attention
    三个字段，patents.db 暂无对应列，忽略即可。
    """
    app_no = normalize_app_no(raw.get('zhuanlisqh', ''))
    if not app_no:
        return None

    return DetectionRecord(
        application_no=app_no,
        status_code=200,
        response_time_ms=None,
        detected=False,
        response_summary='公开查询 MITM 采集（import_public_search）',
        error_message=None,
        famingzlsqgbg=raw.get('famingzlsqgbg'),
        shouquanggh=raw.get('shouquanggh'),
        zhuanlimc=raw.get('zhuanlimc'),
        shenqingrxm=raw.get('shenqingrxm'),
        zhuanlilx=raw.get('zhuanlilx'),
        shenqingr=raw.get('shenqingr'),
        gongkaiggh=raw.get('gongkaiggh'),
        falvzt=raw.get('falvzt'),
        gongkaiggr=raw.get('gongkaiggr'),
        shouquanggr=raw.get('shouquanggr'),
        zhufenlh=raw.get('zhufenlh'),
        anjianbh=raw.get('anjianbh'),
        anjianywzt=raw.get('anjianywzt'),
        fwxx_list=None,
        bhsjtzs_xiazaisj=None,
        bhsjtzs_data=None,
    )


def import_public_search(dry_run: bool = False) -> int:
    """
    扫描 raw_responses/ 下所有 JSON 文件，逐条导入 patents.db。

    已存在的申请号执行 upsert（更新字段），不重复计数。
    导入成功后将源文件归档到 raw_responses_imported/，防止重复导入。

    Returns:
        新增条数（upsert 的都算，已有的不计入）
    """
    print("\n" + "=" * 70)
    print("📥 公开查询结果导入程序")
    print("=" * 70)

    # 收集待处理文件
    source_files = sorted(RAW_RESPONSES_DIR.glob('*.json'))
    if not source_files:
        print(f"\n[!] {RAW_RESPONSES_DIR} 下没有 JSON 文件，无数据可导入")
        print("    请先通过「公开查询」Tab 采集数据后再导入")
        return 0

    print(f"\n[*] 发现 {len(source_files)} 个原始响应文件")

    # 加载已有申请号集合（用于判断新增 vs 更新）
    db = PatentsDB(PATENTS_DB_FILE)
    existing_normalized = {normalize_app_no(a) for a in db.get_processed_app_nos()} - {None}
    print(f"[*] 数据库现有 {len(existing_normalized)} 条记录")

    imported_new = 0   # 真正新增
    updated = 0        # 已有但覆盖更新
    skipped = 0        # 申请号无效
    processed_files = []

    print("\n" + "-" * 70)

    for file_path in source_files:
        records_raw = _extract_records_from_file(file_path)
        if not records_raw:
            print(f"  [-] {file_path.name}: 无有效记录，跳过")
            continue

        file_new = 0
        file_update = 0
        file_rows = []
        for raw in records_raw:
            record = _to_detection_record(raw)
            if record is None:
                skipped += 1
                continue

            is_new = record.application_no not in existing_normalized
            existing_normalized.add(record.application_no)
            if is_new:
                file_new += 1
            else:
                file_update += 1
            if not dry_run:
                file_rows.append(record.to_dict())

        # upsert：新增或覆盖已有记录（公开查询数据可能比原有更新）；单事务批量写
        if file_rows:
            db.upsert_batch(file_rows)

        imported_new += file_new
        updated += file_update
        processed_files.append(file_path)
        tag = "[预览]" if dry_run else "[✓]"
        print(f"  {tag} {file_path.name}: 新增 {file_new} 条，更新 {file_update} 条")

    # 归档原始文件（非预览模式）
    if not dry_run and processed_files:
        ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        for fp in processed_files:
            dest = ARCHIVE_DIR / f"{ts}_{fp.name}"
            shutil.move(str(fp), dest)
        print(f"\n[✓] 已归档 {len(processed_files)} 个文件 → {ARCHIVE_DIR.name}/")

    # 统计
    print("\n" + "=" * 70)
    print("📊 导入统计")
    print("=" * 70)
    if dry_run:
        print("（预览模式，未实际写入）")
    print(f"  新增: {imported_new} 条")
    print(f"  更新: {updated} 条（已有记录覆盖）")
    print(f"  跳过: {skipped} 条（申请号无效）")
    print(f"  处理文件: {len(processed_files)} 个")
    print("=" * 70)

    return imported_new


if __name__ == '__main__':
    dry_run = '--dry' in sys.argv
    if dry_run:
        print("[预览模式] 不会写入数据库")

    count = import_public_search(dry_run=dry_run)
    if count > 0 or dry_run:
        sys.exit(0)
    else:
        sys.exit(1)
