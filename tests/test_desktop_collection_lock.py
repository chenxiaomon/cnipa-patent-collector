#!/usr/bin/env python
# -*- coding: utf-8 -*-

import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import MagicMock, patch

import collect_fwxx
import desktop_collection_lock


class TestDesktopCollectionLock(unittest.TestCase):
    def test_second_detail_collection_is_rejected_while_lock_is_held(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            lock_path = Path(temporary_directory) / "detail_collection.lock"
            with patch.object(
                desktop_collection_lock,
                "DETAIL_COLLECTION_LOCK_FILE",
                lock_path,
            ):
                with desktop_collection_lock.reserve_detail_collection_desktop(
                    "dispatch collection"
                ):
                    with self.assertRaises(
                        desktop_collection_lock.DetailCollectionDesktopBusyError
                    ):
                        with desktop_collection_lock.reserve_detail_collection_desktop(
                            "fee collection"
                        ):
                            self.fail("the second desktop collection must not start")

    @patch("collect_fwxx._run_fwxx_collection")
    @patch("collect_fwxx.reserve_detail_collection_desktop")
    def test_fwxx_entrypoint_reserves_desktop_for_entire_collection(
        self,
        reserve_desktop,
        run_collection,
    ):
        reservation = MagicMock()
        reserve_desktop.return_value = reservation
        arguments = Namespace()

        collect_fwxx.run_fwxx_collection(arguments)

        reserve_desktop.assert_called_once_with("发文信息采集")
        reservation.__enter__.assert_called_once_with()
        run_collection.assert_called_once_with(arguments)
        reservation.__exit__.assert_called_once()


if __name__ == "__main__":
    unittest.main()
