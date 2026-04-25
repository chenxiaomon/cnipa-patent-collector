#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
一次性迁移脚本：将 patent_fwxx_cache.json 回填到 detection_log.json

运行一次即可，之后 collect_fwxx.py 会直接写 detection_log。
"""

import json
import os
import sys

CACHE_FILE = os.path.join(os.path.dirname(__file__), 'data', 'patent_fwxx_cache.json')
LOG_FILE = os.path.join(os.path.dirname(__file__), 'data', 'results', 'detection_log.json')


def main():
    if not os.path.exists(CACHE_FILE):
        print(f"[!] 缓存文件不存在: {CACHE_FILE}")
        sys.exit(1)
    if not os.path.exists(LOG_FILE):
        print(f"[!] 日志文件不存在: {LOG_FILE}")
        sys.exit(1)

    with open(CACHE_FILE, 'r', encoding='utf-8') as f:
        cache = json.load(f)

    with open(LOG_FILE, 'r', encoding='utf-8') as f:
        log = json.load(f)

    index = {r['application_no']: i for i, r in enumerate(log['records'])}

    merged = 0
    skipped_has_data = 0
    skipped_not_in_log = []

    for app_no, fwxx_data in cache.items():
        if app_no not in index:
            skipped_not_in_log.append(app_no)
            continue

        record = log['records'][index[app_no]]
        if record.get('fwxx_list') is not None:
            skipped_has_data += 1
            continue

        record['fwxx_list'] = fwxx_data.get('fwxx_list')
        record['bhsjtzs_xiazaisj'] = fwxx_data.get('bhsjtzs_xiazaisj')
        record['bhsjtzs_data'] = fwxx_data.get('bhsjtzs_data')
        merged += 1

    tmp_file = LOG_FILE + '.tmp'
    with open(tmp_file, 'w', encoding='utf-8') as f:
        json.dump(log, f, ensure_ascii=False, indent=2)
    os.replace(tmp_file, LOG_FILE)

    print(f"\n{'='*50}")
    print(f"回填完成")
    print(f"{'='*50}")
    print(f"  回填成功:       {merged} 条")
    print(f"  已有数据跳过:   {skipped_has_data} 条")
    print(f"  不在 log 中跳过: {len(skipped_not_in_log)} 条")

    if skipped_not_in_log:
        print(f"\n以下申请号在 detection_log 中无对应记录（游离数据，已跳过）:")
        for no in skipped_not_in_log:
            print(f"  {no}")

    total_with_fwxx = sum(1 for r in log['records'] if r.get('fwxx_list') is not None)
    print(f"\ndetection_log 现有 fwxx 记录: {total_with_fwxx} 条")
    print(f"{'='*50}\n")


if __name__ == '__main__':
    main()
