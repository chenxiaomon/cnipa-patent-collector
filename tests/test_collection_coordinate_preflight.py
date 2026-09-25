"""Coordinate validation must finish before production browser startup."""

from __future__ import annotations

import ast
import inspect
import textwrap
import unittest
from argparse import Namespace
from unittest.mock import MagicMock, patch

import collect_agency
import collect_fees
import collect_fwxx
import main_automation
from coordinate_service import CoordinateConfigurationError


def _called_attributes(operation) -> dict[str, list[int]]:
    operation_tree = ast.parse(textwrap.dedent(inspect.getsource(operation)))
    called_attributes: dict[str, list[int]] = {}
    for node in ast.walk(operation_tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            called_attributes.setdefault(node.func.attr, []).append(node.lineno)
    return called_attributes


class TestCollectionCoordinatePreflight(unittest.TestCase):
    def test_strict_coordinates_are_loaded_before_browser_startup(self):
        entry_requirements = (
            (
                collect_fwxx._collect_fwxx_batch,
                ("load_search_coordinates", "load_fwxx_coordinates"),
            ),
            (main_automation._collect_main_batch, ("load_search_coordinates",)),
            (
                collect_fees._collect_fee_batch,
                (
                    "load_search_coordinates",
                    "load_detail_link_coordinates",
                    "load_fee_menu_coordinates",
                ),
            ),
            (
                collect_agency._run_agency_collection,
                ("load_search_coordinates", "load_detail_link_coordinates"),
            ),
        )

        for entry_operation, required_loaders in entry_requirements:
            with self.subTest(entry_operation=entry_operation.__module__):
                called_attributes = _called_attributes(entry_operation)
                browser_start_line = called_attributes["launch_and_login"][0]
                for loader_name in required_loaders:
                    self.assertIn(loader_name, called_attributes)
                    self.assertLess(called_attributes[loader_name][0], browser_start_line)
                self.assertFalse(
                    any(
                        attribute_name.startswith("load_or_record_")
                        for attribute_name in called_attributes
                    )
                )

    def test_fee_item_collection_does_not_start_coordinate_recording(self):
        called_attributes = _called_attributes(collect_fees.collect_one_fee)

        self.assertFalse(
            any(
                attribute_name.startswith("load_or_record_")
                for attribute_name in called_attributes
            )
        )

    def test_main_invalid_coordinates_stop_before_browser_or_search(self):
        checkpoint = MagicMock()
        checkpoint.select_pending.return_value = ['A']
        logger = MagicMock()
        logger.log_file = 'unused-detection-log.jsonl'
        logger.get_stats.return_value = {'total': 0}
        with (
            patch.object(main_automation, 'CoordinateService') as coordinates,
            patch.object(main_automation, 'BrowserService') as browser,
            patch.object(main_automation, 'search_application') as search,
            patch.object(main_automation, 'write_collection_start_heartbeat'),
            patch.object(main_automation, 'write_collection_stopped_heartbeat'),
            patch.object(main_automation, 'stop_virtual_display'),
        ):
            coordinates.load_search_coordinates.side_effect = CoordinateConfigurationError('calibrate search')
            with self.assertRaisesRegex(CoordinateConfigurationError, 'calibrate search'):
                main_automation._collect_main_batch(checkpoint, logger, None, None)

        browser.launch_and_login.assert_not_called()
        search.assert_not_called()
        checkpoint.record_started.assert_not_called()

    def test_detail_invalid_coordinates_stop_before_browser_or_collection(self):
        arguments = Namespace(test=None, url='https://example.invalid')
        for collector, batch_operation, required_loaders in (
            (collect_fwxx, collect_fwxx._collect_fwxx_batch, (
                'load_search_coordinates', 'load_fwxx_coordinates',
            )),
            (collect_fees, collect_fees._collect_fee_batch, (
                'load_search_coordinates', 'load_detail_link_coordinates',
                'load_fee_menu_coordinates',
            )),
        ):
            for loader_name in required_loaders:
                with self.subTest(collector=collector.__name__, loader=loader_name):
                    checkpoint = MagicMock()
                    checkpoint.select_pending.return_value = ['A']
                    with (
                        patch.object(collector, 'CoordinateService') as coordinates,
                        patch.object(collector, 'BrowserService') as browser,
                        patch.object(collector, 'InputService') as input_service,
                        patch.object(collect_fees, 'PatentsDB') as fee_database,
                    ):
                        fee_database.return_value.get_record.return_value = {'application_no': 'A'}
                        coordinates.load_search_coordinates.return_value = (1, 2, 3, 4)
                        coordinates.load_fwxx_coordinates.return_value = (5, 6, 7, 8)
                        coordinates.load_detail_link_coordinates.return_value = (5, 6)
                        coordinates.load_fee_menu_coordinates.return_value = (7, 8)
                        getattr(coordinates, loader_name).side_effect = CoordinateConfigurationError('calibrate detail')
                        with self.assertRaisesRegex(CoordinateConfigurationError, 'calibrate detail'):
                            batch_operation(arguments, checkpoint)

                    browser.launch_and_login.assert_not_called()
                    self.assertEqual(input_service.mock_calls, [])
                    checkpoint.record_started.assert_not_called()

    def test_agency_invalid_coordinates_stop_before_browser_or_collection(self):
        arguments = Namespace(test=None, url='https://example.invalid')
        for loader_name in ('load_search_coordinates', 'load_detail_link_coordinates'):
            with (
                self.subTest(loader=loader_name),
                patch.object(collect_agency, 'load_requested_targets', return_value=['A']),
                patch.object(collect_agency, 'load_resumable_verification_records', return_value=[]),
                patch.object(collect_agency, 'write_verification_reports'),
                patch.object(collect_agency, 'CoordinateService') as coordinates,
                patch.object(collect_agency, 'BrowserService') as browser,
                patch.object(collect_agency, 'collect_one_agency') as collect_one,
            ):
                coordinates.load_search_coordinates.return_value = (1, 2, 3, 4)
                coordinates.load_detail_link_coordinates.return_value = (5, 6)
                getattr(coordinates, loader_name).side_effect = CoordinateConfigurationError('calibrate agency')
                with self.assertRaisesRegex(CoordinateConfigurationError, 'calibrate agency'):
                    collect_agency._run_agency_collection(arguments)

                browser.launch_and_login.assert_not_called()
                collect_one.assert_not_called()


if __name__ == "__main__":
    unittest.main()
