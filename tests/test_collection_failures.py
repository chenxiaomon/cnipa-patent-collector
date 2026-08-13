#!/usr/bin/env python
# -*- coding: utf-8 -*-

import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime
from pathlib import Path

from db_manager import PatentsDB


class TestCollectionFailures(unittest.TestCase):
    def test_record_failure_increments_attempts_and_updates_reason(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            db = PatentsDB(Path(temporary_directory) / "patents.db")

            first_failure = db.record_collection_failure(
                "fees",
                "202310411762X",
                "no_fee_payload",
            )
            second_failure = db.record_collection_failure(
                "fees",
                "202310411762X",
                "fee_persistence_failed",
            )

            self.assertEqual(first_failure["collection_kind"], "fees")
            self.assertEqual(first_failure["application_no"], "202310411762X")
            self.assertEqual(first_failure["attempt_count"], 1)
            self.assertEqual(second_failure["reason"], "fee_persistence_failed")
            self.assertEqual(second_failure["attempt_count"], 2)
            datetime.fromisoformat(second_failure["last_failed_at"].replace("Z", "+00:00"))
            self.assertEqual(db.failed_collection_targets("fees"), [second_failure])

    def test_failure_records_are_isolated_by_collection_kind(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            db = PatentsDB(Path(temporary_directory) / "patents.db")
            application_no = "202310411762X"

            db.record_collection_failure("fees", application_no, "fee_failed")
            db.record_collection_failure("agency", application_no, "agency_failed")

            fee_failures = db.failed_collection_targets("fees")
            agency_failures = db.failed_collection_targets("agency")

            self.assertEqual(len(fee_failures), 1)
            self.assertEqual(fee_failures[0]["reason"], "fee_failed")
            self.assertEqual(len(agency_failures), 1)
            self.assertEqual(agency_failures[0]["reason"], "agency_failed")

    def test_clear_failure_only_removes_requested_kind_and_reports_change(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            db = PatentsDB(Path(temporary_directory) / "patents.db")
            application_no = "202310411762X"
            db.record_collection_failure("fees", application_no, "fee_failed")
            db.record_collection_failure("agency", application_no, "agency_failed")

            self.assertTrue(db.clear_collection_failure("fees", application_no))
            self.assertFalse(db.clear_collection_failure("fees", application_no))
            self.assertEqual(db.failed_collection_targets("fees"), [])
            self.assertEqual(
                [
                    failure["application_no"]
                    for failure in db.failed_collection_targets("agency")
                ],
                [application_no],
            )

    def test_failure_operations_validate_kind_and_application_number(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            db = PatentsDB(Path(temporary_directory) / "patents.db")

            with self.assertRaises(ValueError):
                db.record_collection_failure("", "202310411762X", "failed")
            with self.assertRaises(ValueError):
                db.record_collection_failure("fees", "PCT/2025/134239", "failed")
            with self.assertRaises(ValueError):
                db.failed_collection_targets("   ")

    def test_summary_includes_all_failures_and_counts_by_kind(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "patents.db"
            db = PatentsDB(database_path)
            db.record_collection_failure(
                "agency",
                "202310411762X",
                "agency_failed",
            )
            db.record_collection_failure(
                "fees",
                "2023000000001",
                "fee_failed_first",
            )
            db.record_collection_failure(
                "fees",
                "2023000000002",
                "fee_failed_latest",
            )
            with closing(sqlite3.connect(database_path)) as connection:
                connection.execute(
                    "UPDATE collection_failures SET last_failed_at=? "
                    "WHERE collection_kind='agency'",
                    ("2026-08-13T01:00:00Z",),
                )
                connection.execute(
                    "UPDATE collection_failures SET last_failed_at=? "
                    "WHERE application_no='2023000000001'",
                    ("2026-08-13T02:00:00Z",),
                )
                connection.execute(
                    "UPDATE collection_failures SET last_failed_at=? "
                    "WHERE application_no='2023000000002'",
                    ("2026-08-13T03:00:00Z",),
                )
                connection.commit()

            summary = db.get_summary()

            self.assertEqual(
                summary["collection_failure_counts"],
                {"fees": 2, "agency": 1},
            )
            self.assertEqual(
                [
                    failure["application_no"]
                    for failure in summary["collection_failures"]
                ],
                ["2023000000002", "2023000000001", "202310411762X"],
            )


if __name__ == "__main__":
    unittest.main()
