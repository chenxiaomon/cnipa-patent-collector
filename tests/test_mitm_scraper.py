#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
patent_mitm_scraper.py 的 Mock flow 单元测试

借鉴 selenium-wire (test_handler.py) 的 Mock flow 模式：
用 unittest.mock.Mock 构造 mitmproxy HTTPFlow 对象，
不启动真实代理 / 浏览器 / 数据库，纯粹验证拦截逻辑。
"""

import json
import unittest
from datetime import datetime, timezone
from unittest.mock import Mock, patch


# ── 工具：构造 Mock flow ─────────────────────────────────────────────

def _make_flow(url, status=200, content_type='application/json', body=b'{}'):
    """构造一个最小可用的 Mock mitmproxy HTTPFlow。

    模式来源：selenium-wire test_handler.py
    只设置 patent_mitm_scraper.response() 实际读取的属性。
    """
    flow = Mock()
    flow.request.pretty_url = url
    flow.response.status_code = status
    flow.response.headers = Mock()
    flow.response.headers.get = Mock(return_value=content_type)
    flow.response.content = body
    return flow


def _json_body(obj):
    """把 dict 序列化为 bytes（模拟 CNIPA API 响应 body）。"""
    return json.dumps(obj, ensure_ascii=False).encode('utf-8')


# ── 公共 fixture ─────────────────────────────────────────────────────

# 真实 CNIPA /api/view/gn/sqxx 响应结构（从用户提供的样本简化）
SQXX_RESPONSE = {
    "code": 200,
    "data": {
        "zhuluxmxx": {
            "zhuluxmxx": {
                "zhuanlimc": "一种多通阀装置",
                "zhuanlisqh": "2026104796018",
                "anjianywzt": "等待实审提案",
            }
        },
        "dailijg": {
            "dailijgList": [
                {"diyidlrxm": "陈泽元", "dailijgdm": "浙江侨悦专利代理有限公司"}
            ],
            "isShow": True,
        },
    },
    "msg": "成功",
}

# 真实 CNIPA /api/view/gn/fwxx 响应结构（简化）
FWXX_RESPONSE = {
    "code": 200,
    "data": {
        "tongzhishufw": {
            "tongzhishufwList": [
                {
                    "fawenr": "20251212",
                    "tongzhismc": "驳回决定",
                    "shoujianrxm": "张三(***)",
                    "fawenfs": "电子发文",
                    "xiazaisj": "2025-12-13",
                },
                {
                    "fawenr": "20250926",
                    "tongzhismc": "第N次审查意见通知书",
                    "shoujianrxm": "张三(***)",
                    "fawenfs": "电子发文",
                    "xiazaisj": "2025-09-26",
                },
            ]
        }
    },
    "msg": "成功",
}

# 真实 CNIPA 列表接口响应结构（简化，含一条记录）
LIST_RESPONSE = {
    "code": 200,
    "records": [
        {
            "zhuanlisqh": "2024100659780",
            "zhuanlimc": "一种测试专利",
            "shenqingrxm": "测试公司",
            "zhuanlilx": "1",
            "shenqingr": "2024-01-15",
            "falvzt": "有效",
            "anjianywzt": "专利权维持",
        }
    ],
}


# ══════════════════════════════════════════════════════════════════════
#  Test 1: response() 路由分派
# ══════════════════════════════════════════════════════════════════════

class TestResponseRouting(unittest.TestCase):
    """验证 response() 入口的过滤和路由逻辑。"""

    def setUp(self):
        # patch 掉所有外部依赖，只测路由逻辑
        patcher_logger = patch('patent_mitm_scraper.DetectionLogger')
        self.mock_logger_cls = patcher_logger.start()
        self.addCleanup(patcher_logger.stop)

        from patent_mitm_scraper import PatentMITMScraper
        self.scraper = PatentMITMScraper()

    def test_skips_non_cnipa_url(self):
        flow = _make_flow('https://www.baidu.com/search?q=patent')
        with patch.object(self.scraper, '_process_record') as mock:
            self.scraper.response(flow)
            mock.assert_not_called()

    def test_skips_non_200_status(self):
        flow = _make_flow('https://cponline.cnipa.gov.cn/api/data', status=403)
        with patch.object(self.scraper, '_process_record') as mock:
            self.scraper.response(flow)
            mock.assert_not_called()

    def test_skips_non_json_content(self):
        flow = _make_flow(
            'https://cponline.cnipa.gov.cn/page',
            content_type='text/html',
        )
        with patch.object(self.scraper, '_process_record') as mock:
            self.scraper.response(flow)
            mock.assert_not_called()

    def test_routes_fwxx_to_handler(self):
        flow = _make_flow(
            'https://cponline.cnipa.gov.cn/api/view/gn/fwxx?token=abc',
            body=_json_body(FWXX_RESPONSE),
        )
        with patch.object(self.scraper, '_process_fwxx_response') as mock_fwxx:
            self.scraper.response(flow)
            mock_fwxx.assert_called_once_with(flow)

    def test_routes_sqxx_to_handler(self):
        flow = _make_flow(
            'https://cponline.cnipa.gov.cn/api/view/gn/sqxx?token=abc',
            body=_json_body(SQXX_RESPONSE),
        )
        with patch.object(self.scraper, '_process_sqxx_response') as mock_sqxx:
            self.scraper.response(flow)
            mock_sqxx.assert_called_once_with(flow)

    def test_routes_list_to_process_record(self):
        flow = _make_flow(
            'https://cponline.cnipa.gov.cn/txtSearch',
            body=_json_body(LIST_RESPONSE),
        )
        with patch.object(self.scraper, '_process_record') as mock_rec:
            self.scraper.response(flow)
            mock_rec.assert_called_once()


# ══════════════════════════════════════════════════════════════════════
#  Test 2: _process_record() 缓存写入
# ══════════════════════════════════════════════════════════════════════

class TestProcessRecord(unittest.TestCase):
    """验证专利列表记录的缓存写入逻辑。"""

    def setUp(self):
        patcher_logger = patch('patent_mitm_scraper.DetectionLogger')
        self.mock_logger_cls = patcher_logger.start()
        self.addCleanup(patcher_logger.stop)
        self.mock_logger = self.mock_logger_cls.return_value
        self.mock_logger.get_processed_applications.return_value = set()

        from patent_mitm_scraper import PatentMITMScraper
        self.scraper = PatentMITMScraper()

    @patch('patent_mitm_scraper.write_json_cache')
    @patch('patent_mitm_scraper.read_json_cache', return_value={})
    @patch('os.path.exists', return_value=False)
    def test_normal_record_writes_cache(self, mock_exists, mock_read, mock_write):
        record = LIST_RESPONSE['records'][0]
        self.scraper._process_record(record)

        mock_write.assert_called_once()
        written_data = mock_write.call_args[0][1]
        self.assertIn('2024100659780', written_data)
        self.assertEqual(written_data['2024100659780']['zhuanlimc'], '一种测试专利')

    @patch('patent_mitm_scraper.write_json_cache')
    @patch('patent_mitm_scraper.read_json_cache')
    def test_skips_record_without_app_no(self, mock_read, mock_write):
        record = {'zhuanlimc': '无申请号的记录'}
        self.scraper._process_record(record)
        mock_write.assert_not_called()

    @patch('patent_mitm_scraper.write_json_cache')
    @patch('patent_mitm_scraper.read_json_cache')
    @patch('os.path.exists', return_value=False)
    def test_skips_already_processed(self, mock_exists, mock_read, mock_write):
        self.mock_logger.get_processed_applications.return_value = {'2024100659780'}
        record = LIST_RESPONSE['records'][0]
        self.scraper._process_record(record)
        mock_write.assert_not_called()

    @patch('patent_mitm_scraper.write_json_cache')
    @patch('patent_mitm_scraper.read_json_cache', return_value={})
    @patch('os.path.exists', return_value=True)  # FORCE_UPDATE_FLAG 存在
    def test_force_update_ignores_processed(self, mock_exists, mock_read, mock_write):
        self.mock_logger.get_processed_applications.return_value = {'2024100659780'}
        record = LIST_RESPONSE['records'][0]
        self.scraper._process_record(record)
        mock_write.assert_called_once()  # 强制更新模式下不跳过


# ══════════════════════════════════════════════════════════════════════
#  Test 3: _process_sqxx_response() 代理机构提取
# ══════════════════════════════════════════════════════════════════════

class TestProcessSqxx(unittest.TestCase):
    """验证从专利详情接口提取代理机构/代理人的逻辑。"""

    def setUp(self):
        patcher_logger = patch('patent_mitm_scraper.DetectionLogger')
        self.mock_logger_cls = patcher_logger.start()
        self.addCleanup(patcher_logger.stop)
        self.mock_db = self.mock_logger_cls.return_value._db

        from patent_mitm_scraper import PatentMITMScraper
        self.scraper = PatentMITMScraper()

    def test_normal_agency_extraction(self):
        flow = _make_flow(
            'https://cponline.cnipa.gov.cn/api/view/gn/sqxx?token=abc',
            body=_json_body(SQXX_RESPONSE),
        )
        self.scraper._process_sqxx_response(flow)

        self.mock_db.update_fields.assert_called_once_with(
            '2026104796018',
            {'daili_jg': '浙江侨悦专利代理有限公司', 'daili_r': '陈泽元'},
        )

    def test_skips_empty_dailijg_list(self):
        resp = {**SQXX_RESPONSE, "data": {
            **SQXX_RESPONSE["data"],
            "dailijg": {"dailijgList": [], "isShow": False},
        }}
        flow = _make_flow(
            'https://cponline.cnipa.gov.cn/api/view/gn/sqxx?token=abc',
            body=_json_body(resp),
        )
        self.scraper._process_sqxx_response(flow)
        self.mock_db.update_fields.assert_not_called()

    def test_skips_non_dict_first_item(self):
        resp = {**SQXX_RESPONSE, "data": {
            **SQXX_RESPONSE["data"],
            "dailijg": {"dailijgList": ["not-a-dict"], "isShow": True},
        }}
        flow = _make_flow(
            'https://cponline.cnipa.gov.cn/api/view/gn/sqxx?token=abc',
            body=_json_body(resp),
        )
        self.scraper._process_sqxx_response(flow)
        self.mock_db.update_fields.assert_not_called()

    def test_skips_missing_app_no(self):
        resp = {"code": 200, "data": {
            "zhuluxmxx": {"zhuluxmxx": {}},
            "dailijg": {"dailijgList": [{"dailijgdm": "某机构", "diyidlrxm": "某人"}]},
        }}
        flow = _make_flow(
            'https://cponline.cnipa.gov.cn/api/view/gn/sqxx?token=abc',
            body=_json_body(resp),
        )
        self.scraper._process_sqxx_response(flow)
        self.mock_db.update_fields.assert_not_called()

    def test_skips_api_error_code(self):
        resp = {"code": 500, "msg": "服务器错误"}
        flow = _make_flow(
            'https://cponline.cnipa.gov.cn/api/view/gn/sqxx?token=abc',
            body=_json_body(resp),
        )
        self.scraper._process_sqxx_response(flow)
        self.mock_db.update_fields.assert_not_called()


# ══════════════════════════════════════════════════════════════════════
#  Test 4: _process_fwxx_response() 发文信息提取
# ══════════════════════════════════════════════════════════════════════

class TestProcessFwxx(unittest.TestCase):
    """验证从发文信息接口提取发文列表的逻辑。"""

    def setUp(self):
        patcher_logger = patch('patent_mitm_scraper.DetectionLogger')
        self.mock_logger_cls = patcher_logger.start()
        self.addCleanup(patcher_logger.stop)

        from patent_mitm_scraper import PatentMITMScraper
        self.scraper = PatentMITMScraper()

    @patch('patent_mitm_scraper.write_json_cache')
    @patch('patent_mitm_scraper.read_json_cache')
    def test_normal_fwxx_with_valid_marker(self, mock_read, mock_write):
        """有效的 marker 文件 → 正常缓存发文数据。"""
        now = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
        mock_read.side_effect = [
            # 第一次：_process_fwxx_response 内读 fwxx cache
            {},
            # 但实际 _extract_app_no_from_fwxx 先读 marker
        ]
        # mock_read 被 _process_fwxx_response 调用，然后 _extract_app_no_from_fwxx 也调
        # 重设为按顺序返回：marker → fwxx_cache
        mock_read.side_effect = [
            {'application_no': '2023108272249', 'written_at': now},  # marker
            {},  # fwxx cache
        ]

        flow = _make_flow(
            'https://cponline.cnipa.gov.cn/api/view/gn/fwxx?token=abc',
            body=_json_body(FWXX_RESPONSE),
        )
        self.scraper._process_fwxx_response(flow)

        mock_write.assert_called_once()
        written_data = mock_write.call_args[0][1]
        self.assertIn('2023108272249', written_data)
        fwxx = written_data['2023108272249']
        self.assertEqual(len(fwxx['fwxx_list']), 2)
        self.assertEqual(fwxx['bhsjtzs_xiazaisj'], '2025-12-13')

    @patch('patent_mitm_scraper.write_json_cache')
    @patch('patent_mitm_scraper.read_json_cache')
    def test_skips_expired_marker(self, mock_read, mock_write):
        """marker 超过 5 秒 TTL → 跳过，不写缓存。"""
        mock_read.return_value = {
            'application_no': '2023108272249',
            'written_at': '2020-01-01T00:00:00Z',  # 很久以前
        }
        flow = _make_flow(
            'https://cponline.cnipa.gov.cn/api/view/gn/fwxx?token=abc',
            body=_json_body(FWXX_RESPONSE),
        )
        self.scraper._process_fwxx_response(flow)
        mock_write.assert_not_called()

    @patch('patent_mitm_scraper.write_json_cache')
    @patch('patent_mitm_scraper.read_json_cache', return_value={})
    def test_skips_empty_marker(self, mock_read, mock_write):
        """marker 文件为空 → 跳过。"""
        flow = _make_flow(
            'https://cponline.cnipa.gov.cn/api/view/gn/fwxx?token=abc',
            body=_json_body(FWXX_RESPONSE),
        )
        self.scraper._process_fwxx_response(flow)
        mock_write.assert_not_called()

    def test_skips_api_error(self):
        resp = {"code": 403, "msg": "无权限"}
        flow = _make_flow(
            'https://cponline.cnipa.gov.cn/api/view/gn/fwxx?token=abc',
            body=_json_body(resp),
        )
        with patch('patent_mitm_scraper.write_json_cache') as mock_write:
            self.scraper._process_fwxx_response(flow)
            mock_write.assert_not_called()


if __name__ == '__main__':
    unittest.main()
