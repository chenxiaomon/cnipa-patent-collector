#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
一次性迁移脚本：detection_log.json → detection_log.jsonl

用法：
    uv run python migrate_to_jsonl.py

迁移完成后：
- detection_log.jsonl  ← 新主文件（每行一条记录）
- detection_log.json   ← 保留为只读备份（可手动删除）
"""

import json
import os
import sys
from pathlib import Path

from settings import DETECTION_LOG_FILE, DETECTION_LOG_JSONL_FILE


def migrate():
    src = Path(DETECTION_LOG_FILE)
    dst = Path(DETECTION_LOG_JSONL_FILE)

    if not src.exists():
        print(f"❌ 找不到源文件: {src}")
        sys.exit(1)

    if dst.exists():
        count = sum(1 for line in open(dst, encoding='utf-8') if line.strip())
        print(f"⚠️  目标文件已存在: {dst}（{count} 条）")
        answer = input("覆盖？[y/N] ").strip().lower()
        if answer != 'y':
            print("已取消")
            sys.exit(0)

    print(f"读取: {src}")
    with open(src, 'r', encoding='utf-8') as f:
        data = json.load(f)

    records = data.get('records', [])
    print(f"共 {len(records)} 条记录")

    tmp = str(dst) + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + '\n')
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, dst)

    # 验证
    written = sum(1 for line in open(dst, encoding='utf-8') if line.strip())
    if written != len(records):
        print(f"❌ 验证失败：写入 {written} 条，预期 {len(records)} 条")
        sys.exit(1)

    print(f"✅ 迁移完成: {dst}（{written} 条）")
    print(f"   源文件保留为备份: {src}")
    print(f"   确认无误后可手动删除: rm {src}")


if __name__ == '__main__':
    migrate()
