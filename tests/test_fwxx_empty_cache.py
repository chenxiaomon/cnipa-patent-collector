#!/usr/bin/env python
# -*- coding: utf-8 -*-

import unittest
from contextlib import nullcontext
from unittest.mock import patch

from tests.test_mitm_scraper import _json_body, _verified_detail_flow


class TestEmptyFwxxCache(unittest.TestCase):
    @patch(
        "patent_mitm_scraper.reserve_json_cache_updates",
        side_effect=lambda _cache_file: nullcontext(),
    )
    @patch("patent_mitm_scraper.read_detail_attempt_marker", return_value={
        "application_no": "2026102909420", "attempt_id": "attempt-current",
    })
    @patch("patent_mitm_scraper.write_json_cache")
    @patch("patent_mitm_scraper.read_json_cache")
    @patch("patent_mitm_scraper.DetectionLogger")
    def test_explicit_empty_notice_list_is_cached_as_success(
        self,
        _logger_class,
        read_cache,
        write_cache,
        _attempt_marker,
        _cache_reservation,
    ):
        from patent_mitm_scraper import PatentMITMScraper

        read_cache.return_value = {}
        flow = _verified_detail_flow(
            "https://cponline.cnipa.gov.cn/api/view/gn/fwxx?token=abc",
            "2026102909420",
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
        scraper._process_fwxx_response(flow)

        cached = write_cache.call_args.args[1]["2026102909420"]
        self.assertEqual(cached["fwxx_list"], [])
        self.assertIsNone(cached["bhsjtzs_xiazaisj"])
        self.assertIsNone(cached["bhsjtzs_data"])


if __name__ == "__main__":
    unittest.main()
