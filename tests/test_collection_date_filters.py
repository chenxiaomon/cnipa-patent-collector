"""Regression coverage for collection-date filtering across stored time formats."""

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from db_manager import PatentsDB


class TestCollectionDateFilters(unittest.TestCase):
    def setUp(self):
        temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        self.patents_db = PatentsDB(Path(temporary_directory.name) / "patents.db")

    def _store_timestamps(self, timestamps):
        self.patents_db.upsert_batch([
            {"application_no": f"202600000{index:04d}", "timestamp": timestamp}
            for index, timestamp in enumerate(timestamps, start=1)
        ])

    def test_utc_midnight_microseconds_are_not_lost_by_text_ordering(self):
        inside_range = {
            "2026-09-06T00:00:00Z",
            "2026-09-06T00:00:00.000001Z",
            "2026-09-06T00:00:00.999999Z",
            "2026-09-06T12:00:00Z",
        }
        self._store_timestamps([
            "2026-09-05T23:59:59.999999Z",
            *sorted(inside_range),
            "2026-09-07T00:00:00Z",
        ])

        selected_patents = self.patents_db.query_filtered(
            ts_from="2026-09-06T00:00:00Z",
            ts_to="2026-09-06T23:59:59.999999Z",
        )

        self.assertEqual({patent["timestamp"] for patent in selected_patents}, inside_range)

    def test_beijing_day_includes_last_microsecond_and_excludes_next_midnight(self):
        self._store_timestamps([
            "2026-09-05T15:59:59.999999Z",
            "2026-09-05T16:00:00Z",
            "2026-09-06T15:59:59.999999Z",
            "2026-09-06T16:00:00Z",
        ])

        selected_patents = self.patents_db.query_filtered(
            ts_from="2026-09-06", ts_to="2026-09-06"
        )

        self.assertEqual({patent["timestamp"] for patent in selected_patents}, {
            "2026-09-05T16:00:00Z",
            "2026-09-06T15:59:59.999999Z",
        })

    def test_equal_instants_match_regardless_of_offset_or_naive_utc_storage(self):
        same_instant = {
            "2026-09-05T16:00:00.000001Z",
            "2026-09-05T16:00:00.000001+00:00",
            "2026-09-06T00:00:00.000001+08:00",
            "2026-09-05T12:00:00.000001-04:00",
            "2026-09-05T16:00:00.000001",
            "2026-09-05 16:00:00.000001",
        }
        self._store_timestamps([
            *sorted(same_instant),
            "2026-09-06T00:00:00+08:00",
            "2026-09-06T00:00:00.000002+08:00",
        ])

        selected_patents = self.patents_db.query_filtered(
            ts_from="2026-09-05T16:00:00.000001Z",
            ts_to="2026-09-06T00:00:00.000001+08:00",
        )

        self.assertEqual({patent["timestamp"] for patent in selected_patents}, same_instant)

    def test_date_ranges_handle_year_boundary_and_leap_day(self):
        self._store_timestamps([
            "2025-12-31T15:59:59.999999Z",
            "2025-12-31T16:00:00Z",
            "2026-01-01T15:59:59.999999Z",
            "2026-01-01T16:00:00Z",
            "2024-02-28T15:59:59.999999Z",
            "2024-02-28T16:00:00Z",
            "2024-02-29T15:59:59.999999Z",
            "2024-02-29T16:00:00Z",
        ])
        expected_days = {
            "2026-01-01": {
                "2025-12-31T16:00:00Z", "2026-01-01T15:59:59.999999Z"
            },
            "2024-02-29": {
                "2024-02-28T16:00:00Z", "2024-02-29T15:59:59.999999Z"
            },
        }

        for business_day, expected_timestamps in expected_days.items():
            with self.subTest(business_day=business_day):
                selected_patents = self.patents_db.query_filtered(
                    ts_from=business_day, ts_to=business_day
                )
                self.assertEqual(
                    {patent["timestamp"] for patent in selected_patents}, expected_timestamps
                )

    def test_single_date_endpoint_keeps_its_beijing_boundary(self):
        self._store_timestamps([
            "2026-09-05T15:59:59.999999Z",
            "2026-09-05T16:00:00Z",
            "2026-09-06T15:59:59.999999Z",
            "2026-09-06T16:00:00Z",
        ])

        after_start = self.patents_db.query_filtered(ts_from="2026-09-06")
        before_end = self.patents_db.query_filtered(ts_to="2026-09-06")

        self.assertEqual({patent["timestamp"] for patent in after_start}, {
            "2026-09-05T16:00:00Z",
            "2026-09-06T15:59:59.999999Z",
            "2026-09-06T16:00:00Z",
        })
        self.assertEqual({patent["timestamp"] for patent in before_end}, {
            "2026-09-05T15:59:59.999999Z",
            "2026-09-05T16:00:00Z",
            "2026-09-06T15:59:59.999999Z",
        })

    def test_naive_exact_filter_remains_utc_and_both_endpoints_are_inclusive(self):
        self._store_timestamps([
            "2026-09-06T03:59:59.999999Z",
            "2026-09-06T04:00:00Z",
            "2026-09-06T12:00:00+08:00",
            "2026-09-06T04:00:00.000001Z",
            "2026-09-06T04:00:00.000002Z",
        ])

        selected_patents = self.patents_db.query_filtered(
            ts_from="2026-09-06T04:00:00",
            ts_to="2026-09-06T04:00:00.000001",
        )

        self.assertEqual({patent["timestamp"] for patent in selected_patents}, {
            "2026-09-06T04:00:00Z",
            "2026-09-06T12:00:00+08:00",
            "2026-09-06T04:00:00.000001Z",
        })

    def test_dates_and_applicant_notice_rejection_dimensions_remain_or(self):
        self.patents_db.upsert_batch([
            {"application_no": "2026000000001", "timestamp": "2026-09-06T00:00:00Z"},
            {
                "application_no": "2026000000002", "timestamp": "not-a-timestamp",
                "shenqingrxm": "Joint Applicant; Alpha",
            },
            {
                "application_no": "2026000000003", "timestamp": "0001-01-01T00:00:00+08:00",
                "fwxx_list": [{"fawenmc": "Decision Notice", "fawenr": "20260803"}],
            },
            {
                "application_no": "2026000000004", "timestamp": None,
                "bhsjtzs_xiazaisj": "2026-07-02",
            },
            {
                "application_no": "2026000000005", "timestamp": "2026-01-01T00:00:00Z",
                "shenqingrxm": "Alpha Subsidiary",
                "fwxx_list": [
                    {"fawenmc": "Decision Notice", "fawenr": "20260703"},
                    {"fawenmc": "Other Notice", "fawenr": "20260803"},
                ],
                "bhsjtzs_xiazaisj": "2026-07-03",
            },
        ])

        selected_patents = self.patents_db.query_filtered(
            ts_from="2026-09-06", ts_to="2026-09-06",
            applicants=["Alpha"],
            notice_name_contains="Decision", notice_from="2026-08-01", notice_to="2026-08-31",
            rejection_from="2026-07-01", rejection_to="2026-07-02",
        )

        self.assertEqual({patent["application_no"] for patent in selected_patents}, {
            "2026000000001", "2026000000002", "2026000000003", "2026000000004"
        })

    def test_invalid_stored_timestamps_do_not_match_date_only_filter(self):
        self._store_timestamps([
            None, "", "broken", "2026-02-30T12:00:00Z",
            "0001-01-01T00:00:00+08:00", "9999-12-31T23:59:59-08:00",
            "2026-09-06T12:00:00Z",
        ])

        selected_patents = self.patents_db.query_filtered(ts_from="2026-09-06")

        self.assertEqual(
            [patent["timestamp"] for patent in selected_patents], ["2026-09-06T12:00:00Z"]
        )

    def test_invalid_filter_is_rejected_even_when_another_dimension_matches(self):
        self.patents_db.upsert({
            "application_no": "2026000000001", "timestamp": "2026-09-06T12:00:00Z",
            "shenqingrxm": "Alpha",
        })

        for invalid_filter in ("broken", "2026-02-29", "2026-09-31", "   ", 0, False, [], {}):
            for endpoint in ("ts_from", "ts_to"):
                with self.subTest(endpoint=endpoint, invalid_filter=invalid_filter):
                    with self.assertRaises(ValueError):
                        self.patents_db.query_filtered(
                            applicants=["Alpha"], **{endpoint: invalid_filter}
                        )

    def test_reversed_date_and_exact_ranges_are_rejected(self):
        self._store_timestamps(["2026-09-06T12:00:00Z"])

        for start, end in (
            ("2026-09-07", "2026-09-06"),
            ("2026-09-06T00:00:00.000001Z", "2026-09-06T00:00:00Z"),
            ("2026-09-06T23:59:59Z", "2026-09-07T00:00:00+08:00"),
        ):
            with self.subTest(start=start, end=end):
                with self.assertRaises(ValueError):
                    self.patents_db.query_filtered(ts_from=start, ts_to=end)

    def test_omitted_date_filters_keep_unfiltered_export(self):
        self._store_timestamps([None, "broken", "2026-09-06T12:00:00Z"])

        self.assertEqual(len(self.patents_db.query_filtered(ts_from="", ts_to="")), 3)

    def test_daily_summary_orders_seven_beijing_days_across_new_year(self):
        self._store_timestamps([
            "2025-12-27T15:59:59.999999Z",
            "2025-12-27T16:00:00Z",
            "2025-12-31T12:00:00+08:00",
            "2026-01-01T04:00:00Z",
            "2026-01-02T16:00:00Z",
            "2026-01-03T16:00:00Z",
            "2025-01-01T04:00:00Z",
            "2027-01-01T04:00:00Z",
        ])
        beijing_now = datetime(2026, 1, 3, 0, 30, tzinfo=timezone(timedelta(hours=8)))

        with patch("db_manager.datetime", wraps=datetime) as frozen_datetime:
            frozen_datetime.now.side_effect = beijing_now.astimezone
            summary = self.patents_db.get_summary()

        self.assertEqual(summary["daily_counts"], [
            {"date": "12-28", "count": 1},
            {"date": "12-29", "count": 0},
            {"date": "12-30", "count": 0},
            {"date": "12-31", "count": 1},
            {"date": "01-01", "count": 1},
            {"date": "01-02", "count": 0},
            {"date": "01-03", "count": 1},
        ])

    def test_daily_summary_keeps_midnight_microseconds_in_their_business_day(self):
        self._store_timestamps([
            "2026-09-05T15:59:59.999999Z",
            "2026-09-05T23:59:59.999999+08:00",
            "2026-09-05T15:59:59.999999",
            "2026-09-05T16:00:00Z",
            "2026-09-06T00:00:00+08:00",
            "2026-09-05T16:00:00",
            "2026-09-06T15:59:59.999999+00:00",
            "2026-09-06T16:00:00Z",
        ])
        beijing_now = datetime(2026, 9, 6, 12, tzinfo=timezone(timedelta(hours=8)))

        with patch("db_manager.datetime", wraps=datetime) as frozen_datetime:
            frozen_datetime.now.side_effect = beijing_now.astimezone
            summary = self.patents_db.get_summary()

        self.assertEqual(summary["daily_counts"], [
            {"date": "08-31", "count": 0},
            {"date": "09-01", "count": 0},
            {"date": "09-02", "count": 0},
            {"date": "09-03", "count": 0},
            {"date": "09-04", "count": 0},
            {"date": "09-05", "count": 3},
            {"date": "09-06", "count": 4},
        ])

    def test_daily_summary_ignores_invalid_and_overflowing_timestamps(self):
        self._store_timestamps([
            None,
            "broken",
            "2026-02-30T12:00:00Z",
            "0001-01-01T00:00:00+08:00",
            "9999-12-31T23:59:59-08:00",
            "9999-12-31T16:00:00Z",
            "2026-09-06T12:00:00+08:00",
        ])
        beijing_now = datetime(2026, 9, 6, 12, tzinfo=timezone(timedelta(hours=8)))

        with patch("db_manager.datetime", wraps=datetime) as frozen_datetime:
            frozen_datetime.now.side_effect = beijing_now.astimezone
            summary = self.patents_db.get_summary()

        self.assertEqual(summary["unique_count"], 7)
        self.assertEqual(sum(day["count"] for day in summary["daily_counts"]), 1)
        self.assertEqual(summary["daily_counts"][-1], {"date": "09-06", "count": 1})


if __name__ == "__main__":
    unittest.main()
