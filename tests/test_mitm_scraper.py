#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
patent_mitm_scraper.py 的 Mock flow 单元测试

两层测试：
1. Mock 单元测试（借鉴 selenium-wire test_handler.py）
   — 验证解析逻辑正确，防止回归；不能检测 CNIPA 真实改字段。

2. 金标准（golden master）fixture 测试
   — 用 tests/fixtures/*.json 中存档的真实响应验证关键字段存在。
   — CNIPA 改字段名时，只有这层会报红；需人工替换 fixture 并更新断言。
"""

import json
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

_FIXTURES = Path(__file__).parent / 'fixtures'


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

FYXX_RESPONSE = json.loads(
    (_FIXTURES / 'fyxx_real_response.json').read_text(encoding='utf-8')
)

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

    def test_routes_fyxx_to_handler(self):
        flow = _make_flow(
            'https://cponline.cnipa.gov.cn/api/view/gn/fyxx?token=abc',
            body=_json_body(FYXX_RESPONSE),
        )
        with patch.object(self.scraper, '_process_fee_response') as mock_fee:
            self.scraper.response(flow)
            mock_fee.assert_called_once_with(flow)

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


class TestProcessFeeInformation(unittest.TestCase):
    """验证费用接口的四个栏目能独立绑定到当前申请号。"""

    def setUp(self):
        patcher_logger = patch('patent_mitm_scraper.DetectionLogger')
        patcher_logger.start()
        self.addCleanup(patcher_logger.stop)

        from patent_mitm_scraper import PatentMITMScraper
        self.scraper = PatentMITMScraper()

    @patch('patent_mitm_scraper.write_json_cache')
    @patch('patent_mitm_scraper.read_json_cache')
    def test_caches_all_sections_without_filtering_raw_fields(self, mock_read, mock_write):
        now = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
        mock_read.side_effect = [
            {'application_no': '2026102909420', 'written_at': now},
            {},
        ]
        response_payload = json.loads(json.dumps(FYXX_RESPONSE, ensure_ascii=False))
        payable_record = response_payload['data']['yingjiaofei']['svYingjfList'][0]
        payable_record['cnipaFutureField'] = {'nested': ['value', 3]}
        flow = _make_flow(
            'https://cponline.cnipa.gov.cn/api/view/gn/fyxx?token=abc',
            body=_json_body(response_payload),
        )

        self.scraper._process_fee_response(flow)

        mock_write.assert_called_once()
        cache_entry = mock_write.call_args[0][1]['2026102909420']
        self.assertEqual(
            cache_entry['payable_fee_records'],
            response_payload['data']['yingjiaofei']['svYingjfList'],
        )
        self.assertEqual(
            cache_entry['late_fee_schedule_records'],
            response_payload['data']['zhinajin']['svZnjList'],
        )
        self.assertTrue({
            'yingjiaoffyzlmc',
            'yingjiaoje',
            'jiaofeijzr',
            'yingjiaoffyzt',
        }.issubset(cache_entry['payable_fee_records'][0]))
        self.assertTrue({
            'zhinajjfsj',
            'zhinajdqnfje',
            'zhinajyjznje',
            'zhinajzj',
        }.issubset(cache_entry['late_fee_schedule_records'][0]))
        self.assertEqual(
            cache_entry['paid_fee_records'],
            response_payload['data']['yijiaofei']['svYijfList'],
        )
        self.assertEqual(
            cache_entry['fee_receipt_dispatch_records'],
            response_payload['data']['shoujufawen']['svSjfwList'],
        )
        self.assertEqual(
            cache_entry['payable_fee_records'][0]['cnipaFutureField'],
            {'nested': ['value', 3]},
        )
        snapshot_at = datetime.fromisoformat(
            cache_entry['fee_snapshot_at'].replace('Z', '+00:00')
        )
        self.assertEqual(snapshot_at.utcoffset().total_seconds(), 0)

    @patch('patent_mitm_scraper.write_json_cache')
    @patch('patent_mitm_scraper.read_json_cache')
    def test_empty_tables_are_cached_as_success(self, mock_read, mock_write):
        now = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
        mock_read.side_effect = [
            {'application_no': '2026102909420', 'written_at': now},
            {},
        ]
        empty_response = {
            'code': 200,
            'data': {
                'yingjiaofei': {'isShow': False, 'svYingjfList': []},
                'zhinajin': {'isShow': False, 'svZnjList': []},
                'yijiaofei': {'isShow': False, 'svYijfList': []},
                'shoujufawen': {'isShow': False, 'svSjfwList': []},
            },
        }
        flow = _make_flow(
            'https://cponline.cnipa.gov.cn/api/view/gn/fyxx?token=abc',
            body=_json_body(empty_response),
        )

        self.scraper._process_fee_response(flow)

        cache_entry = mock_write.call_args[0][1]['2026102909420']
        self.assertEqual(cache_entry['payable_fee_records'], [])
        self.assertEqual(cache_entry['late_fee_schedule_records'], [])
        self.assertEqual(cache_entry['paid_fee_records'], [])
        self.assertEqual(cache_entry['fee_receipt_dispatch_records'], [])
        self.assertIn('fee_snapshot_at', cache_entry)

    @patch('patent_mitm_scraper.write_json_cache')
    @patch('patent_mitm_scraper.read_json_cache')
    def test_missing_late_fee_section_preserves_other_sections(self, mock_read, mock_write):
        now = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
        mock_read.side_effect = [
            {'application_no': '2026102909420', 'written_at': now},
            {},
        ]
        incomplete_response = json.loads(json.dumps(FYXX_RESPONSE, ensure_ascii=False))
        del incomplete_response['data']['zhinajin']
        flow = _make_flow(
            'https://cponline.cnipa.gov.cn/api/view/gn/fyxx?token=abc',
            body=_json_body(incomplete_response),
        )

        self.scraper._process_fee_response(flow)

        cache_entry = mock_write.call_args[0][1]['2026102909420']
        self.assertNotIn('late_fee_schedule_records', cache_entry)
        self.assertIn('payable_fee_records', cache_entry)
        self.assertIn('paid_fee_records', cache_entry)
        self.assertIn('fee_receipt_dispatch_records', cache_entry)
        self.assertIn('fee_snapshot_at', cache_entry)

    @patch('patent_mitm_scraper.write_json_cache')
    @patch('patent_mitm_scraper.read_json_cache')
    def test_invalid_sections_only_omit_their_own_fields(self, mock_read, mock_write):
        now = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
        mock_read.side_effect = [
            {'application_no': '2026102909420', 'written_at': now},
            {},
        ]
        partial_response = {
            'code': 200,
            'data': {
                'yingjiaofei': {'svYingjfList': [{'valid': True}, 'not-an-object']},
                'zhinajin': {'isShow': False},
                'yijiaofei': {'svYijfList': []},
                'shoujufawen': None,
            },
        }
        flow = _make_flow(
            'https://cponline.cnipa.gov.cn/api/view/gn/fyxx?token=abc',
            body=_json_body(partial_response),
        )

        self.scraper._process_fee_response(flow)

        cache_entry = mock_write.call_args[0][1]['2026102909420']
        self.assertEqual(cache_entry, {'paid_fee_records': []})

    @patch('patent_mitm_scraper.write_json_cache')
    @patch('patent_mitm_scraper.read_json_cache')
    def test_no_valid_section_does_not_write_fee_cache(self, mock_read, mock_write):
        invalid_response = {
            'code': 200,
            'data': {
                'yingjiaofei': {},
                'zhinajin': None,
                'yijiaofei': {'svYijfList': None},
                'shoujufawen': {'isShow': False},
            },
        }
        flow = _make_flow(
            'https://cponline.cnipa.gov.cn/api/view/gn/fyxx?token=abc',
            body=_json_body(invalid_response),
        )

        self.scraper._process_fee_response(flow)

        mock_read.assert_not_called()
        mock_write.assert_not_called()

    @patch('patent_mitm_scraper.write_json_cache')
    @patch('patent_mitm_scraper.read_json_cache')
    def test_expired_marker_does_not_write_fee_cache(self, mock_read, mock_write):
        mock_read.return_value = {
            'application_no': '2026102909420',
            'written_at': '2020-01-01T00:00:00Z',
        }
        flow = _make_flow(
            'https://cponline.cnipa.gov.cn/api/view/gn/fyxx?token=abc',
            body=_json_body(FYXX_RESPONSE),
        )

        self.scraper._process_fee_response(flow)

        mock_write.assert_not_called()

    def test_api_error_does_not_write_fee_cache(self):
        flow = _make_flow(
            'https://cponline.cnipa.gov.cn/api/view/gn/fyxx?token=abc',
            body=_json_body({'code': 403, 'msg': '无权限'}),
        )
        with patch('patent_mitm_scraper.write_json_cache') as mock_write:
            self.scraper._process_fee_response(flow)
            mock_write.assert_not_called()


# ══════════════════════════════════════════════════════════════════════
#  Test 5: 金标准（golden master）fixture 测试
#  — 使用 tests/fixtures/ 中存档的真实 CNIPA 响应
#  — 防御"CNIPA 真改字段名"场景；mock 单元测试检测不到此类变化
#  — 当测试报红时：替换 fixture 文件 + 更新断言 = 一次 API 变更记录
# ══════════════════════════════════════════════════════════════════════

class TestGoldenMasterSqxx(unittest.TestCase):
    """用真实存档响应验证 _process_sqxx_response 的字段提取契约。

    这是金标准测试，不是 mock 测试——fixture 里的字段名就是契约本身。
    CNIPA 把 dailijgdm 改成 agencyName 时，mock 测试照样绿，
    但这里 assertIn('dailijgdm', item) 会红，提示需要更新采集逻辑。
    """

    @classmethod
    def setUpClass(cls):
        fixture_path = _FIXTURES / 'sqxx_real_response.json'
        if not fixture_path.exists():
            raise unittest.SkipTest(f'fixture 不存在，跳过: {fixture_path}')
        cls.fixture = json.loads(fixture_path.read_text(encoding='utf-8'))

    def test_fixture_has_code_200(self):
        """API 响应格式：顶层 code 字段存在且为 200。"""
        self.assertEqual(self.fixture.get('code'), 200)

    def test_fixture_has_data_key(self):
        """API 响应格式：顶层 data 字段存在。"""
        self.assertIn('data', self.fixture)

    def test_fixture_has_app_no_field(self):
        """申请号字段路径：data.zhuluxmxx.zhuluxmxx.zhuanlisqh 存在。"""
        app_no = (
            self.fixture['data']
            .get('zhuluxmxx', {})
            .get('zhuluxmxx', {})
            .get('zhuanlisqh')
        )
        self.assertIsNotNone(app_no, "申请号字段 zhuanlisqh 缺失——API 可能已更改路径")

    def test_fixture_has_dailijg_structure(self):
        """代理机构字段路径：data.dailijg.dailijgList 存在且非空。"""
        dailijg = self.fixture['data'].get('dailijg', {})
        self.assertIn('dailijgList', dailijg, "dailijg.dailijgList 缺失——API 可能已更改结构")
        lst = dailijg['dailijgList']
        self.assertIsInstance(lst, list)
        self.assertGreater(len(lst), 0, "dailijgList 为空，无法验证字段名")

    def test_fixture_dailijg_item_has_agency_field(self):
        """代理机构名称字段：dailijgList[0].dailijgdm 存在。

        ⚠️  如果 CNIPA 把 dailijgdm 改名，这里会红——
        需同步更新 _process_sqxx_response() 的解析逻辑。
        """
        item = self.fixture['data']['dailijg']['dailijgList'][0]
        self.assertIn(
            'dailijgdm', item,
            f"代理机构字段 dailijgdm 缺失，当前字段：{list(item.keys())}",
        )

    def test_fixture_dailijg_item_has_agent_field(self):
        """代理人姓名字段：dailijgList[0].diyidlrxm 存在。"""
        item = self.fixture['data']['dailijg']['dailijgList'][0]
        self.assertIn(
            'diyidlrxm', item,
            f"代理人字段 diyidlrxm 缺失，当前字段：{list(item.keys())}",
        )

    def test_golden_master_full_extraction(self):
        """端到端：用真实 fixture 跑 _process_sqxx_response，验证 DB 写入正确。"""
        patcher = patch('patent_mitm_scraper.DetectionLogger')
        mock_logger_cls = patcher.start()
        mock_db = mock_logger_cls.return_value._db
        try:
            from patent_mitm_scraper import PatentMITMScraper
            scraper = PatentMITMScraper()

            flow = _make_flow(
                'https://cponline.cnipa.gov.cn/api/view/gn/sqxx?token=abc',
                body=json.dumps(self.fixture, ensure_ascii=False).encode('utf-8'),
            )
            scraper._process_sqxx_response(flow)

            mock_db.update_fields.assert_called_once()
            app_no, fields = mock_db.update_fields.call_args[0]
            self.assertEqual(app_no, '2026104796018')
            self.assertIn('daili_jg', fields)
            self.assertIn('daili_r', fields)
            self.assertEqual(fields['daili_jg'], '浙江侨悦专利代理有限公司')
        finally:
            patcher.stop()


# ══════════════════════════════════════════════════════════════════════
#  Test 6: 防御性失败态测试
#  — 覆盖"线上真正炸的地方"：HTTP 200 但业务错误码、null 数据、HTML 错误页等
# ══════════════════════════════════════════════════════════════════════

class TestDefensiveSqxx(unittest.TestCase):
    """_process_sqxx_response 的防御性失败态覆盖。"""

    def setUp(self):
        patcher = patch('patent_mitm_scraper.DetectionLogger')
        self.mock_logger_cls = patcher.start()
        self.addCleanup(patcher.stop)
        self.mock_db = self.mock_logger_cls.return_value._db

        from patent_mitm_scraper import PatentMITMScraper
        self.scraper = PatentMITMScraper()

    def test_http_200_but_business_error_code(self):
        """HTTP 200 但 body.code != 200（最常见的 API 坑）：不写 DB。"""
        resp = {"code": 401, "msg": "未登录，请重新登录", "data": None}
        flow = _make_flow(
            'https://cponline.cnipa.gov.cn/api/view/gn/sqxx?token=abc',
            body=_json_body(resp),
        )
        self.scraper._process_sqxx_response(flow)
        self.mock_db.update_fields.assert_not_called()

    def test_data_is_null(self):
        """data 字段为 null（会话过期时常见）：不写 DB，不抛异常。"""
        resp = {"code": 200, "data": None, "msg": "成功"}
        flow = _make_flow(
            'https://cponline.cnipa.gov.cn/api/view/gn/sqxx?token=abc',
            body=_json_body(resp),
        )
        # 不应抛 AttributeError / TypeError
        self.scraper._process_sqxx_response(flow)
        self.mock_db.update_fields.assert_not_called()

    def test_data_is_empty_dict(self):
        """data 是空 dict：dailijg 缺失，不写 DB，不抛异常。"""
        resp = {"code": 200, "data": {}, "msg": "成功"}
        flow = _make_flow(
            'https://cponline.cnipa.gov.cn/api/view/gn/sqxx?token=abc',
            body=_json_body(resp),
        )
        self.scraper._process_sqxx_response(flow)
        self.mock_db.update_fields.assert_not_called()

    def test_body_is_html_not_json(self):
        """被限流或会话过期时，服务器返回 HTML 错误页：json.loads 失败，不写 DB，不崩溃。"""
        html_body = b'<html><body><h1>503 Service Unavailable</h1></body></html>'
        flow = _make_flow(
            'https://cponline.cnipa.gov.cn/api/view/gn/sqxx?token=abc',
            body=html_body,
        )
        # 不应抛 JSONDecodeError，应静默处理
        self.scraper._process_sqxx_response(flow)
        self.mock_db.update_fields.assert_not_called()

    def test_dailijgdm_value_is_null(self):
        """字段存在但值为 null（代理机构未填写时）：不写 DB。"""
        resp = {
            "code": 200,
            "data": {
                "zhuluxmxx": {"zhuluxmxx": {"zhuanlisqh": "2026104796018"}},
                "dailijg": {
                    "dailijgList": [{"diyidlrxm": "张三", "dailijgdm": None}],
                    "isShow": True,
                },
            },
        }
        flow = _make_flow(
            'https://cponline.cnipa.gov.cn/api/view/gn/sqxx?token=abc',
            body=_json_body(resp),
        )
        self.scraper._process_sqxx_response(flow)
        # dailijgdm 为 None，_process_sqxx_response 中 `or None` 会保留 None，
        # 然后 `if not daili_jg: return` 跳过写入
        self.mock_db.update_fields.assert_not_called()

    def test_dailijg_key_missing_entirely(self):
        """dailijg 字段整体缺失（API 新增字段前的旧格式）：不写 DB，不抛异常。"""
        resp = {
            "code": 200,
            "data": {
                "zhuluxmxx": {"zhuluxmxx": {"zhuanlisqh": "2026104796018"}},
                # dailijg 完全不存在
            },
        }
        flow = _make_flow(
            'https://cponline.cnipa.gov.cn/api/view/gn/sqxx?token=abc',
            body=_json_body(resp),
        )
        self.scraper._process_sqxx_response(flow)
        self.mock_db.update_fields.assert_not_called()


if __name__ == '__main__':
    unittest.main()
