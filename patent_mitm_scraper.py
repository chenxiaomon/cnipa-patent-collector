#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
CNIPA 专利数据 MITM 拦截脚本
- 拦截浏览器的 API 响应
- 提取完整的专利数据
- 与 detection_logger 集成
"""

import json
import os
import threading
from datetime import datetime, timezone, timedelta
from typing import Optional
from mitmproxy import http

# 导入日志模块
import sys
sys.path.insert(0, os.path.dirname(__file__))
from agency_attempt import read_agency_attempt_marker
from detection_logger import DetectionLogger
from cache_utils import normalize_app_no, read_json_cache, write_json_cache
from settings import (
    AGENCY_UNMATCHED_FILE,
    FORCE_UPDATE_FLAG,
    MARKER_FILE,
    PATENT_AGENCY_CACHE_FILE,
    PATENT_CACHE_FILE,
    PATENT_FEE_CACHE_FILE,
    PATENT_FWXX_CACHE_FILE,
)

# 将 Path 对象转换为字符串（用于文件操作）
FORCE_UPDATE_FLAG = str(FORCE_UPDATE_FLAG)

_DETAIL_API_PATTERNS = ('/api/view/gn/fwxx', '/api/view/gn/fyxx')
_DETAIL_TARGET_METADATA_KEY = 'cnipa_detail_target_application_no'
_AGENCY_ATTEMPT_METADATA_KEY = 'cnipa_agency_attempt'
_SQXX_API_PATTERN = '/api/view/gn/sqxx'


class PatentMITMScraper:
    """MITM 爬虫，用于拦截和处理专利 API 响应"""

    # 路由表：URL 关键词 → 处理方法名。匹配顺序为列表顺序，首个命中即分派。
    # 不匹配任何路由的 JSON 响应走默认的 _process_record()（提取专利列表数据）。
    _API_ROUTES = [
        ('/api/view/gn/fyxx', '_process_fee_response'),
        ('/api/view/gn/fwxx', '_process_fwxx_response'),
        ('/api/view/gn/sqxx', '_process_sqxx_response'),
    ]

    def __init__(self):
        self.logger = DetectionLogger()
        self.processed_count = 0
        # mitmproxy 在线程池中并发回调 response()，缓存与备份文件的读-改-写必须加锁
        self._cache_lock = threading.Lock()

    def request(self, flow: http.HTTPFlow) -> None:
        """在详情 API 请求发出时绑定申请号，避免迟到响应串到下一件。"""
        url = flow.request.pretty_url
        if 'cponline.cnipa.gov.cn' not in url:
            return

        if _SQXX_API_PATTERN in url:
            agency_attempt = read_agency_attempt_marker()
            if agency_attempt is not None:
                flow.metadata[_AGENCY_ATTEMPT_METADATA_KEY] = agency_attempt
                print(
                    "[✓] sqxx 请求已绑定代理机构复核尝试: "
                    f"{agency_attempt['application_no']} / {agency_attempt['attempt_id']}"
                )
            return

        if not any(pattern in url for pattern in _DETAIL_API_PATTERNS):
            return
        application_no = self._read_recent_target_app_no()
        if application_no:
            flow.metadata[_DETAIL_TARGET_METADATA_KEY] = application_no
            print(f"[✓] 详情请求已绑定申请号: {application_no}")

    def response(self, flow: http.HTTPFlow) -> None:
        """拦截响应的钩子函数：CNIPA 域名 + 200 + JSON 才处理。"""
        if 'cponline.cnipa.gov.cn' not in flow.request.pretty_url:
            return
        if flow.response.status_code != 200:
            return
        content_type = flow.response.headers.get('content-type', '')
        if 'application/json' not in content_type:
            return

        # 按路由表分派到专用 handler；无命中则走默认列表数据提取
        url = flow.request.pretty_url
        for pattern, method_name in self._API_ROUTES:
            if pattern in url:
                getattr(self, method_name)(flow)
                return

        print(f"\n[+] 拦截到 JSON 响应: {url[:100]}")

        try:
            # 显式 UTF-8 解码，避免 mitmproxy 在响应头无 charset 时回退到 latin-1
            response_text = flow.response.content.decode('utf-8', errors='replace')
            data = json.loads(response_text)

            # 智能检测数据位置
            records = []

            if isinstance(data, dict):
                # 检查 code 字段（如果存在）
                if "code" in data and data.get("code") != 200:
                    print(f"[-] API 错误: code={data.get('code')}, msg={data.get('msg')}")
                    return

                # 尝试从不同位置提取 records
                # 格式 1: {records: [...]}
                if "records" in data and isinstance(data["records"], list):
                    records = data["records"]
                    print(f"[*] 从根目录找到 records（格式1）")

                # 格式 2: {data: {records: [...]}}
                elif "data" in data:
                    data_field = data["data"]
                    if isinstance(data_field, dict) and "records" in data_field:
                        records = data_field["records"]
                        print(f"[*] 从 data.records 找到（格式2）")
                    elif isinstance(data_field, list):
                        records = data_field
                        print(f"[*] 从 data 找到列表（格式3）")

            elif isinstance(data, list):
                # 格式 4: 直接是数组
                records = data
                print(f"[*] 根是数组（格式4）")

            if not records:
                # 不是记录数据，可能是其他 API
                print(f"[-] 未找到记录数据，响应类型: {type(data).__name__}")
                return

            print(f"[*] 成功提取 {len(records)} 条数据")

            # 处理每条记录
            for record in records:
                self._process_record(record)

            print(f"[✓] 已处理 {len(records)} 条，总计 {self.processed_count} 条")

        except json.JSONDecodeError as e:
            print(f"[!] JSON 解析失败: {e}")
        except Exception as e:
            print(f"[!] 处理响应失败: {e}")

    def _process_record(self, api_record: dict) -> None:
        """
        处理单条 API 记录

        改进：将原始 API 数据写入文件系统（而不是内存缓存）
        原因：MITM 和 main_automation.py 运行在不同进程，内存缓存无法跨进程共享
        文件系统是操作系统级别共享，所有进程都能读写

        这样 main_automation.py 的 search_application() 函数就能查询到
        完整的专利数据，并在创建 record 时填充 14 个字段。

        Args:
            api_record: API 返回的原始记录
        """
        try:
            # 提取申请号（作为唯一标识）并规范化
            application_no = normalize_app_no(api_record.get('zhuanlisqh', ''))
            if not application_no:
                print("  [!] 跳过：未找到申请号")
                return

            # 检查是否已处理（强制更新模式下跳过此检查）
            if not os.path.exists(FORCE_UPDATE_FLAG) and \
                    application_no in self.logger.get_processed_applications():
                print(f"  [→] 跳过已处理: {application_no}")
                return

            cache_file = str(PATENT_CACHE_FILE)
            patent_data = {
                'zhuanlisqh': api_record.get('zhuanlisqh'),
                'famingzlsqgbg': api_record.get('famingzlsqgbg'),
                'shouquanggh': api_record.get('shouquanggh'),
                'zhuanlimc': api_record.get('zhuanlimc'),
                'shenqingrxm': api_record.get('shenqingrxm'),
                'zhuanlilx': self._convert_patent_type(api_record.get('zhuanlilx')),
                'shenqingr': api_record.get('shenqingr'),
                'gongkaiggh': api_record.get('gongkaiggh'),
                'falvzt': api_record.get('falvzt'),
                'gongkaiggr': api_record.get('gongkaiggr'),
                'shouquanggr': api_record.get('shouquanggr'),
                'zhufenlh': api_record.get('zhufenlh'),
                'anjianbh': api_record.get('anjianbh'),
                'anjianywzt': api_record.get('anjianywzt'),
            }

            # 加锁：读-改-写必须原子，防止并发回调互相覆盖
            with self._cache_lock:
                cache_data = read_json_cache(cache_file)
                cache_data[application_no] = patent_data
                write_json_cache(cache_file, cache_data)

            self.processed_count += 1
            print(f"  [✓] 已缓存: {application_no} - {api_record.get('zhuanlimc', 'N/A')}")

        except Exception as e:
            print(f"  [!] 处理记录失败: {e}")

    def _process_sqxx_response(self, flow: http.HTTPFlow) -> None:
        """
        处理专利详情 API（/api/view/gn/sqxx）响应，提取代理机构和代理人。

        响应结构：
          data.dailijg.dailijgList[0].dailijgdm  → 代理机构名称
          data.dailijg.dailijgList[0].diyidlrxm  → 第一代理人姓名
          data.zhuluxmxx.zhuluxmxx.zhuanlisqh    → 申请号（用于关联记录）

        写入策略：
          - daili_jg 直接覆盖：官方接口是权威来源，代理所会变更（转所），
            覆盖优先于保留 import_agency_csv.py 导入的旧值。
          - daili_r 仅在本次响应带回 diyidlrxm 时写入，避免把已知代理人抹成 NULL。
          - 官方机构为空时只写确认回执，不清空数据库中的历史代理字段。
          - 未建档申请号转存备份文件，不凭空创建 patents 行（建档只由主采集流程负责）。
        """
        try:
            response_text = flow.response.content.decode('utf-8', errors='replace')
            data = json.loads(response_text)

            if data.get('code') != 200:
                return

            body = data.get('data')
            if not isinstance(body, dict):
                return

            # 提取申请号
            bibliographic_section = body.get('zhuluxmxx')
            if not isinstance(bibliographic_section, dict):
                return
            bibliographic_fields = bibliographic_section.get('zhuluxmxx')
            if not isinstance(bibliographic_fields, dict):
                return
            app_no_raw = (
                bibliographic_fields.get('zhuanlisqh', '')
            )
            app_no = normalize_app_no(app_no_raw)
            if not app_no:
                print('[-] sqxx: 未找到申请号，跳过')
                return

            # 提取代理机构信息
            agency_section = body.get('dailijg')
            if not isinstance(agency_section, dict) or 'dailijgList' not in agency_section:
                return
            dailijg_list = agency_section['dailijgList']
            if not isinstance(dailijg_list, list):
                return

            first = dailijg_list[0] if dailijg_list else {}
            if not isinstance(first, dict):
                return

            if dailijg_list and 'dailijgdm' not in first:
                return
            agency_name = first.get('dailijgdm')
            if agency_name is not None and not isinstance(agency_name, str):
                return
            agent_name = first.get('diyidlrxm')
            daili_jg = agency_name.strip() or None if isinstance(agency_name, str) else None
            daili_r = agent_name.strip() or None if isinstance(agent_name, str) else None

            if daili_jg:
                agency_fields = {'daili_jg': daili_jg}
                if daili_r:
                    agency_fields['daili_r'] = daili_r
                try:
                    persistence_status = self._persist_agency_fields(app_no, agency_fields)
                except Exception as e:
                    persistence_status = 'persistence_error'
                    print(f'[!] 写入代理机构失败: {e}')
            else:
                persistence_status = 'official_empty'
                print(f'[!] {app_no} 官方代理机构为空，保留数据库历史代理字段')

            attempt_id = self._matching_agency_attempt_id(flow, app_no)
            if attempt_id is not None:
                self._cache_agency_acknowledgement(
                    app_no,
                    attempt_id,
                    daili_jg,
                    daili_r,
                    persistence_status,
                )

        except json.JSONDecodeError as e:
            print(f'[!] sqxx JSON 解析失败: {e}')
        except Exception as e:
            print(f'[!] 处理 sqxx 响应失败: {e}')

    def _persist_agency_fields(self, app_no: str, agency_fields: dict) -> str:
        """写入代理字段；申请号未建档时转存备份，不新建 patents 行。

        patents 行只由主采集流程建档（见 db_manager.fee_dataset_pending_app_nos 的说明），
        半截记录会污染各处统计口径，故此处宁可转存也不 upsert。
        """
        if self.logger._db.update_fields(app_no, agency_fields):
            daili_r = agency_fields.get('daili_r')
            print(
                f"[✓] 代理机构已更新: {app_no} → {agency_fields['daili_jg']}"
                + (f' / {daili_r}' if daili_r else '')
            )
            return 'updated'

        self._append_unmatched_agency(app_no, agency_fields)
        print(f'[!] {app_no} 未建档，代理机构转存 {AGENCY_UNMATCHED_FILE}')
        return 'unmatched'

    def _cache_agency_acknowledgement(
        self,
        app_no: str,
        attempt_id: str,
        daili_jg: Optional[str],
        daili_r: Optional[str],
        persistence_status: str,
    ) -> None:
        """按申请号保存最近一次有效官方代理机构回执。"""
        try:
            cache_file = str(PATENT_AGENCY_CACHE_FILE)
            acknowledgement = {
                'attempt_id': attempt_id,
                'captured_at': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
                'daili_jg': daili_jg,
                'daili_r': daili_r,
                'persistence_status': persistence_status,
            }
            with self._cache_lock:
                agency_cache = read_json_cache(cache_file)
                agency_cache[app_no] = acknowledgement
                write_json_cache(cache_file, agency_cache)
        except Exception as e:
            print(f'[!] 写入代理机构确认回执失败: {e}')

    @staticmethod
    def _matching_agency_attempt_id(flow: http.HTTPFlow, app_no: str) -> Optional[str]:
        """Return the bound attempt only while it is still current for this response."""
        bound_attempt = flow.metadata.get(_AGENCY_ATTEMPT_METADATA_KEY)
        if not isinstance(bound_attempt, dict):
            return None

        bound_app_no = normalize_app_no(bound_attempt.get('application_no'))
        attempt_id = str(bound_attempt.get('attempt_id') or '').strip()
        if bound_app_no != app_no or not attempt_id:
            print(
                '[!] sqxx 响应与代理机构复核尝试不匹配，跳过确认回执: '
                f'响应={app_no}, 绑定={bound_app_no}'
            )
            return None

        current_attempt = read_agency_attempt_marker()
        if (
            current_attempt is None
            or current_attempt['application_no'] != app_no
            or current_attempt['attempt_id'] != attempt_id
        ):
            print(f'[!] sqxx 代理机构复核尝试已过期，跳过确认回执: {app_no}')
            return None
        return attempt_id

    def _append_unmatched_agency(self, app_no: str, agency_fields: dict) -> None:
        """将无法匹配到主库的代理字段追加到独立备份（形状同 fee_unmatched.json）。"""
        try:
            backup_file = str(AGENCY_UNMATCHED_FILE)
            unmatched_record = {
                'application_no': app_no,
                'reason': 'not_found_in_db',
                'captured_at': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
                **agency_fields,
            }
            with self._cache_lock:
                unmatched_payload = read_json_cache(backup_file)
                unmatched_payload.setdefault('records', []).append(unmatched_record)
                write_json_cache(backup_file, unmatched_payload)
        except Exception as e:
            # 备份失败不能打断 mitm 响应链路
            print(f'[!] 写入代理机构 unmatched 失败: {e}')

    def _process_fwxx_response(self, flow: http.HTTPFlow) -> None:
        """
        处理发文信息 API 响应

        Args:
            flow: mitmproxy 的 HTTP 流对象
        """
        try:
            response_text = flow.response.content.decode('utf-8', errors='replace')
            data = json.loads(response_text)

            # 检查 API 响应状态
            if data.get("code") != 200:
                print(f"[-] 发文信息 API 错误: code={data.get('code')}, msg={data.get('msg')}")
                return

            response_data = data.get('data')
            fwxx_section = (
                response_data.get('tongzhishufw')
                if isinstance(response_data, dict)
                else None
            )
            if not isinstance(fwxx_section, dict) or 'tongzhishufwList' not in fwxx_section:
                print("[-] 发文信息响应缺少 tongzhishufwList")
                return

            fwxx_list = fwxx_section['tongzhishufwList']
            if not isinstance(fwxx_list, list):
                print("[-] 发文信息 tongzhishufwList 类型错误")
                return

            print(f"[*] 成功提取 {len(fwxx_list)} 条发文数据")

            # 筛选驳回决定
            bhsj_data = None
            for item in fwxx_list:
                if item.get('tongzhismc') == '驳回决定':
                    bhsj_data = item
                    print(f"[*] 找到驳回决定: {bhsj_data.get('xiazaisj')}")
                    break

            application_no = self._read_bound_detail_target(flow)

            if not application_no:
                print(f"[-] 无法提取申请号")
                return

            fwxx_cache_data = {
                'fwxx_list': fwxx_list,
                'bhsjtzs_xiazaisj': bhsj_data.get('xiazaisj') if bhsj_data else None,
                'bhsjtzs_data': bhsj_data
            }

            cache_file = str(PATENT_FWXX_CACHE_FILE)
            with self._cache_lock:
                cache_data = read_json_cache(cache_file)
                cache_data[application_no] = fwxx_cache_data
                write_json_cache(cache_file, cache_data)

            print(f"[✓] 发文信息已缓存: {application_no}")

        except json.JSONDecodeError as e:
            print(f"[!] 发文信息 JSON 解析失败: {e}")
        except Exception as e:
            print(f"[!] 处理发文信息失败: {e}")

    def _process_fee_response(self, flow: http.HTTPFlow) -> None:
        """处理费用信息响应，独立保留四个费用栏目的完整原始记录。"""
        try:
            response_text = flow.response.content.decode('utf-8', errors='replace')
            response_payload = json.loads(response_text)

            if response_payload.get('code') != 200:
                print(
                    f"[-] 费用信息 API 错误: code={response_payload.get('code')}, "
                    f"msg={response_payload.get('msg')}"
                )
                return

            fee_sections = response_payload.get('data')
            if not isinstance(fee_sections, dict):
                print('[-] 费用信息响应缺少 data 对象')
                return

            def extract_section_records(section_name: str, list_name: str):
                if section_name not in fee_sections:
                    print(f'[*] 费用栏目 {section_name} 未返回，已省略')
                    return None
                section = fee_sections.get(section_name)
                if not isinstance(section, dict):
                    print(f'[-] 费用栏目 {section_name} 不是对象，已省略')
                    return None
                if list_name not in section:
                    print(f'[*] 费用栏目 {section_name}.{list_name} 未返回，已省略')
                    return None
                records = section[list_name]
                if not isinstance(records, list):
                    print(f'[-] 费用栏目 {section_name}.{list_name} 不是列表')
                    return None
                if not all(isinstance(record, dict) for record in records):
                    print(f'[-] 费用栏目 {section_name}.{list_name} 包含非对象记录')
                    return None
                return records

            fee_section_specs = (
                ('payable_fee_records', 'yingjiaofei', 'svYingjfList'),
                ('late_fee_schedule_records', 'zhinajin', 'svZnjList'),
                ('paid_fee_records', 'yijiaofei', 'svYijfList'),
                ('fee_receipt_dispatch_records', 'shoujufawen', 'svSjfwList'),
            )
            fee_cache_entry = {}
            for cache_field, section_name, list_name in fee_section_specs:
                records = extract_section_records(section_name, list_name)
                if records is not None:
                    fee_cache_entry[cache_field] = records

            if 'payable_fee_records' in fee_cache_entry:
                fee_cache_entry['fee_snapshot_at'] = (
                    datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
                )

            if not fee_cache_entry:
                print('[-] 费用信息响应没有可缓存的有效栏目')
                return

            application_no = self._read_bound_detail_target(flow)
            if not application_no:
                return

            with self._cache_lock:
                fee_cache = read_json_cache(str(PATENT_FEE_CACHE_FILE))
                fee_cache[application_no] = fee_cache_entry
                write_json_cache(str(PATENT_FEE_CACHE_FILE), fee_cache)

            section_counts = ', '.join(
                f'{cache_field} {len(fee_cache_entry[cache_field])} 条'
                for cache_field, _, _ in fee_section_specs
                if cache_field in fee_cache_entry
            )
            print(
                f"[✓] 费用信息已缓存: {application_no} "
                f"({section_counts})"
            )
        except json.JSONDecodeError as e:
            print(f'[!] 费用信息 JSON 解析失败: {e}')
        except Exception as e:
            print(f'[!] 处理费用信息失败: {e}')

    @staticmethod
    def _read_bound_detail_target(flow: http.HTTPFlow) -> str | None:
        """读取 request() 已绑定到当前 HTTP 流的申请号。"""
        metadata = getattr(flow, 'metadata', None)
        if not isinstance(metadata, dict):
            print('[-] 详情响应缺少请求元数据，跳过')
            return None
        application_no = normalize_app_no(
            metadata.get(_DETAIL_TARGET_METADATA_KEY)
        )
        if not application_no:
            print('[-] 详情响应没有请求时绑定的申请号，跳过')
            return None
        return application_no

    def _read_recent_target_app_no(self) -> str:
        """
        从详情采集标记读取当前申请号。

        标记超过 5 秒即视为陈旧，防止异步响应关联到下一件专利。

        Returns:
            申请号 或 None
        """
        try:
            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            # 方案 0（最优先）：从标记文件读取
            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            # collect_fwxx.py 在点击详情菜单前会标记申请号
            marker = read_json_cache(str(MARKER_FILE))
            app_no = marker.get('application_no')
            if app_no:
                # TTL 检查：标记必须在 5 秒内写入，超时视为上一轮遗留的陈旧标记
                written_at_str = marker.get('written_at')
                if written_at_str:
                    try:
                        written_at = datetime.fromisoformat(
                            written_at_str.replace('Z', '+00:00')
                        )
                        age = datetime.now(timezone.utc) - written_at
                        if age > timedelta(seconds=5):
                            print(f"[-] 标记文件已过期（{age.total_seconds():.1f}s 前写入），跳过此详情响应")
                            return None
                    except Exception:
                        pass  # 解析失败时不拒绝，降级为无 TTL 行为
                print(f"[✓] 从标记文件获取申请号: {app_no}")
                return app_no

            # 方案 1（唯一回退）：标记文件为空，记录告警，不猜测申请号
            # 猜测 patent_cache 最后一个键在并发写入下不可靠，会静默关联错误数据
            print(f"[-] 标记文件为空，无法确定当前申请号，跳过此发文响应")
            return None

        except Exception as e:
            print(f"[!] 提取申请号失败: {e}")
            return None

    @staticmethod
    def _convert_patent_type(type_code: str) -> str:
        """
        转换专利类型代码

        Args:
            type_code: API 返回的类型代码 ("1"/"2"/"3")

        Returns:
            中文类型名称
        """
        type_map = {
            "1": "发明",
            "2": "实用新型",
            "3": "外观设计"
        }
        return type_map.get(str(type_code), type_code)


# 创建全局实例
scraper = PatentMITMScraper()


def request(flow: http.HTTPFlow) -> None:
    """mitmproxy 的请求拦截钩子。"""
    scraper.request(flow)


def response(flow: http.HTTPFlow) -> None:
    """mitmproxy 的响应拦截钩子。"""
    scraper.response(flow)
