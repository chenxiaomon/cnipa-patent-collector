#!/usr/bin/env python
# -*- coding: utf-8 -*-

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from collect_fees import _append_unmatched_fee, persist_fee_fields
from collect_fwxx import load_standalone_targets
from manual_fwxx_requests import create_manual_fwxx_request, parse_manual_fwxx_targets


class TestManualFwxxTargets(unittest.TestCase):
    def test_normalizes_and_deduplicates_pasted_targets(self):
        targets = parse_manual_fwxx_targets(
            "CN202411006597.0\n2024110065970, CN202111504942.X"
        )
        self.assertEqual(targets, ["2024110065970", "202111504942X"])

    def test_accepts_application_numbers_with_bare_x_check_digit(self):
        targets = parse_manual_fwxx_targets("201911399694X, 202110049482X")
        self.assertEqual(targets, ["201911399694X", "202110049482X"])

    def test_rejects_invalid_target_instead_of_silently_ignoring_it(self):
        with self.assertRaisesRegex(ValueError, "格式不正确"):
            parse_manual_fwxx_targets("CN202411006597.0\nnot-a-patent")

    def test_rejects_more_than_configured_limit(self):
        pasted = "\n".join(f"20240000{i:05d}" for i in range(3))
        with self.assertRaisesRegex(ValueError, "最多允许 2 个"):
            parse_manual_fwxx_targets(pasted, max_app_nos=2)

    def test_creates_unique_normalized_request_file(self):
        with TemporaryDirectory() as temp_dir:
            first_path, first_targets = create_manual_fwxx_request(
                "CN202411006597.0\nCN202111504942.X",
                request_dir=Path(temp_dir),
            )
            second_path, _ = create_manual_fwxx_request(
                "CN202411006597.0",
                request_dir=Path(temp_dir),
            )

            self.assertNotEqual(first_path, second_path)
            self.assertEqual(first_targets, ["2024110065970", "202111504942X"])
            self.assertEqual(
                first_path.read_text(encoding="utf-8"),
                "2024110065970\n202111504942X\n",
            )


class TestStandaloneFwxxForceMode(unittest.TestCase):
    @patch("collect_fwxx._load_standalone_collected", return_value={"2024110065970"})
    def test_force_mode_keeps_targets_with_existing_fwxx(self, _mock_collected):
        targets = load_standalone_targets(
            app_nos="CN202411006597.0, CN202111504942.X",
            force=True,
        )
        self.assertEqual(targets, ["2024110065970", "202111504942X"])

    @patch("collect_fwxx._load_standalone_collected", return_value={"2024110065970"})
    def test_resume_mode_still_skips_targets_with_existing_fwxx(self, _mock_collected):
        targets = load_standalone_targets(
            app_nos="CN202411006597.0, CN202111504942.X",
            force=False,
        )
        self.assertEqual(targets, ["202111504942X"])


class TestFeeFieldPersistence(unittest.TestCase):
    @patch("collect_fees.PatentsDB")
    def test_partial_fee_success_does_not_clear_missing_fwxx_fields(self, mock_db_class):
        mock_db = mock_db_class.return_value
        mock_db.update_fee_snapshot.return_value = {
            "payable_fee_records": [], "paid_fee_records": [], "fee_receipt_dispatch_records": [],
        }

        self.assertTrue(persist_fee_fields(
            "2026102909420",
            {
                "payable_fee_records": [],
                "paid_fee_records": [],
                "fee_receipt_dispatch_records": [],
                "fee_snapshot_at": "2026-07-18T00:00:00Z",
            },
        ))

        persisted = mock_db.update_fee_snapshot.call_args[0][1]
        self.assertEqual(persisted["payable_fee_records"], [])
        self.assertEqual(persisted["paid_fee_records"], [])
        self.assertEqual(persisted["fee_receipt_dispatch_records"], [])
        self.assertEqual(persisted["fee_snapshot_at"], "2026-07-18T00:00:00Z")
        self.assertNotIn("timestamp", persisted)
        self.assertNotIn("fwxx_list", persisted)
        self.assertNotIn("bhsjtzs_data", persisted)
        self.assertNotIn("late_fee_schedule_records", persisted)

    def test_unmatched_backup_omits_fee_sections_not_returned_by_api(self):
        with TemporaryDirectory() as temp_dir:
            unmatched_path = Path(temp_dir) / "fee_unmatched.json"
            with patch("collect_fees.FEE_UNMATCHED_FILE", str(unmatched_path)):
                _append_unmatched_fee(
                    "2026102909420",
                    {
                        "payable_fee_records": [],
                        "paid_fee_records": [{"yijiaofpjhm": "0026303067"}],
                        "fee_snapshot_at": "2026-07-18T00:00:00Z",
                    },
                    reason="not_found_in_db",
                )

            import json
            payload = json.loads(unmatched_path.read_text(encoding="utf-8"))
            backed_up = payload["records"][0]
            self.assertEqual(backed_up["payable_fee_records"], [])
            self.assertNotIn("late_fee_schedule_records", backed_up)
            self.assertNotIn("fee_receipt_dispatch_records", backed_up)


if __name__ == "__main__":
    unittest.main()
