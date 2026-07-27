#!/usr/bin/env python
# -*- coding: utf-8 -*-

import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from tests.test_mitm_scraper import _json_body, _make_flow


class TestEmptyFwxxCache(unittest.TestCase):
    @patch("patent_mitm_scraper.write_json_cache")
    @patch("patent_mitm_scraper.read_json_cache")
    @patch("patent_mitm_scraper.DetectionLogger")
    def test_explicit_empty_notice_list_is_cached_as_success(
        self,
        _logger_class,
        read_cache,
        write_cache,
    ):
        from patent_mitm_scraper import PatentMITMScraper

        written_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        read_cache.side_effect = [
            {"application_no": "2026102909420", "written_at": written_at},
            {},
        ]
        flow = _make_flow(
            "https://cponline.cnipa.gov.cn/api/view/gn/fwxx?token=abc",
            body=_json_body({
                "code": 200,
                "data": {
                    "tongzhishufw": {
                        "tongzhishufwList": [],
                    },
                },
            }),
        )

        scraper = PatentMITMScraper()
        scraper.request(flow)
        scraper._process_fwxx_response(flow)

        cached = write_cache.call_args.args[1]["2026102909420"]
        self.assertEqual(cached["fwxx_list"], [])
        self.assertIsNone(cached["bhsjtzs_xiazaisj"])
        self.assertIsNone(cached["bhsjtzs_data"])


if __name__ == "__main__":
    unittest.main()
