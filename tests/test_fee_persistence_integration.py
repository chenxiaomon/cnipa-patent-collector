#!/usr/bin/env python
# -*- coding: utf-8 -*-

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import collect_fees
from db_manager import PatentsDB


class TestFeePersistenceIntegration(unittest.TestCase):
    def test_partial_fee_snapshot_preserves_status_timestamp(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            db_path = Path(temporary_directory) / "patents.db"
            db = PatentsDB(db_path)
            original_timestamp = "2026-07-01T08:00:00Z"
            db.upsert({
                "application_no": "2026102909420",
                "timestamp": original_timestamp,
                "anjianywzt": "rejected",
            })

            with patch.object(collect_fees, "PATENTS_DB_FILE", db_path):
                persisted = collect_fees.persist_fee_fields(
                    "2026102909420",
                    {
                        "payable_fee_records": [],
                        "fee_snapshot_at": "2026-07-27T00:00:00Z",
                    },
                )

            self.assertTrue(persisted)
            record = db.get_record("2026102909420")
            self.assertEqual(record["timestamp"], original_timestamp)
            self.assertEqual(record["payable_fee_records"], [])
            self.assertEqual(
                record["fee_snapshot_at"],
                "2026-07-27T00:00:00Z",
            )
            self.assertIsNone(record["late_fee_schedule_records"])
            self.assertIsNone(record["paid_fee_records"])
            self.assertIsNone(record["fee_receipt_dispatch_records"])


if __name__ == "__main__":
    unittest.main()
