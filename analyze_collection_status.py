#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
采集数据分类查看工具（方案 A：短期，基于现有数据结构）

用途：
  - 按时间戳查看最近采集的数据
  - 查看失败的数据详情
  - 查看待重试的申请号
"""

import json
from datetime import datetime, timedelta
from pathlib import Path

from settings import DETECTION_LOG_JSONL_FILE, DATA_DIR

def load_logs():
    """加载 JSONL 日志"""
    records = []
    with open(DETECTION_LOG_JSONL_FILE, 'r') as f:
        for line in f:
            try:
                record = json.loads(line.strip())
                records.append(record)
            except json.JSONDecodeError:
                pass
    return records

def load_retry_list():
    """加载待重试的申请号"""
    try:
        with open(DATA_DIR / 'retry_dynamic.txt', 'r') as f:
            return [line.strip() for line in f if line.strip()]
    except FileNotFoundError:
        return []

def print_recent_data(records, hours=24):
    """【查看】最近 N 小时采集的数据"""
    print(f"\n📅 【最近 {hours} 小时的采集数据】")
    print("=" * 80)

    cutoff = datetime.utcnow() - timedelta(hours=hours)
    recent = []

    for r in records:
        ts_str = r.get('timestamp', '')
        if ts_str:
            try:
                ts = datetime.fromisoformat(ts_str.replace('Z', '+00:00'))
                if ts > cutoff:
                    recent.append(r)
            except:
                pass

    if not recent:
        print(f"❌ 过去 {hours} 小时内没有采集数据")
        return

    recent.sort(key=lambda x: x.get('timestamp', ''))
    print(f"✅ 共 {len(recent)} 条数据\n")

    success = sum(1 for r in recent if r.get('status_code') == 200)
    failed = len(recent) - success

    print(f"  状态分布:")
    print(f"    ✅ 成功: {success} 条")
    print(f"    ❌ 失败: {failed} 条")
    print()

    # 显示前 10 条（最早）和最后 10 条（最新）
    print(f"  【最早的 5 条】:")
    for r in recent[:5]:
        status = "✅" if r.get('status_code') == 200 else "❌"
        app_no = r.get('application_no', '?')
        ts = r.get('timestamp', '?')[:19]  # 只显示日期时间，不显示毫秒
        print(f"    {status} {app_no:15} {ts}")

    if len(recent) > 10:
        print(f"    ... ({len(recent) - 10} 条中间数据) ...\n")

    print(f"  【最新的 5 条】:")
    for r in recent[-5:]:
        status = "✅" if r.get('status_code') == 200 else "❌"
        app_no = r.get('application_no', '?')
        ts = r.get('timestamp', '?')[:19]
        print(f"    {status} {app_no:15} {ts}")

def print_failed_data(records):
    """【查看】所有失败的数据"""
    print(f"\n❌ 【采集失败的数据】")
    print("=" * 80)

    failed = [r for r in records if r.get('status_code') != 200]

    if not failed:
        print("✅ 没有失败数据！采集成功率 100%")
        return

    print(f"共 {len(failed)} 条失败数据:\n")
    for i, r in enumerate(failed, 1):
        print(f"  【{i}】")
        print(f"    申请号: {r.get('application_no')}")
        print(f"    状态码: {r.get('status_code')}")
        print(f"    错误信息: {r.get('error_message', '无')}")
        print(f"    响应概要: {r.get('response_summary', '无')}")
        print(f"    采集时间: {r.get('timestamp', '无')}")
        print()

def print_retry_list(retry_apps):
    """【查看】待重试的申请号"""
    print(f"\n🔄 【待重试的申请号】")
    print("=" * 80)

    if not retry_apps:
        print("✅ 没有待重试的申请号")
        return

    print(f"共 {len(retry_apps)} 条:\n")
    for app_no in retry_apps:
        print(f"  - {app_no}")

def compare_collected_vs_retry(records, retry_apps):
    """对比：已采集数据 vs 待重试数据"""
    print(f"\n📊 【采集进度对比】")
    print("=" * 80)

    collected_nos = set(r.get('application_no') for r in records)
    retry_nos = set(retry_apps)

    # 重试列表中有没有的申请号
    not_collected = retry_nos - collected_nos

    # 已采集但标记为待重试的
    collected_and_retry = retry_nos & collected_nos

    print(f"总采集数: {len(records)}")
    print(f"总待重试数: {len(retry_apps)}")
    print()
    print(f"【分析】:")
    print(f"  待重试的申请号中:")
    print(f"    - 已采集过的: {len(collected_and_retry)} 条 (标记失败，需要重新采集)")
    print(f"    - 从未采集过的: {len(not_collected)} 条 (还未进入采集流程)")

    if collected_and_retry:
        print(f"\n  【已采集但失败的申请号】:")
        for app_no in sorted(collected_and_retry):
            matching = [r for r in records if r.get('application_no') == app_no]
            if matching:
                r = matching[0]
                print(f"    - {app_no} (status_code={r.get('status_code')}, {r.get('timestamp', '?')[:19]})")

    if not_collected:
        print(f"\n  【从未采集过的申请号】:")
        for app_no in sorted(not_collected):
            print(f"    - {app_no}")

def main():
    print("🔍 采集数据分类查看工具（方案 A）")
    print("=" * 80)

    # 加载数据
    records = load_logs()
    retry_apps = load_retry_list()

    print(f"\n📦 数据加载完成:")
    print(f"  - 采集日志: {len(records)} 条记录")
    print(f"  - 待重试: {len(retry_apps)} 条申请号")

    # 显示各种分类
    print_recent_data(records, hours=24)
    print_recent_data(records, hours=72)
    print_failed_data(records)
    print_retry_list(retry_apps)
    compare_collected_vs_retry(records, retry_apps)

    print("\n" + "=" * 80)
    print("✅ 分析完成")
    print("\n【如何使用这个信息】:")
    print("""
1. 区分"新数据":
   - 看 【最近 24 小时的采集数据】的时间戳
   - 高于这个时间戳的就是"新采集的"

2. 找"失败的数据":
   - 【采集失败的数据】 显示所有失败记录
   - 【待重试的申请号】 列出需要重新采集的

3. 跟踪采集进度:
   - 【采集进度对比】 显示哪些申请号已采集、哪些待重试

4. 决定下一步:
   - 如果待重试较多 → 运行 python retry_failed_applications.py
   - 如果没有待重试 → 可以新开采集任务
   - 如果需要更清晰的"批次"概念 → 考虑新分支 feature/enhance-state-tracking
    """)

if __name__ == '__main__':
    main()
