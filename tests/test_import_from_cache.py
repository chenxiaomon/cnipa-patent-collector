#!/usr/bin/env python3
"""Phase 0 cache imports retain their source until the whole batch succeeds."""

import json
import tempfile
import threading
import unittest
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import Mock, patch

import import_from_cache
from cache_utils import read_json_cache, reserve_json_cache_updates, write_json_cache


class TestPhase0CacheImport(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.cache_file = Path(self.temporary_directory.name) / "patent_cache.json"
        self.existing_app_no = "202310411762X"
        self.new_app_no = "2024100659780"
        self.cache_payload = {
            self.existing_app_no: {"zhuanlimc": "已入库专利"},
            self.new_app_no: {"zhuanlimc": "待导入专利"},
        }
        self.cache_file.write_text(
            json.dumps(self.cache_payload, ensure_ascii=False),
            encoding="utf-8",
        )

        cache_path_patch = patch.object(import_from_cache, "CACHE_FILE", str(self.cache_file))
        reservation_patch = patch.object(
            import_from_cache,
            "reserve_phase0_browser",
            return_value=nullcontext(),
        )
        desktop_reservation_patch = patch.object(
            import_from_cache,
            "reserve_detail_collection_desktop",
            return_value=nullcontext(),
        )
        cache_path_patch.start()
        reservation_patch.start()
        desktop_reservation_patch.start()
        self.addCleanup(cache_path_patch.stop)
        self.addCleanup(reservation_patch.stop)
        self.addCleanup(desktop_reservation_patch.stop)

    def _run_import(self, logger):
        with patch.object(import_from_cache, "DetectionLogger", return_value=logger):
            return import_from_cache.import_from_cache()

    def test_database_failure_keeps_existing_and_unimported_cache_entries(self):
        logger = Mock()
        logger.get_processed_applications.return_value = {self.existing_app_no}
        logger.add_records.side_effect = OSError("synthetic database failure")

        self.assertFalse(self._run_import(logger))

        self.assertEqual(
            json.loads(self.cache_file.read_text(encoding="utf-8")),
            self.cache_payload,
        )
        logger.add_records.assert_called_once()
        self.assertEqual(len(logger.add_records.call_args.args[0]), 1)
        logger.refresh_jsonl_backup.assert_not_called()

    def test_complete_batch_clears_cache(self):
        logger = Mock()
        logger.get_processed_applications.return_value = {self.existing_app_no}
        logger.add_records.return_value = 1

        self.assertTrue(self._run_import(logger))
        self.assertEqual(
            json.loads(self.cache_file.read_text(encoding="utf-8")),
            {},
        )
        logger.refresh_jsonl_backup.assert_called_once_with()

    def test_backup_failure_is_repaired_before_retry_clears_cache(self):
        first_logger = Mock()
        first_logger.get_processed_applications.return_value = {self.existing_app_no}
        first_logger.add_records.return_value = 1
        first_logger.refresh_jsonl_backup.side_effect = OSError("backup disk unavailable")

        self.assertFalse(self._run_import(first_logger))
        self.assertEqual(
            json.loads(self.cache_file.read_text(encoding="utf-8")),
            self.cache_payload,
        )

        retry_logger = Mock()
        retry_logger.get_processed_applications.return_value = set(self.cache_payload)
        retry_logger.add_records.return_value = 0
        retry_logger.refresh_jsonl_backup.return_value = len(self.cache_payload)

        self.assertTrue(self._run_import(retry_logger))
        retry_logger.add_records.assert_called_once_with([])
        retry_logger.refresh_jsonl_backup.assert_called_once_with()
        self.assertEqual(
            json.loads(self.cache_file.read_text(encoding="utf-8")),
            {},
        )

    def test_late_cache_write_waits_until_import_finishes(self):
        late_app_no = "2025100659781"
        late_entry = {"zhuanlimc": "晚到响应"}
        writer_attempting = threading.Event()
        writer_entered = threading.Event()
        writer = None

        def write_late_entry():
            writer_attempting.set()
            with reserve_json_cache_updates(str(self.cache_file)):
                writer_entered.set()
                cache_entries = read_json_cache(str(self.cache_file))
                cache_entries[late_app_no] = late_entry
                write_json_cache(str(self.cache_file), cache_entries)

        def add_records_then_start_writer(records):
            nonlocal writer
            writer = threading.Thread(target=write_late_entry)
            writer.start()
            self.assertTrue(writer_attempting.wait(5))
            self.assertFalse(writer_entered.wait(0.2))
            return len(records)

        logger = Mock()
        logger.get_processed_applications.return_value = {self.existing_app_no}
        logger.add_records.side_effect = add_records_then_start_writer
        logger.refresh_jsonl_backup.return_value = 2

        self.assertTrue(self._run_import(logger))
        self.assertIsNotNone(writer)
        writer.join(5)
        self.assertFalse(writer.is_alive())
        self.assertEqual(
            read_json_cache(str(self.cache_file)),
            {late_app_no: late_entry},
        )

    def test_browser_reservation_failure_leaves_cache_untouched(self):
        busy_error = import_from_cache.DetailCollectionDesktopBusyError("Phase 0 浏览器正在运行")
        with patch.object(
            import_from_cache,
            "reserve_phase0_browser",
            side_effect=busy_error,
        ), patch.object(import_from_cache, "DetectionLogger") as logger_class:
            self.assertFalse(import_from_cache.import_from_cache())

        logger_class.assert_not_called()
        self.assertEqual(
            json.loads(self.cache_file.read_text(encoding="utf-8")),
            self.cache_payload,
        )


if __name__ == "__main__":
    unittest.main()
