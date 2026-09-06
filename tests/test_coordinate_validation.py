import unittest
from unittest.mock import patch

import coordinate_service
from coordinate_config import (
    DETAIL_COORDINATE_PAIRS,
    DETAIL_LINK_COORDINATE_PAIRS,
    FEE_MENU_COORDINATE_PAIRS,
    FWXX_COORDINATE_PAIRS,
    SEARCH_COORDINATE_PAIRS,
    coordinate_configuration_issues,
    recorded_coordinates,
    validate_coordinate_config,
)


class TestCoordinateConfigurationRules(unittest.TestCase):
    def test_save_validation_accepts_empty_partial_and_screen_edge_coordinates(self):
        for coordinate_config in (
            {},
            {'input_x': 120},
            {'input_x': 0, 'input_y': 415},
            {'button_x': -200, 'button_y': 300},
            {'last_updated': '2026-09-06T12:00:00'},
        ):
            with self.subTest(coordinate_config=coordinate_config):
                validate_coordinate_config(coordinate_config)

    def test_save_validation_rejects_non_object_and_non_integer_values(self):
        invalid_configs = (
            [],
            {'input_x': True},
            {'input_y': False},
            {'button_x': '120'},
            {'link_y': 4.5},
        )
        for coordinate_config in invalid_configs:
            with self.subTest(coordinate_config=coordinate_config):
                with self.assertRaisesRegex(ValueError, 'JSON 对象|必须是整数'):
                    validate_coordinate_config(coordinate_config)

    def test_save_validation_rejects_complete_zero_pair_in_every_group(self):
        for coordinate_x, coordinate_y in SEARCH_COORDINATE_PAIRS + DETAIL_COORDINATE_PAIRS:
            with self.subTest(coordinate_x=coordinate_x):
                with self.assertRaisesRegex(ValueError, r'\(0, 0\)'):
                    validate_coordinate_config({coordinate_x: 0, coordinate_y: 0})

    def test_collection_checks_only_coordinates_needed_by_its_operation(self):
        coordinate_config = {
            'link_x': 5,
            'link_y': 6,
            'fwxx_menu_x': 'invalid but unrelated to detail-link collection',
            'fee_menu_x': 0,
            'fee_menu_y': 0,
        }

        self.assertEqual(
            recorded_coordinates(coordinate_config, DETAIL_LINK_COORDINATE_PAIRS),
            (5, 6),
        )
        self.assertEqual(
            coordinate_configuration_issues(
                coordinate_config, DETAIL_LINK_COORDINATE_PAIRS,
            ),
            [],
        )
        self.assertIsNone(recorded_coordinates(coordinate_config, FWXX_COORDINATE_PAIRS))
        self.assertIsNone(recorded_coordinates(coordinate_config, FEE_MENU_COORDINATE_PAIRS))

    def test_recorded_coordinates_require_every_selected_pair(self):
        for coordinate_config in (
            {'input_x': 10},
            {'input_x': 10, 'input_y': True},
            {'input_x': 0, 'input_y': 0},
        ):
            with self.subTest(coordinate_config=coordinate_config):
                self.assertIsNone(
                    recorded_coordinates(coordinate_config, (('input_x', 'input_y'),)),
                )


class TestRecordedCoordinatesAreValidatedBeforeSaving(unittest.TestCase):
    def _assert_invalid_recording_is_not_saved(self, recording, mouse_positions):
        with patch.object(
            coordinate_service.CoordinateService, '_countdown',
        ), patch.object(
            coordinate_service.pyautogui, 'position', side_effect=mouse_positions,
        ), patch.object(coordinate_service, 'write_json_atomic') as write_coordinates:
            with self.assertRaises(ValueError):
                recording()

        write_coordinates.assert_not_called()

    def test_search_recording_rejects_non_integer_mouse_position(self):
        self._assert_invalid_recording_is_not_saved(
            coordinate_service.CoordinateService._record_search_coordinates,
            [(True, 2), (3, 4)],
        )

    def test_detail_link_recording_rejects_zero_placeholder(self):
        self._assert_invalid_recording_is_not_saved(
            lambda: coordinate_service.CoordinateService._record_detail_link_coordinates({}),
            [(0, 0)],
        )

    def test_fwxx_recording_rejects_zero_placeholder(self):
        self._assert_invalid_recording_is_not_saved(
            lambda: coordinate_service.CoordinateService._record_fwxx_coordinates({}),
            [(5, 6), (0, 0)],
        )

    def test_fee_recording_rejects_non_integer_mouse_position(self):
        self._assert_invalid_recording_is_not_saved(
            lambda: coordinate_service.CoordinateService._record_fee_menu_coordinates({}),
            [('7', 8)],
        )

    def test_recording_checks_only_new_coordinates_for_its_operation(self):
        with patch.object(
            coordinate_service.CoordinateService, '_countdown',
        ), patch.object(
            coordinate_service.pyautogui, 'position', return_value=(5, 6),
        ), patch.object(coordinate_service, 'write_json_atomic') as write_coordinates:
            recorded = coordinate_service.CoordinateService._record_detail_link_coordinates({
                'fee_menu_x': True,
            })

        self.assertEqual(recorded, (5, 6))
        saved_coordinates = write_coordinates.call_args.args[1]
        self.assertEqual(saved_coordinates['link_x'], 5)
        self.assertEqual(saved_coordinates['link_y'], 6)
        self.assertIs(saved_coordinates['fee_menu_x'], True)


if __name__ == '__main__':
    unittest.main()
