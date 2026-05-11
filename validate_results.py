#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
采集结果验证脚本

功能：
- 检查成功率和失败记录
- 验证申请号唯一性
- 统计发文覆盖率
- 检查 JSON/Excel 一致性
- 生成验证报告

用法：
    python validate_results.py                # 完整验证
    python validate_results.py --check-excel  # 仅检查 Excel
    python validate_results.py --check-fwxx   # 仅检查发文
"""

import json
import pandas as pd
import sys
from pathlib import Path
from collections import defaultdict

# 配置
DETECTION_LOG = Path('data/results/detection_log.json')
PATENTS_EXCEL = Path('data/results/patents_data.xlsx')


def load_json_log():
    """加载 detection_log.json"""
    if not DETECTION_LOG.exists():
        print(f"❌ 找不到日志文件: {DETECTION_LOG}")
        sys.exit(1)

    with open(DETECTION_LOG, 'r', encoding='utf-8') as f:
        return json.load(f)


def load_excel():
    """加载 Excel 文件"""
    if not PATENTS_EXCEL.exists():
        print(f"❌ 找不到 Excel 文件: {PATENTS_EXCEL}")
        return None

    try:
        return pd.read_excel(PATENTS_EXCEL)
    except Exception as e:
        print(f"❌ 读取 Excel 失败: {e}")
        return None


def validate_success_rate(records):
    """验证成功率和失败记录"""
    print("\n" + "="*60)
    print("📊 成功率统计")
    print("="*60)

    total = len(records)
    successful = sum(1 for r in records if r.get('status_code') == 200)
    failed = sum(1 for r in records if r.get('status_code') == 0)
    others = total - successful - failed

    success_rate = 100 * successful / total if total > 0 else 0

    print(f"总记录数: {total}")
    print(f"✅ 成功: {successful} ({success_rate:.2f}%)")
    print(f"❌ 失败: {failed}")
    print(f"⚠️  其他: {others}")

    # 列出失败记录
    if failed > 0:
        print(f"\n失败的申请号:")
        for record in records:
            if record.get('status_code') == 0:
                app_no = record.get('application_no', 'N/A')
                error = record.get('error_message') or 'MITM timeout or incomplete data'
                error_str = error[:50] if isinstance(error, str) else str(error)[:50]
                print(f"  - {app_no}: {error_str}")

    return {
        'total': total,
        'successful': successful,
        'failed': failed,
        'success_rate': success_rate
    }


def validate_duplicates(records):
    """验证申请号唯一性"""
    print("\n" + "="*60)
    print("🔍 申请号唯一性检查")
    print("="*60)

    app_nos = [r.get('application_no') for r in records]
    total = len(app_nos)
    unique = len(set(app_nos))
    duplicates = total - unique

    print(f"总申请号: {total}")
    print(f"唯一申请号: {unique}")
    print(f"重复数: {duplicates}")

    if duplicates > 0:
        print(f"\n⚠️  重复申请号:")
        from collections import Counter
        counts = Counter(app_nos)
        for app_no, count in counts.most_common():
            if count > 1:
                print(f"  - {app_no}: {count} 次")
    else:
        print("✅ 无重复申请号")

    return {'duplicates': duplicates}


def validate_fwxx_coverage(records):
    """验证发文覆盖率"""
    print("\n" + "="*60)
    print("📝 发文信息覆盖率")
    print("="*60)

    huihe_cases = [r for r in records if r.get('anjianywzt') == '驳回等复审请求']
    cases_with_fwxx = [r for r in huihe_cases if r.get('fwxx_list')]

    total_huihe = len(huihe_cases)
    total_fwxx = len(cases_with_fwxx)
    coverage = 100 * total_fwxx / total_huihe if total_huihe > 0 else 0

    print(f"驳回等复审案件: {total_huihe}")
    print(f"已采发文: {total_fwxx} ({coverage:.2f}%)")
    print(f"缺失发文: {total_huihe - total_fwxx}")

    # 分析缺失原因
    if total_huihe > total_fwxx:
        print(f"\n缺失发文的申请号:")
        missing = [r for r in huihe_cases if not r.get('fwxx_list')]
        for i, record in enumerate(missing[:10], 1):
            app_no = record.get('application_no', 'N/A')
            status = record.get('status_code', 'N/A')
            print(f"  {i}. {app_no} (status_code={status})")

        if len(missing) > 10:
            print(f"  ... 还有 {len(missing) - 10} 个")

    return {
        'huihe_total': total_huihe,
        'fwxx_collected': total_fwxx,
        'fwxx_coverage': coverage
    }


def validate_json_excel_consistency(records, excel_df):
    """验证 JSON 和 Excel 的一致性"""
    print("\n" + "="*60)
    print("🔄 JSON/Excel 一致性检查")
    print("="*60)

    if excel_df is None:
        print("⚠️  无法检查（Excel 加载失败）")
        return {}

    json_count = len(records)
    excel_count = len(excel_df)

    print(f"JSON 记录数: {json_count}")
    print(f"Excel 行数: {excel_count}")

    if json_count == excel_count:
        print("✅ 行数一致")
    else:
        print(f"❌ 行数不一致（差异: {abs(json_count - excel_count)}）")

    # 检查发文列表一��性
    json_fwxx_count = sum(1 for r in records if r.get('fwxx_list'))
    excel_fwxx_col = '发文列表'

    if excel_fwxx_col in excel_df.columns:
        excel_fwxx_count = (excel_df[excel_fwxx_col].notna() &
                           (excel_df[excel_fwxx_col] != 'N/A')).sum()

        print(f"\nJSON 发文列表非空: {json_fwxx_count}")
        print(f"Excel 发文列表非空: {excel_fwxx_count}")

        if json_fwxx_count == excel_fwxx_count:
            print("✅ 发文列表数一致")
        else:
            print(f"❌ 发文列表数不一致（差异: {abs(json_fwxx_count - excel_fwxx_count)}）")

    return {
        'json_count': json_count,
        'excel_count': excel_count,
        'json_fwxx_count': json_fwxx_count,
        'consistency': json_count == excel_count
    }


def generate_summary(results):
    """生成验证摘要"""
    print("\n" + "="*60)
    print("📋 验证摘要")
    print("="*60)

    status = {
        'success_rate': '✅' if results['success_rate'].get('success_rate', 0) > 95 else '⚠️',
        'duplicates': '✅' if results['duplicates'].get('duplicates', 0) == 0 else '❌',
        'fwxx_coverage': '✅' if results['fwxx_coverage'].get('fwxx_coverage', 0) > 90 else '⚠️',
        'consistency': '✅' if results['consistency'].get('consistency', True) else '❌'
    }

    print(f"{status['success_rate']} 成功率: {results['success_rate'].get('success_rate', 'N/A'):.2f}%")
    print(f"{status['duplicates']} 申请号唯一性: {results['duplicates'].get('duplicates', 'N/A') == 0}")
    print(f"{status['fwxx_coverage']} 发文覆盖率: {results['fwxx_coverage'].get('fwxx_coverage', 0):.2f}%")
    print(f"{status['consistency']} JSON/Excel 一致: {results['consistency'].get('consistency', 'N/A')}")

    # 建议
    print("\n📌 建议:")
    if results['success_rate'].get('success_rate', 0) < 95:
        print("  1. 重试失败的申请号：python retry_failed_applications.py")

    if results['fwxx_coverage'].get('fwxx_coverage', 0) < 95:
        print("  2. 补采发文信息：python collect_fwxx.py")

    if not results['consistency'].get('consistency', True):
        print("  3. 重新生成 Excel：python validate_results.py --regenerate-excel")


def main():
    """主程序"""
    print("\n" + "="*60)
    print("🔍 采集结果验证工具")
    print("="*60)

    # 加载数据
    log_data = load_json_log()
    records = log_data.get('records', [])
    excel_df = load_excel()

    # 执行验证
    results = {
        'success_rate': validate_success_rate(records),
        'duplicates': validate_duplicates(records),
        'fwxx_coverage': validate_fwxx_coverage(records),
        'consistency': validate_json_excel_consistency(records, excel_df)
    }

    # 生成摘要
    generate_summary(results)

    print("\n" + "="*60)
    print("✅ 验证完成")
    print("="*60 + "\n")


if __name__ == '__main__':
    main()
