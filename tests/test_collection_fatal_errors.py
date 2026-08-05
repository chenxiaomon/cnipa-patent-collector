#!/usr/bin/env python
# -*- coding: utf-8 -*-

import unittest
from argparse import Namespace
from unittest.mock import patch

import collect_fees
import collect_fwxx


def collection_arguments() -> Namespace:
    return Namespace(
        test=None,
        input=None,
        app=None,
        force=False,
        url="https://example.invalid",
    )


class TestCollectionFatalErrors(unittest.TestCase):
    @patch(
        "collect_fwxx.BrowserService.launch_and_login",
        side_effect=RuntimeError("browser failed"),
    )
    @patch("collect_fwxx.load_target_applications", return_value=["A"])
    def test_fwxx_batch_propagates_browser_startup_failure(
        self,
        _load_targets,
        _launch_browser,
    ):
        with self.assertRaisesRegex(RuntimeError, "browser failed"):
            collect_fwxx._run_fwxx_collection(collection_arguments())

    @patch(
        "collect_fees.BrowserService.launch_and_login",
        side_effect=RuntimeError("browser failed"),
    )
    @patch("collect_fees.load_fee_dataset_targets", return_value=["A"])
    def test_fee_batch_propagates_browser_startup_failure(
        self,
        _load_targets,
        _launch_browser,
    ):
        with self.assertRaisesRegex(RuntimeError, "browser failed"):
            collect_fees._run_fee_collection(collection_arguments())


if __name__ == "__main__":
    unittest.main()
