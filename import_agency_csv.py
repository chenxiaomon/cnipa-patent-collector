#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
代理机构批量导入脚本

功能：从 CSV 或 Excel 文件中读取（申请号, 代理机构）对，
     将代理机构信息写入本地 patents.db。

适用场景：
  非发文专利无法通过 MITM 自动采集代理机构，
  由用户提供包含申请号和代理机构的名单文件，通过本脚本批量导入。

文件格式要求：
  - 支持 .csv / .xlsx（旧式 .xls 请先另存为 .xlsx）
  - 必须包含申请号列（列名：申请号 / application_no / app_no / zhuanlisqh）
  - 必须包含代理机构列（列名：代理机构 / daili_jg / agency）
  - 可选代理人列（列名：代理人 / daili_r / agent）
  - 编码自动识别（UTF-8 / GBK）

使用方式：
  python import_agency_csv.py agencies.csv
  python import_agency_csv.py agencies.xlsx
  python import_agency_csv.py agencies.csv --dry     # 预览模式，不写入
"""

import csv
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from cache_utils import is_supported_cn_application_no, normalize_app_no
from db_manager import PatentsDB
from settings import PATENTS_DB_FILE

# 申请号列的候选列名（不区分大小写）
_APP_NO_COLS  = {'申请号', 'application_no', 'app_no', 'zhuanlisqh', '专利申请号'}
# 代理机构列的候选列名
_AGENCY_COLS  = {'代理机构', 'daili_jg', 'agency', '代理所', '专利代理机构'}
# 代理人列的候选列名（可选）
_AGENT_COLS   = {'代理人', 'daili_r', 'agent', '第一代理人', 'diyidlrxm'}


def _find_col(headers: list[str], candidates: set[str]) -> str | None:
    """在表头中找到第一个匹配候选集的列名（不区分大小写和空白）。"""
    normalized = {h.strip().lower(): h for h in headers}
    for candidate in candidates:
        if candidate.lower() in normalized:
            return normalized[candidate.lower()]
    return None


def _read_csv(path: Path) -> tuple[list[str], list[dict]]:
    """读取 CSV 文件，自动尝试 UTF-8-sig 和 GBK 编码。"""
    for encoding in ('utf-8-sig', 'utf-8', 'gbk', 'gb2312'):
        try:
            text = path.read_text(encoding=encoding)
            reader = csv.DictReader(io.StringIO(text))
            headers = list(reader.fieldnames or [])
            rows = list(reader)
            return headers, rows
        except (UnicodeDecodeError, Exception):
            continue
    raise ValueError(f"无法解析 CSV 文件（尝试了 UTF-8/GBK）：{path}")


def _read_excel(path: Path) -> tuple[list[str], list[dict]]:
    """读取 XLSX 文件（需要 openpyxl）。"""
    try:
        import openpyxl
        wb = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
        ws = wb.active
        rows_iter = ws.iter_rows(values_only=True)
        headers = [str(h).strip() if h is not None else '' for h in next(rows_iter)]
        rows = []
        for row in rows_iter:
            rows.append({headers[i]: (str(v).strip() if v is not None else '') for i, v in enumerate(row)})
        wb.close()
        return headers, rows
    except ImportError:
        raise ImportError("读取 Excel 需要 openpyxl：pip install openpyxl")


def parse_file(path: Path) -> tuple[list[str], list[dict]]:
    """根据扩展名分派解析方式，返回 (headers, rows)。"""
    suffix = path.suffix.lower()
    if suffix == '.csv':
        return _read_csv(path)
    elif suffix == '.xlsx':
        return _read_excel(path)
    elif suffix == '.xls':
        raise ValueError("旧式 .xls 不支持导入；请先另存为 .xlsx 或 .csv")
    else:
        raise ValueError(f"不支持的文件格式：{suffix}（仅支持 .csv / .xlsx）")


def import_agency(source: Path, dry_run: bool = False) -> dict:
    """
    读取文件并将代理机构信息 upsert 进 patents.db。

    Returns:
        {
            "updated": int,
            "skipped_invalid": int,
            "skipped_no_agency": int,
            "skipped_missing": int,
            "bad_rows": int,
        }
    """
    headers, rows = parse_file(source)

    col_app    = _find_col(headers, _APP_NO_COLS)
    col_agency = _find_col(headers, _AGENCY_COLS)
    col_agent  = _find_col(headers, _AGENT_COLS)  # 可选

    if not col_app:
        raise ValueError(f"未找到申请号列，当前列名：{headers}\n"
                         f"支持列名：{sorted(_APP_NO_COLS)}")
    if not col_agency:
        raise ValueError(f"未找到代理机构列，当前列名：{headers}\n"
                         f"支持列名：{sorted(_AGENCY_COLS)}")

    print(f"[*] 列映射：申请号={col_app!r}  代理机构={col_agency!r}"
          + (f"  代理人={col_agent!r}" if col_agent else "  代理人=（无）"))
    print(f"[*] 共 {len(rows)} 行")

    db = PatentsDB(PATENTS_DB_FILE)
    existing = db.get_all_app_nos()

    updated = skipped_invalid = skipped_no_agency = skipped_missing = bad_rows = 0

    for i, row in enumerate(rows, 1):
        raw_app = str(row.get(col_app, '') or '').strip()
        agency  = str(row.get(col_agency, '') or '').strip()
        agent   = str(row.get(col_agent, '') or '').strip() if col_agent else ''

        if not raw_app:
            bad_rows += 1
            continue

        if not is_supported_cn_application_no(raw_app):
            print(f"  [{i}] 申请号格式无效，跳过：{raw_app!r}")
            skipped_invalid += 1
            continue
        app_no = normalize_app_no(raw_app)

        if not agency:
            skipped_no_agency += 1
            continue

        if app_no not in existing:
            print(f"  [{i}] DB 中不存在该申请号，跳过：{app_no}")
            skipped_missing += 1
            continue

        if not dry_run:
            fields = {'daili_jg': agency}
            if agent:
                fields['daili_r'] = agent
            db.update_fields(app_no, fields)

        updated += 1
        if updated <= 5 or updated % 500 == 0:
            print(f"  [{i}] {'[预览] ' if dry_run else ''}更新：{app_no} → {agency}"
                  + (f" / {agent}" if agent else ""))

    return {
        'updated': updated,
        'skipped_invalid': skipped_invalid,
        'skipped_no_agency': skipped_no_agency,
        'skipped_missing': skipped_missing,
        'bad_rows': bad_rows,
    }


def main() -> None:
    args = sys.argv[1:]
    dry_run = '--dry' in args
    args = [a for a in args if a != '--dry']

    if not args:
        print(__doc__)
        sys.exit(1)

    source = Path(args[0])
    if not source.exists():
        print(f"[!] 文件不存在：{source}")
        sys.exit(1)

    print("\n" + "=" * 70)
    print("🏢 代理机构批量导入")
    print("=" * 70)
    if dry_run:
        print("[预览模式] 只统计，不写入数据库")
    print(f"源文件：{source}")
    print()

    try:
        stats = import_agency(source, dry_run=dry_run)
    except (ValueError, ImportError) as e:
        print(f"\n[!] {e}")
        sys.exit(1)

    print("\n" + "=" * 70)
    print("📊 导入统计")
    print("=" * 70)
    print(f"  {'[预览] ' if dry_run else ''}更新：{stats['updated']} 条")
    print(f"  跳过（申请号格式无效）：{stats['skipped_invalid']} 条")
    print(f"  跳过（代理机构为空）：{stats['skipped_no_agency']} 条")
    print(f"  跳过（DB 中无此申请号）：{stats['skipped_missing']} 条")
    print(f"  申请号为空：{stats['bad_rows']} 行")
    print("=" * 70)

    if stats['updated'] == 0 and not dry_run:
        print("\n[!] 无数据写入")
        sys.exit(1)
    print(f"\n✅ {'预览完成' if dry_run else '导入完成'}")


if __name__ == '__main__':
    main()
