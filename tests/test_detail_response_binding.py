#!/usr/bin/env python
# -*- coding: utf-8 -*-

import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from tests.test_mitm_scraper import (
    FWXX_RESPONSE,
    FYXX_RESPONSE,
    _json_body,
    _make_flow,
)


class TestDetailResponseTargetBinding(unittest.TestCase):
    @patch("patent_mitm_scraper.write_json_cache")
    @patch("patent_mitm_scraper.read_json_cache")
    @patch("patent_mitm_scraper.DetectionLogger")
    def test_delayed_fwxx_response_keeps_request_time_application_no(
        self,
        _logger_class,
        read_cache,
        write_cache,
    ):
        from patent_mitm_scraper import PatentMITMScraper

        written_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        read_cache.side_effect = [
            {"application_no": "A", "written_at": written_at},
            {"application_no": "B", "written_at": written_at},
            {},
        ]
        delayed_flow = _make_flow(
            "https://cponline.cnipa.gov.cn/api/view/gn/fwxx?token=a",
            body=_json_body(FWXX_RESPONSE),
        )
        next_flow = _make_flow(
            "https://cponline.cnipa.gov.cn/api/view/gn/fwxx?token=b",
            body=_json_body(FWXX_RESPONSE),
        )
        delayed_flow.metadata = {}
        next_flow.metadata = {}
        scraper = PatentMITMScraper()

        scraper.request(delayed_flow)
        scraper.request(next_flow)
        scraper._process_fwxx_response(delayed_flow)

        cached_records = write_cache.call_args.args[1]
        self.assertIn("A", cached_records)
        self.assertNotIn("B", cached_records)

    @patch("patent_mitm_scraper.write_json_cache")
    @patch("patent_mitm_scraper.read_json_cache")
    @patch("patent_mitm_scraper.DetectionLogger")
    def test_delayed_fee_response_keeps_request_time_application_no(
        self,
        _logger_class,
        read_cache,
        write_cache,
    ):
        from patent_mitm_scraper import PatentMITMScraper

        written_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        read_cache.side_effect = [
            {"application_no": "A", "written_at": written_at},
            {"application_no": "B", "written_at": written_at},
            {},
        ]
        delayed_flow = _make_flow(
            "https://cponline.cnipa.gov.cn/api/view/gn/fyxx?token=a",
            body=_json_body(FYXX_RESPONSE),
        )
        next_flow = _make_flow(
            "https://cponline.cnipa.gov.cn/api/view/gn/fyxx?token=b",
            body=_json_body(FYXX_RESPONSE),
        )
        delayed_flow.metadata = {}
        next_flow.metadata = {}
        scraper = PatentMITMScraper()

        scraper.request(delayed_flow)
        scraper.request(next_flow)
        scraper._process_fee_response(delayed_flow)

        cached_records = write_cache.call_args.args[1]
        self.assertIn("A", cached_records)
        self.assertNotIn("B", cached_records)


if __name__ == "__main__":
    unittest.main()
