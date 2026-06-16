#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
公开搜索数据导出脚本
- 读取 data/raw_responses/ 中的 JSON 文件
- 按申请号去重
- 导出到 Excel 和 JSON
"""

import json
import os
import sys

try:
    import pandas as pd
    from openpyxl.styles import Font, PatternFill
except ImportError:
    print("[!] 缺少依赖: pandas 或 openpyxl")
    print("[!] 请运行: pip install pandas openpyxl")
    sys.exit(1)


# 字段映射：API 字段名 → 中文表头
FIELD_MAPPING = {
    'zhuanlisqh': '申请号',
    'zhuanlimc': '发明名称',
    'shenqingrxm': '申请人',
    'zhuanlilx': '专利类型',
    'shenqingr': '申请日',
    'falvzt': '法律状态',
    'zhufenlh': '主分类号',
    'famingzlsqgbg': '发明专利申请公布号',
    'shouquanggh': '授权公告号',
    'gongkaiggh': '公开公告号',
    'gongkaiggr': '公开公告日',
    'shouquanggr': '授权公告日',
    'anjianbh': '案件编号',
    'anjianywzt': '案件业务状态',
    'attention': '关注',
}

# 专利类型映射
PATENT_TYPE_MAP = {
    '1': '发明专利',
    '2': '实用新型',
    '3': '外观设计',
}


def extract_records(resp_json: dict) -> list:
    """支持多种 API 响应格式"""
    if not isinstance(resp_json, dict):
        return []

    # 检查 API 错误
    if resp_json.get('code') != 200:
        return []

    # 格式1: {records: [...]}
    if 'records' in resp_json and isinstance(resp_json['records'], list):
        return resp_json['records']

    # 格式2: {data: {records: [...]}}
    if 'data' in resp_json and isinstance(resp_json['data'], dict):
        data_field = resp_json['data']
        if 'records' in data_field and isinstance(data_field['records'], list):
            return data_field['records']

    # 格式3: {data: [...]}
    if 'data' in resp_json and isinstance(resp_json['data'], list):
        return resp_json['data']

    return []


def convert_patent_type(type_code) -> str:
    """转换专利类型代码"""
    return PATENT_TYPE_MAP.get(str(type_code) if type_code else '', str(type_code))


def load_raw_responses(raw_dir: str) -> list:
    """加载 raw_responses 目录中的所有 JSON 文件"""
    print(f"\n[*] 扫描目录: {raw_dir}")

    if not os.path.exists(raw_dir):
        print(f"[-] 目录不存在: {raw_dir}")
        return []

    records = []
    processed_sns = set()
    file_count = 0
    record_count = 0

    # 按文件名排序（确保页面顺序）
    json_files = sorted([f for f in os.listdir(raw_dir) if f.endswith('.json')])

    if not json_files:
        print("[-] 未找到任何 JSON 文件")
        return []

    print(f"[*] 找到 {len(json_files)} 个 JSON 文件")
    print()

    for filename in json_files:
        filepath = os.path.join(raw_dir, filename)
        file_count += 1

        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                resp_json = json.load(f)

            # 提取记录
            api_records = extract_records(resp_json)
            if not api_records:
                continue

            print(f"[{file_count:3d}] {filename} → {len(api_records)} 条数据", end='')

            # 去重并转换
            new_count = 0
            for api_record in api_records:
                zhuanlisqh = api_record.get('zhuanlisqh', '').upper().strip()
                if not zhuanlisqh or zhuanlisqh in processed_sns:
                    continue

                processed_sns.add(zhuanlisqh)
                new_count += 1

                # 字段转换
                record = {
                    '申请号': zhuanlisqh,
                    '发明名称': api_record.get('zhuanlimc', ''),
                    '申请人': api_record.get('shenqingrxm', ''),
                    '专利类型': convert_patent_type(api_record.get('zhuanlilx')),
                    '申请日': api_record.get('shenqingr', ''),
                    '法律状态': api_record.get('falvzt', ''),
                    '主分类号': api_record.get('zhufenlh', ''),
                    '发明专利申请公布号': api_record.get('famingzlsqgbg', ''),
                    '授权公告号': api_record.get('shouquanggh', ''),
                    '公开公告号': api_record.get('gongkaiggh', ''),
                    '公开公告日': api_record.get('gongkaiggr', ''),
                    '授权公告日': api_record.get('shouquanggr', ''),
                    '案件编号': api_record.get('anjianbh', ''),
                    '案件业务状态': api_record.get('anjianywzt', ''),
                    '关注': api_record.get('attention', ''),
                }
                records.append(record)
                record_count += 1

            print(f" → 新增 {new_count} 条（累计 {record_count} 条）")

        except json.JSONDecodeError:
            print(f"[!] JSON 解析失败: {filename}")
        except Exception as e:
            print(f"[!] 处理失败: {filename} - {e}")

    print()
    print(f"[✓] 已加载 {file_count} 个文件，共 {record_count} 条去重数据")
    return records


def save_to_excel(records: list, output_file: str):
    """保存到 Excel（带样式）"""
    if not records:
        print("[-] 没有数据可以导出")
        return False

    try:
        print(f"\n[*] 正在导出 Excel: {output_file}")

        # 创建 DataFrame
        df = pd.DataFrame(records)

        # 写入 Excel
        with pd.ExcelWriter(output_file, engine='openpyxl') as writer:
            df.to_excel(writer, sheet_name='搜索结果', index=False)

            # 获取工作表（用于冻结首行等格式设置）
            worksheet = writer.sheets['搜索结果']

            # 冻结首行
            worksheet.freeze_panes = 'A2'

            # 设置表头样式
            header_fill = PatternFill(
                start_color='CCCCCC',
                end_color='CCCCCC',
                fill_type='solid'
            )
            header_font = Font(bold=True)

            for cell in worksheet[1]:
                cell.font = header_font
                cell.fill = header_fill

            # 自动列宽
            for column in worksheet.columns:
                max_length = 0
                for cell in column:
                    if cell.value:
                        max_length = max(max_length, len(str(cell.value)))
                column_letter = column[0].column_letter
                worksheet.column_dimensions[column_letter].width = min(max_length + 2, 50)

            # 添加自动筛选
            worksheet.auto_filter.ref = worksheet.dimensions

        print(f"[✓] Excel 文件已保存")
        print(f"   位置: {output_file}")
        print(f"   记录数: {len(records)}")
        return True

    except Exception as e:
        print(f"[!] Excel 导出失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def save_to_json(records: list, output_file: str):
    """保存到 JSON"""
    try:
        print(f"\n[*] 正在导出 JSON: {output_file}")

        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(records, f, ensure_ascii=False, indent=2)

        print(f"[✓] JSON 文件已保存")
        print(f"   位置: {output_file}")
        print(f"   记录数: {len(records)}")
        return True

    except Exception as e:
        print(f"[!] JSON 导出失败: {e}")
        return False


def main():
    print("\n" + "=" * 70)
    print("📊 CNIPA 公开搜索数据导出工具")
    print("=" * 70)

    # 输入和输出路径
    raw_dir = 'data/raw_responses'
    results_dir = 'data/results'
    excel_file = os.path.join(results_dir, 'public_search_results.xlsx')
    json_file = os.path.join(results_dir, 'public_search_results.json')

    # 确保 results 目录存在
    os.makedirs(results_dir, exist_ok=True)

    # 加载数据
    records = load_raw_responses(raw_dir)

    if not records:
        print("[-] 没有找到任何数据")
        return

    # 导出到 Excel
    excel_ok = save_to_excel(records, excel_file)

    # 导出到 JSON
    json_ok = save_to_json(records, json_file)

    # 总结
    print("\n" + "=" * 70)
    if excel_ok and json_ok:
        print("✅ 导出完成！")
        print("=" * 70)
        print(f"\n导出文件:")
        print(f"  1. Excel: {excel_file}")
        print(f"  2. JSON:  {json_file}")
        print(f"\n共导出 {len(records)} 条去重数据")
    else:
        print("⚠️  导出部分失败")
        print("=" * 70)


if __name__ == "__main__":
    main()
