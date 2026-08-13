#!/usr/bin/env python
# -*- coding: utf-8 -*-

import io
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import import_agency_csv


class TestAgencyCsvImport(unittest.TestCase):
    def test_csv_updates_registered_record_and_reports_each_skip_reason(self):
        csv_text = (
            "application_no,agency,agent\n"
            "CN202411006597.0,Agency A,Agent A\n"
            "CN202111504942.X,,\n"
            "CN.,Invalid application number,\n"
            "CN202499999999.9,Missing record,\n"
            ",Missing application number,\n"
        )
        database = MagicMock()
        database.get_all_app_nos.return_value = {"2024110065970"}

        with (
            patch.object(Path, "read_text", return_value=csv_text),
            patch.object(import_agency_csv, "PatentsDB", return_value=database),
        ):
            statistics = import_agency_csv.import_agency(Path("agencies.csv"))

        self.assertEqual(
            statistics,
            {
                "updated": 1,
                "skipped_invalid": 1,
                "skipped_no_agency": 1,
                "skipped_missing": 1,
                "bad_rows": 1,
            },
        )
        database.update_fields.assert_called_once_with(
            "2024110065970",
            {"daili_jg": "Agency A", "daili_r": "Agent A"},
        )

    def test_csv_reader_falls_back_to_gbk(self):
        csv_text = "application_no,agency\n2024110065970,Agency A\n"
        decode_error = UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid")

        with patch.object(
            Path,
            "read_text",
            side_effect=[decode_error, decode_error, csv_text],
        ) as read_text:
            headers, rows = import_agency_csv._read_csv(Path("agencies.csv"))

        self.assertEqual(headers, ["application_no", "agency"])
        self.assertEqual(rows[0]["agency"], "Agency A")
        self.assertEqual(
            [read_call.kwargs["encoding"] for read_call in read_text.call_args_list],
            ["utf-8-sig", "utf-8", "gbk"],
        )

    def test_csv_reader_reports_all_encoding_failures(self):
        decode_error = UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid")

        with (
            patch.object(Path, "read_text", side_effect=decode_error),
            self.assertRaisesRegex(ValueError, "UTF-8/GBK"),
        ):
            import_agency_csv._read_csv(Path("agencies.csv"))

    def test_missing_required_headers_are_rejected(self):
        cases = (
            (["agency"], "申请号"),
            (["application_no"], "代理机构"),
        )

        for headers, expected_label in cases:
            with (
                self.subTest(headers=headers),
                patch.object(import_agency_csv, "parse_file", return_value=(headers, [])),
                self.assertRaisesRegex(ValueError, expected_label),
            ):
                import_agency_csv.import_agency(Path("agencies.csv"))

    def test_dry_run_counts_update_without_writing_database(self):
        database = MagicMock()
        database.get_all_app_nos.return_value = {"2024110065970"}
        parsed_file = (
            ["application_no", "agency"],
            [{"application_no": "2024110065970", "agency": "Agency A"}],
        )

        with (
            patch.object(import_agency_csv, "parse_file", return_value=parsed_file),
            patch.object(import_agency_csv, "PatentsDB", return_value=database),
        ):
            statistics = import_agency_csv.import_agency(
                Path("agencies.csv"),
                dry_run=True,
            )

        self.assertEqual(statistics["updated"], 1)
        database.update_fields.assert_not_called()

    def test_malformed_number_cannot_normalize_into_an_existing_application(self):
        database = MagicMock()
        database.get_all_app_nos.return_value = {"2024110065970"}
        parsed_file = (
            ["application_no", "agency"],
            [{"application_no": "CNCN202411006597.0", "agency": "Wrong Agency"}],
        )

        with (
            patch.object(import_agency_csv, "parse_file", return_value=parsed_file),
            patch.object(import_agency_csv, "PatentsDB", return_value=database),
        ):
            statistics = import_agency_csv.import_agency(Path("agencies.csv"))

        self.assertEqual(statistics["skipped_invalid"], 1)
        self.assertEqual(statistics["updated"], 0)
        database.update_fields.assert_not_called()

    def test_cli_successfully_prints_the_returned_statistics(self):
        statistics = {
            "updated": 3,
            "skipped_invalid": 2,
            "skipped_no_agency": 4,
            "skipped_missing": 5,
            "bad_rows": 1,
        }
        stdout = io.StringIO()

        with (
            patch.object(sys, "argv", ["import_agency_csv.py", __file__]),
            patch.object(import_agency_csv, "import_agency", return_value=statistics),
            patch("sys.stdout", stdout),
        ):
            import_agency_csv.main()

        output = stdout.getvalue()
        self.assertIn("更新：3 条", output)
        self.assertIn("跳过（申请号格式无效）：2 条", output)
        self.assertIn("跳过（代理机构为空）：4 条", output)
        self.assertIn("跳过（DB 中无此申请号）：5 条", output)
        self.assertIn("申请号为空：1 行", output)

    def test_cli_dry_run_with_no_updates_exits_successfully(self):
        statistics = {
            "updated": 0,
            "skipped_invalid": 0,
            "skipped_no_agency": 0,
            "skipped_missing": 0,
            "bad_rows": 0,
        }
        stdout = io.StringIO()

        with (
            patch.object(sys, "argv", ["import_agency_csv.py", __file__, "--dry"]),
            patch.object(
                import_agency_csv,
                "import_agency",
                return_value=statistics,
            ) as importer,
            patch("sys.stdout", stdout),
        ):
            import_agency_csv.main()

        importer.assert_called_once_with(Path(__file__), dry_run=True)
        self.assertIn("预览完成", stdout.getvalue())

    def test_cli_parse_error_exits_with_failure(self):
        stdout = io.StringIO()

        with (
            patch.object(sys, "argv", ["import_agency_csv.py", __file__]),
            patch.object(
                import_agency_csv,
                "import_agency",
                side_effect=ValueError("bad file"),
            ),
            patch("sys.stdout", stdout),
            self.assertRaisesRegex(SystemExit, "1"),
        ):
            import_agency_csv.main()

        self.assertIn("bad file", stdout.getvalue())

    def test_legacy_xls_is_rejected_with_a_clear_error(self):
        with (
            patch.object(import_agency_csv, "_read_excel") as read_excel,
            self.assertRaisesRegex(ValueError, r"\.xls.*(不支持|unsupported)"),
        ):
            import_agency_csv.parse_file(Path("legacy.xls"))

        read_excel.assert_not_called()

    def test_xlsx_is_routed_to_the_xlsx_reader(self):
        expected = (["application_no", "agency"], [])

        with patch.object(
            import_agency_csv,
            "_read_excel",
            return_value=expected,
        ) as read_excel:
            parsed = import_agency_csv.parse_file(Path("agencies.xlsx"))

        self.assertEqual(parsed, expected)
        read_excel.assert_called_once_with(Path("agencies.xlsx"))

    def test_unknown_extension_is_rejected(self):
        with self.assertRaisesRegex(ValueError, r"\.txt.*\.csv / \.xlsx"):
            import_agency_csv.parse_file(Path("agencies.txt"))


if __name__ == "__main__":
    unittest.main()
