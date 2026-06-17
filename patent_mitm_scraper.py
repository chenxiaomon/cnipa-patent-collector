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
from mitmproxy import http

# 导入日志模块
import sys
sys.path.insert(0, os.path.dirname(__file__))
from detection_logger import DetectionLogger
from cache_utils import normalize_app_no, read_json_cache, write_json_cache
from settings import FORCE_UPDATE_FLAG, PATENT_CACHE_FILE, PATENT_FWXX_CACHE_FILE, MARKER_FILE

# 将 Path 对象转换为字符串（用于文件操作）
FORCE_UPDATE_FLAG = str(FORCE_UPDATE_FLAG)


class PatentMITMScraper:
    """MITM 爬虫，用于拦截和处理专利 API 响应"""

    # 路由表：URL 关键词 → 处理方法名。匹配顺序为列表顺序，首个命中即分派。
    # 不匹配任何路由的 JSON 响应走默认的 _process_record()（提取专利列表数据）。
    _API_ROUTES = [
        ('/api/view/gn/fwxx', '_process_fwxx_response'),
        ('/api/view/gn/sqxx', '_process_sqxx_response'),
    ]

    def __init__(self):
        self.logger = DetectionLogger()
        self.processed_count = 0
        # mitmproxy 在线程池中并发回调 response()，缓存读-改-写必须加锁
        self._cache_lock = threading.Lock()

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

        写入策略：仅在当前记录 daili_jg 为空时更新，避免覆盖已有数据。
        """
        try:
            response_text = flow.response.content.decode('utf-8', errors='replace')
            data = json.loads(response_text)

            if data.get('code') != 200:
                return

            body = data.get('data', {})

            # 提取申请号
            app_no_raw = (
                body.get('zhuluxmxx', {})
                    .get('zhuluxmxx', {})
                    .get('zhuanlisqh', '')
            )
            app_no = normalize_app_no(app_no_raw)
            if not app_no:
                print('[-] sqxx: 未找到申请号，跳过')
                return

            # 提取代理机构信息
            dailijg_list = body.get('dailijg', {}).get('dailijgList', [])
            if not dailijg_list:
                return

            first = dailijg_list[0]
            if not isinstance(first, dict):
                return

            daili_jg = first.get('dailijgdm') or None
            daili_r  = first.get('diyidlrxm') or None

            if not daili_jg:
                return

            # 写入 DB：只更新代理字段，不触碰其他列
            self.logger._db.update_fields(app_no, {
                'daili_jg': daili_jg,
                'daili_r':  daili_r,
            })
            print(f'[✓] 代理机构已更新: {app_no} → {daili_jg} / {daili_r}')

        except json.JSONDecodeError as e:
            print(f'[!] sqxx JSON 解析失败: {e}')
        except Exception as e:
            print(f'[!] 处理 sqxx 响应失败: {e}')

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

            # 提取发文列表
            fwxx_list = data.get('data', {}).get('tongzhishufw', {}).get('tongzhishufwList', [])
            if not fwxx_list:
                print(f"[-] 未找到发文列表数据")
                return

            print(f"[*] 成功提取 {len(fwxx_list)} 条发文数据")

            # 筛选驳回决定
            bhsj_data = None
            for item in fwxx_list:
                if item.get('tongzhismc') == '驳回决定':
                    bhsj_data = item
                    print(f"[*] 找到驳回决定: {bhsj_data.get('xiazaisj')}")
                    break

            # 从 URL 参数或其他地方提取申请号
            # URL 格式: /api/view/gn/fwxx?hHp4Kgam=...
            # 需要从浏览器上下文获取申请号，暂时使用占位符
            application_no = self._extract_app_no_from_fwxx(flow, bhsj_data)

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

    def _extract_app_no_from_fwxx(self, flow: http.HTTPFlow, bhsj_data: dict) -> str:
        """
        从发文信息 API 响应中提取申请号

        ⚠️  优先级策略：
        1. 从标记文件读取（collect_fwxx.py 设置）- Phase 2 专用
        2. 从 Request Headers 中的 Referer 提取
        3. 从最近修改的 patent_cache.json 推断

        Args:
            flow: HTTP 流对象
            bhsj_data: 驳回决定数据（可能包含收件人等线索）

        Returns:
            申请号 或 None
        """
        try:
            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            # 方案 0（最优先）：从标记文件读取
            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            # collect_fwxx.py 在点击"发文信息"前会标记申请号
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
                            print(f"[-] 标记文件已过期（{age.total_seconds():.1f}s 前写入），跳过此发文响应")
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


def response(flow: http.HTTPFlow) -> None:
    """mitmproxy 的响应拦截钩子"""
    scraper.response(flow)
