#!/usr/bin/env python
# -*- coding: utf-8 -*-

import io
import unittest
from argparse import Namespace
from contextlib import ExitStack, redirect_stderr
from unittest.mock import patch

import collect_fees


APPLICATION_NO = "202310411762X"


def _complete_fee_payload():
    return {
        "payable_fee_records": [],
        "paid_fee_records": [],
        "fee_receipt_dispatch_records": [],
    }


def _collection_arguments(**overrides):
    arguments = {
        "test": None,
        "input": None,
        "app": None,
        "retry_failed": False,
        "force": False,
        "url": "https://example.invalid",
    }
    arguments.update(overrides)
    return Namespace(**arguments)


class TestFeeFailedTargetSource(unittest.TestCase):
    def test_failed_target_loader_reads_only_fee_failures(self):
        with patch("collect_fees.PatentsDB") as db_class:
            db_class.return_value.failed_collection_targets.return_value = [
                {
                    "collection_kind": "fees",
                    "application_no": APPLICATION_NO,
                    "reason": "no_fee_payload",
                    "attempt_count": 2,
                    "last_failed_at": "2026-08-13T00:00:00Z",
                }
            ]

            targets = collect_fees.load_failed_fee_targets()

        self.assertEqual(targets, [APPLICATION_NO])
        db_class.return_value.failed_collection_targets.assert_called_once_with("fees")

    def test_retry_failed_is_a_cli_target_source(self):
        arguments = collect_fees._build_argument_parser().parse_args(["--retry-failed"])

        self.assertTrue(arguments.retry_failed)

    def test_retry_failed_cannot_be_combined_with_input_or_app(self):
        parser = collect_fees._build_argument_parser()
        for conflicting_arguments in (
            ["--retry-failed", "--input", "targets.txt"],
            ["--retry-failed", "--app", APPLICATION_NO],
        ):
            with self.subTest(arguments=conflicting_arguments):
                with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                    parser.parse_args(conflicting_arguments)

    def test_retry_failed_cannot_be_combined_with_force(self):
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            collect_fees.main(["--retry-failed", "--force"])

    def test_retry_source_does_not_fall_back_to_dataset_or_manual_targets(self):
        with (
            patch("collect_fees.load_failed_fee_targets", return_value=[]) as failed_targets,
            patch("collect_fees.load_fee_dataset_targets") as dataset_targets,
            patch("collect_fees.load_standalone_targets") as manual_targets,
        ):
            collect_fees._run_fee_collection(
                _collection_arguments(retry_failed=True)
            )

        failed_targets.assert_called_once_with()
        dataset_targets.assert_not_called()
        manual_targets.assert_not_called()


class TestFeeFailureLifecycle(unittest.TestCase):
    def _run_one_target(self, fee_fields, stored_snapshot):
        patches = (
            patch("collect_fees.load_fee_dataset_targets", return_value=[APPLICATION_NO]),
            patch("collect_fees.CoordinateService"),
            patch("collect_fees.BrowserService"),
            patch("collect_fees.countdown"),
            patch("collect_fees.is_browser_alive", return_value=True),
            patch("collect_fees.collect_one_fee", return_value=fee_fields),
            patch("collect_fees.persist_fee_fields", return_value=stored_snapshot),
            patch("collect_fees.DetectionLogger"),
            patch("collect_fees.PatentsDB"),
        )
        with ExitStack() as stack:
            (
                _load_targets,
                coordinate_service,
                _browser_service,
                _countdown,
                _browser_alive,
                _collect_one,
                _persist_fields,
                _logger,
                db_class,
            ) = [stack.enter_context(test_patch) for test_patch in patches]
            coordinate_service.load_or_record_search_coordinates.return_value = (
                1,
                2,
                3,
                4,
            )
            coordinate_service.load_or_record_detail_link_coordinates.return_value = (
                5,
                6,
            )

            collect_fees._run_fee_collection(_collection_arguments())

            return db_class.return_value

    def test_no_payload_records_a_retryable_failure(self):
        db = self._run_one_target(fee_fields=None, stored_snapshot=None)

        db.record_collection_failure.assert_called_once_with(
            "fees",
            APPLICATION_NO,
            "no_fee_payload",
        )
        db.clear_collection_failure.assert_not_called()

    def test_persistence_failure_records_a_retryable_failure(self):
        db = self._run_one_target(
            fee_fields=_complete_fee_payload(),
            stored_snapshot=None,
        )

        db.record_collection_failure.assert_called_once_with(
            "fees",
            APPLICATION_NO,
            "fee_persistence_failed",
        )
        db.clear_collection_failure.assert_not_called()

    def test_partial_payload_is_persisted_but_remains_retryable(self):
        db = self._run_one_target(
            fee_fields={"payable_fee_records": []},
            stored_snapshot={
                "payable_fee_records": [],
                "paid_fee_records": None,
                "fee_receipt_dispatch_records": None,
            },
        )

        db.record_collection_failure.assert_called_once_with(
            "fees",
            APPLICATION_NO,
            "incomplete_fee_payload",
        )
        db.clear_collection_failure.assert_not_called()

    def test_success_clears_a_previous_failure(self):
        db = self._run_one_target(
            fee_fields=_complete_fee_payload(),
            stored_snapshot=_complete_fee_payload(),
        )

        db.clear_collection_failure.assert_called_once_with(
            "fees",
            APPLICATION_NO,
        )
        db.record_collection_failure.assert_not_called()


if __name__ == "__main__":
    unittest.main()
