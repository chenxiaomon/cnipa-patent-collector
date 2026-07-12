#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
数据合并脚本 - 合并旧数据和新数据

用法：
  python merge_detection_logs.py \
    --old "/path/to/old/detection_log.json" \
    --new "/path/to/new/detection_log.json" \
    --output "/path/to/output/detection_log_merged.json"

说明：
  - 新数据优先策略：新数据中有值则使用，否则使用旧数据的值
  - 按 application_no 字段匹配记录
  - 输出合并统计报告
"""

import json
import argparse
from datetime import datetime

from atomic_write import write_json_atomic


def load_json(file_path):
    """加载 JSON 文件"""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f"[!] 加载文件失败: {file_path}")
        print(f"    错误: {e}")
        return None


def save_json(data, file_path):
    """保存 JSON 文件"""
    try:
        write_json_atomic(file_path, data)
        return True
    except Exception as e:
        print(f"[!] 保存文件失败: {file_path}")
        print(f"    错误: {e}")
        return False


def merge_records(old_record, new_record):
    """
    合并两条记录（新优先策略）

    - 新数据中有值 → 使用新数据
    - 新数据为 None/空 → 使用旧数据
    """
    merged = {}

    # 遍历新记录的所有字段
    for key, new_value in new_record.items():
        if new_value is not None and new_value != "":
            # 新数据有值，使用新数据
            merged[key] = new_value
        elif key in old_record:
            # 新数据无值，使用旧数据
            merged[key] = old_record[key]
        else:
            # 旧数据也没有，使用新数据的值（可能是 None）
            merged[key] = new_value

    # 处理只在旧数据中的字段
    for key, old_value in old_record.items():
        if key not in merged:
            merged[key] = old_value

    return merged


def merge_logs(old_file, new_file, output_file):
    """
    合并两个 detection_log.json 文件

    返回：(merged_data, stats)
    """
    print("\n" + "="*70)
    print("📊 数据合并程序")
    print("="*70)

    # 加载数据
    print(f"\n[*] 加载旧数据: {old_file}")
    old_data = load_json(old_file)
    if not old_data:
        return None, None

    print(f"[*] 加载新数据: {new_file}")
    new_data = load_json(new_file)
    if not new_data:
        return None, None

    # 统计信息
    old_records = old_data.get('records', [])
    new_records = new_data.get('records', [])

    print(f"\n[*] 统计信息:")
    print(f"    旧数据: {len(old_records)} 条记录")
    print(f"    新数据: {len(new_records)} 条记录")

    # 建立旧数据索引
    old_by_app_no = {}
    for record in old_records:
        app_no = record.get('application_no')
        if app_no:
            old_by_app_no[app_no] = record

    # 合并逻辑
    print(f"\n[*] 开始合并...")
    merged_records = []
    stats = {
        'total': 0,
        'from_new_only': 0,      # 只在新数据中
        'from_old_only': 0,      # 只在旧数据中
        'merged': 0,             # 两者都有，合并了
        'has_fwxx_in_merged': 0, # 合并后包含发文信息
    }

    # 处理新数据中的所有记录
    for new_record in new_records:
        app_no = new_record.get('application_no')

        if app_no in old_by_app_no:
            # 两个数据集都有，进行合并
            old_record = old_by_app_no[app_no]
            merged = merge_records(old_record, new_record)
            merged_records.append(merged)
            stats['merged'] += 1

            # 统计发文信息
            if merged.get('fwxx_list'):
                stats['has_fwxx_in_merged'] += 1

            # 从索引中删除，后面用来找只在旧数据中的记录
            del old_by_app_no[app_no]
        else:
            # 只在新数据中
            merged_records.append(new_record)
            stats['from_new_only'] += 1

            if new_record.get('fwxx_list'):
                stats['has_fwxx_in_merged'] += 1

    # 处理只在旧数据中的记录
    for app_no, old_record in old_by_app_no.items():
        merged_records.append(old_record)
        stats['from_old_only'] += 1

    stats['total'] = len(merged_records)

    # 构建输出数据
    merged_data = {
        'records': merged_records,
        'merge_info': {
            'merged_at': datetime.now().isoformat(),
            'old_file': old_file,
            'new_file': new_file,
            'stats': stats
        }
    }

    # 输出统计报告
    print(f"\n[✓] 合并完成！")
    print(f"    总记录数: {stats['total']}")
    print(f"    - 只在新数据中: {stats['from_new_only']}")
    print(f"    - 只在旧数据中: {stats['from_old_only']}")
    print(f"    - 两者都有（已合并）: {stats['merged']}")
    print(f"    - 包含发文信息: {stats['has_fwxx_in_merged']}")

    return merged_data, stats


def main():
    parser = argparse.ArgumentParser(
        description="数据合并脚本 - 合并旧数据和新数据"
    )
    parser.add_argument(
        '--old',
        required=True,
        help='旧数据文件路径'
    )
    parser.add_argument(
        '--new',
        required=True,
        help='新数据文件路径'
    )
    parser.add_argument(
        '--output',
        required=True,
        help='输出文件路径'
    )

    args = parser.parse_args()

    # 执行合并
    merged_data, stats = merge_logs(args.old, args.new, args.output)

    if not merged_data:
        print("\n[!] 合并失败")
        return 1

    # 保存结果
    print(f"\n[*] 保存合并结果: {args.output}")
    if save_json(merged_data, args.output):
        print(f"[✓] 保存成功！")
    else:
        print(f"[!] 保存失败！")
        return 1

    # 输出说明
    print("\n" + "="*70)
    print("📝 后续步骤")
    print("="*70)
    print(f"""
1. 验证合并结果是否正确：
   - 检查记录总数是否符合预期
   - 检查发文信息是否正确合并

2. 备份原文件：
   mv {args.new} {args.new}.bak

3. 使用合并结果替换原文件：
   mv {args.output} {args.new}

4. 重新导出 Excel：
   python -c "from detection_logger import DetectionLogger; DetectionLogger().export_to_excel()"
""")

    return 0


if __name__ == "__main__":
    exit(main())
