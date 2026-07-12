#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
CNIPA 公开搜索 MITM 拦截脚本
- 拦截 publicSearch API 响应
- 提取搜索结果记录
- 按申请号去重
- 保存原始响应和记录到文件
"""

import json
import os
import sys
from datetime import datetime
from mitmproxy import http
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from settings import RAW_RESPONSES_DIR, RAW_SEARCHES_DIR
from atomic_write import write_json_atomic


class PublicSearchMITMAddon:
    """公开搜索 MITM 拦截器"""

    def __init__(self):
        self.processed_sns = set()  # 申请号去重集合
        self.record_count = 0
        self.page_count = 0

        # 确保输出目录存在（settings 模块导入时已自动创建，这里保留以防独立运行）
        RAW_RESPONSES_DIR.mkdir(parents=True, exist_ok=True)
        RAW_SEARCHES_DIR.mkdir(parents=True, exist_ok=True)

    def response(self, flow: http.HTTPFlow) -> None:
        """拦截响应的钩子函数"""

        # 只处理 CNIPA 域名下的请求（同时覆盖 cpquery 和 cponline 两个子域）
        if 'cponline.cnipa.gov.cn' not in flow.request.pretty_url:
            return

        # 跳过非 200 响应
        if flow.response.status_code != 200:
            return

        # 只处理 JSON 响应
        content_type = flow.response.headers.get('content-type', '')
        if 'application/json' not in content_type:
            return

        print(f"\n[+] 拦截到 API: {flow.request.pretty_url[:120]}")

        try:
            # 解析响应 JSON
            response_text = flow.response.get_text()
            resp_json = json.loads(response_text)

            # 提取记录列表
            records = self._extract_records(resp_json)
            if not records:
                print("[-] 未找到记录数据")
                return

            print(f"[*] 成功提取 {len(records)} 条数据")

            # 从 URL 提取页码
            page_no = self._extract_page_no(flow.request.pretty_url)
            self.page_count += 1

            # 处理每条记录（去重）
            for record in records:
                zhuanlisqh = record.get('zhuanlisqh', '').upper().strip()
                if zhuanlisqh and zhuanlisqh not in self.processed_sns:
                    self.processed_sns.add(zhuanlisqh)
                    self.record_count += 1

            print(f"[✓] 本页新增 {len(records)} 条数据，累计 {self.record_count} 条")

            # 保存原始响应到 raw_responses/ 目录
            self._save_raw_response(resp_json, page_no)

            # 追加记录到 JSONL 文件（可选）
            self._append_to_jsonl(records)

        except json.JSONDecodeError as e:
            print(f"[!] JSON 解析失败: {e}")
        except Exception as e:
            print(f"[!] 处理响应失败: {e}")

    def _extract_records(self, resp_json: dict) -> list:
        """支持多种 API 响应格式"""
        if not isinstance(resp_json, dict):
            return []

        # 检查 API 错误状态
        if resp_json.get('code') != 200:
            print(f"[-] API 错误: code={resp_json.get('code')}, msg={resp_json.get('msg')}")
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

    def _extract_page_no(self, url: str) -> int:
        """从 URL 中提取页码"""
        try:
            parsed = urlparse(url)
            query_params = parse_qs(parsed.query)

            # 尝试多个可能的页码参数名
            for param_name in ['pageNo', 'page', 'pageNumber', 'currentPage']:
                if param_name in query_params:
                    return int(query_params[param_name][0])

            # 如果没有找到，返回页码计数
            return self.page_count + 1
        except Exception:
            return self.page_count + 1

    def _save_raw_response(self, resp_json: dict, page_no: int) -> None:
        """保存原始响应到文件"""
        try:
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            filename = f"undomestic_{page_no:04d}_{timestamp}.json"
            filepath = RAW_RESPONSES_DIR / filename

            write_json_atomic(filepath, resp_json)

            print(f"[✓] 已保存原始响应: {filename}")

        except Exception as e:
            print(f"[!] 保存响应失败: {e}")

    def _append_to_jsonl(self, records: list) -> None:
        """追加记录到 JSONL 文件（一行一条）"""
        try:
            filepath = RAW_SEARCHES_DIR / 'undomestic_all_records.jsonl'
            with open(filepath, 'a', encoding='utf-8') as f:
                for record in records:
                    f.write(json.dumps(record, ensure_ascii=False) + '\n')

        except Exception as e:
            print(f"[!] 追加 JSONL 失败: {e}")


# 创建全局实例
addon = PublicSearchMITMAddon()


def response(flow: http.HTTPFlow) -> None:
    """mitmproxy 的响应拦截钩子"""
    addon.response(flow)
