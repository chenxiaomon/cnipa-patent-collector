"""Fee snapshots must stay coherent across every local record-writing path."""

import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

import collect_fees
import detection_logger
from db_manager import PatentsDB
from detection_logger import DetectionLogger, DetectionRecord


APPLICATION_NO = "2026102909420"
RECENT_SNAPSHOT_AT = "2026-09-06T01:00:00.123456Z"
OLDER_SNAPSHOT_AT = "2026-09-01T00:00:00Z"
NEWER_SNAPSHOT_AT = "2026-09-07T00:00:00Z"
ORIGINAL_STATUS_TIMESTAMP = "2026-08-20T00:00:00Z"


class TestFeeSnapshotVersions(unittest.TestCase):
    def setUp(self):
        temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        self.temporary_path = Path(temporary_directory.name)
        self.database_path = self.temporary_path / "patents.db"
        self.database = PatentsDB(self.database_path)
        self.database.upsert({
            "application_no": APPLICATION_NO,
            "timestamp": ORIGINAL_STATUS_TIMESTAMP,
            "anjianywzt": "等待实审提案",
            "error_message": "retained status error",
            "payable_fee_records": [{"yingjiaoje": 900}],
            "late_fee_schedule_records": [{"zhinajzj": 9}],
            "paid_fee_records": [{"yijiaofje": 600}],
            "fee_receipt_dispatch_records": [{"shoujufwsjh": "recent"}],
            "fee_snapshot_at": RECENT_SNAPSHOT_AT,
        })
        logger_database_patch = patch.object(detection_logger, "PATENTS_DB_FILE", self.database_path)
        logger_database_patch.start()
        self.addCleanup(logger_database_patch.stop)
        collection_database_patch = patch.object(collect_fees, "PATENTS_DB_FILE", self.database_path)
        collection_database_patch.start()
        self.addCleanup(collection_database_patch.stop)
        self.logger = DetectionLogger(str(self.temporary_path / "detections.jsonl"))

    def test_old_fee_records_do_not_override_current_snapshot_in_logger_paths(self):
        for write_record in (
            self.logger.add_record,
            self.logger.upsert_record,
            lambda record: self.logger.add_records([record]),
        ):
            with self.subTest(write_record=write_record):
                write_record(DetectionRecord(
                    application_no=APPLICATION_NO,
                    status_code=200,
                    anjianywzt="等待发明专利证书公告",
                    payable_fee_records=[{"yingjiaoje": 100}],
                    late_fee_schedule_records=[],
                    paid_fee_records=[],
                    fee_receipt_dispatch_records=[],
                    fee_snapshot_at=OLDER_SNAPSHOT_AT,
                ))

                stored_patent = self.database.get_record(APPLICATION_NO)
                self.assertEqual(stored_patent["fee_snapshot_at"], RECENT_SNAPSHOT_AT)
                self.assertEqual(stored_patent["payable_fee_records"], [{"yingjiaoje": 900}])
                self.assertEqual(stored_patent["late_fee_schedule_records"], [{"zhinajzj": 9}])
                self.assertEqual(stored_patent["paid_fee_records"], [{"yijiaofje": 600}])
                self.assertEqual(stored_patent["fee_receipt_dispatch_records"], [{"shoujufwsjh": "recent"}])
                self.assertEqual(stored_patent["anjianywzt"], "等待发明专利证书公告")

    def test_newer_partial_logger_snapshot_clears_fields_from_previous_snapshot(self):
        self.logger.upsert_record(DetectionRecord(
            application_no=APPLICATION_NO,
            payable_fee_records=[],
            fee_snapshot_at=NEWER_SNAPSHOT_AT,
        ))

        stored_patent = self.database.get_record(APPLICATION_NO)
        self.assertEqual(stored_patent["payable_fee_records"], [])
        self.assertIsNone(stored_patent["late_fee_schedule_records"])
        self.assertIsNone(stored_patent["paid_fee_records"])
        self.assertIsNone(stored_patent["fee_receipt_dispatch_records"])
        self.assertEqual(stored_patent["fee_snapshot_at"], NEWER_SNAPSHOT_AT)

    def test_base_record_without_fee_fields_preserves_current_snapshot(self):
        self.logger.upsert_record(DetectionRecord(
            application_no=APPLICATION_NO,
            status_code=200,
            anjianywzt="等待发明专利证书公告",
        ))

        stored_patent = self.database.get_record(APPLICATION_NO)
        self.assertEqual(stored_patent["fee_snapshot_at"], RECENT_SNAPSHOT_AT)
        self.assertEqual(stored_patent["payable_fee_records"], [{"yingjiaoje": 900}])
        self.assertEqual(stored_patent["late_fee_schedule_records"], [{"zhinajzj": 9}])

    def test_same_snapshot_can_fill_missing_sections_without_erasing_other_sections(self):
        self.logger.upsert_record(DetectionRecord(
            application_no=APPLICATION_NO,
            paid_fee_records=[{"yijiaofje": 1200}],
            fee_snapshot_at=RECENT_SNAPSHOT_AT,
        ))

        stored_patent = self.database.get_record(APPLICATION_NO)
        self.assertEqual(stored_patent["payable_fee_records"], [{"yingjiaoje": 900}])
        self.assertEqual(stored_patent["late_fee_schedule_records"], [{"zhinajzj": 9}])
        self.assertEqual(stored_patent["paid_fee_records"], [{"yijiaofje": 1200}])

    def test_unversioned_fee_record_does_not_replace_a_versioned_snapshot(self):
        self.logger.upsert_record(DetectionRecord(
            application_no=APPLICATION_NO,
            payable_fee_records=[{"yingjiaoje": 100}],
            paid_fee_records=[],
        ))

        stored_patent = self.database.get_record(APPLICATION_NO)
        self.assertEqual(stored_patent["fee_snapshot_at"], RECENT_SNAPSHOT_AT)
        self.assertEqual(stored_patent["payable_fee_records"], [{"yingjiaoje": 900}])
        self.assertEqual(stored_patent["paid_fee_records"], [{"yijiaofje": 600}])

    def test_snapshot_order_preserves_microsecond_precision(self):
        self.logger.upsert_record(DetectionRecord(
            application_no=APPLICATION_NO,
            payable_fee_records=[{"yingjiaoje": 100}],
            fee_snapshot_at="2026-09-06T01:00:00.123455Z",
        ))
        self.assertEqual(
            self.database.get_record(APPLICATION_NO)["payable_fee_records"],
            [{"yingjiaoje": 900}],
        )

        self.logger.upsert_record(DetectionRecord(
            application_no=APPLICATION_NO,
            payable_fee_records=[],
            fee_snapshot_at="2026-09-06T01:00:00.123457Z",
        ))
        self.assertEqual(self.database.get_record(APPLICATION_NO)["payable_fee_records"], [])

    def test_equivalent_timezone_timestamps_belong_to_same_snapshot(self):
        self.logger.upsert_record(DetectionRecord(
            application_no=APPLICATION_NO,
            paid_fee_records=[],
            fee_snapshot_at="2026-09-06T09:00:00.123456+08:00",
        ))

        stored_patent = self.database.get_record(APPLICATION_NO)
        self.assertEqual(stored_patent["payable_fee_records"], [{"yingjiaoje": 900}])
        self.assertEqual(stored_patent["late_fee_schedule_records"], [{"zhinajzj": 9}])
        self.assertEqual(stored_patent["paid_fee_records"], [])

    def test_invalid_snapshot_time_does_not_override_a_known_version(self):
        self.logger.upsert_record(DetectionRecord(
            application_no=APPLICATION_NO,
            payable_fee_records=[],
            fee_snapshot_at="invalid-timestamp",
        ))

        stored_patent = self.database.get_record(APPLICATION_NO)
        self.assertEqual(stored_patent["fee_snapshot_at"], RECENT_SNAPSHOT_AT)
        self.assertEqual(stored_patent["payable_fee_records"], [{"yingjiaoje": 900}])

    def test_fee_snapshot_write_does_not_create_an_unregistered_patent(self):
        stored_snapshot = self.database.update_fee_snapshot("2024100659780", {
            "payable_fee_records": [],
            "fee_snapshot_at": NEWER_SNAPSHOT_AT,
        })

        self.assertIsNone(stored_snapshot)
        self.assertIsNone(self.database.get_record("2024100659780"))

    def test_legacy_partial_fields_can_fill_an_unversioned_patent(self):
        self.database.upsert({"application_no": "2024100659780"})
        stored_snapshot = self.database.update_fee_snapshot("2024100659780", {"paid_fee_records": []})
        self.assertEqual(stored_snapshot["paid_fee_records"], [])

        stored_patent = self.database.get_record("2024100659780")
        self.assertEqual(stored_patent["paid_fee_records"], [])
        self.assertIsNone(stored_patent["fee_snapshot_at"])

    def test_collection_replaces_missing_optional_section_without_touching_status(self):
        self.assertTrue(collect_fees.persist_fee_fields(APPLICATION_NO, {
            "payable_fee_records": [],
            "paid_fee_records": [],
            "fee_receipt_dispatch_records": [],
            "fee_snapshot_at": NEWER_SNAPSHOT_AT,
        }))

        stored_patent = self.database.get_record(APPLICATION_NO)
        self.assertIsNone(stored_patent["late_fee_schedule_records"])
        self.assertEqual(stored_patent["fee_snapshot_at"], NEWER_SNAPSHOT_AT)
        self.assertEqual(stored_patent["timestamp"], ORIGINAL_STATUS_TIMESTAMP)
        self.assertEqual(stored_patent["anjianywzt"], "等待实审提案")
        self.assertEqual(stored_patent["error_message"], "retained status error")

    def test_collection_ignores_old_snapshot_without_reporting_persistence_failure(self):
        self.assertTrue(collect_fees.persist_fee_fields(APPLICATION_NO, {
            "payable_fee_records": [{"yingjiaoje": 100}],
            "paid_fee_records": [],
            "fee_receipt_dispatch_records": [],
            "fee_snapshot_at": OLDER_SNAPSHOT_AT,
        }))

        stored_patent = self.database.get_record(APPLICATION_NO)
        self.assertEqual(stored_patent["payable_fee_records"], [{"yingjiaoje": 900}])
        self.assertEqual(stored_patent["fee_snapshot_at"], RECENT_SNAPSHOT_AT)

    def test_collection_without_version_cannot_replace_known_snapshot_fields(self):
        self.assertTrue(collect_fees.persist_fee_fields(APPLICATION_NO, {"paid_fee_records": []}))

        stored_patent = self.database.get_record(APPLICATION_NO)
        self.assertEqual(stored_patent["paid_fee_records"], [{"yijiaofje": 600}])
        self.assertEqual(stored_patent["fee_snapshot_at"], RECENT_SNAPSHOT_AT)

    def _run_collection_for_payload(self, fee_payload):
        with (
            patch.object(collect_fees, "load_fee_dataset_targets", return_value=[APPLICATION_NO]),
            patch.object(collect_fees, "CoordinateService") as coordinate_service,
            patch.object(collect_fees, "BrowserService"),
            patch.object(collect_fees, "countdown"),
            patch.object(collect_fees, "is_browser_alive", return_value=True),
            patch.object(collect_fees, "collect_one_fee", return_value=fee_payload),
            patch.object(collect_fees, "PatentsDB", return_value=self.database),
            patch.object(collect_fees, "DetectionLogger"),
            patch.object(self.database, "export_to_jsonl", return_value=1),
        ):
            coordinate_service.load_or_record_search_coordinates.return_value = (1, 2, 3, 4)
            coordinate_service.load_or_record_detail_link_coordinates.return_value = (5, 6)
            collect_fees._run_fee_collection(Namespace(
                test=None, input=None, app=None, force=False, url="https://example.invalid",
            ))

    def test_old_complete_payload_cannot_clear_failure_for_retained_incomplete_snapshot(self):
        self.database.update_fields(APPLICATION_NO, {
            "paid_fee_records": None,
            "fee_receipt_dispatch_records": None,
        })
        self.database.record_collection_failure("fees", APPLICATION_NO, "incomplete_fee_payload")

        self._run_collection_for_payload({
            "payable_fee_records": [],
            "paid_fee_records": [],
            "fee_receipt_dispatch_records": [],
            "fee_snapshot_at": OLDER_SNAPSHOT_AT,
        })

        stored_patent = self.database.get_record(APPLICATION_NO)
        self.assertEqual(stored_patent["fee_snapshot_at"], RECENT_SNAPSHOT_AT)
        self.assertIsNone(stored_patent["paid_fee_records"])
        failures = self.database.failed_collection_targets("fees")
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0]["reason"], "incomplete_fee_payload")
        self.assertEqual(failures[0]["attempt_count"], 2)

    def test_old_partial_payload_uses_retained_complete_snapshot_to_clear_failure(self):
        self.database.record_collection_failure("fees", APPLICATION_NO, "no_fee_payload")

        self._run_collection_for_payload({
            "payable_fee_records": [],
            "fee_snapshot_at": OLDER_SNAPSHOT_AT,
        })

        self.assertEqual(self.database.failed_collection_targets("fees"), [])
        self.assertEqual(self.database.get_record(APPLICATION_NO)["fee_snapshot_at"], RECENT_SNAPSHOT_AT)


if __name__ == "__main__":
    unittest.main()
