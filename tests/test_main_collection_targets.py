import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import main_collection_targets


class TestMainCollectionTargets(unittest.TestCase):
    def test_selection_freezes_only_unprocessed_normalized_applications(self):
        with TemporaryDirectory() as temporary_directory:
            search_list = Path(temporary_directory) / 'search_list.txt'
            search_list.write_text(
                'CN202310869634.X\n202411006597.0\n202210049482X\n',
                encoding='utf-8',
            )
            with patch.object(main_collection_targets, 'SEARCH_LIST_FILE', search_list), patch.object(
                main_collection_targets, 'PatentsDB'
            ) as patents_db:
                patents_db.return_value.get_processed_app_nos.return_value = {'202310869634X'}

                targets = main_collection_targets.select_main_collection_targets()

        self.assertEqual(targets, ['2024110065970', '202210049482X'])
        patents_db.assert_called_once_with(main_collection_targets.PATENTS_DB_FILE)

    def test_missing_search_list_has_operator_facing_error(self):
        with TemporaryDirectory() as temporary_directory, patch.object(
            main_collection_targets,
            'SEARCH_LIST_FILE',
            Path(temporary_directory) / 'missing.txt',
        ):
            with self.assertRaisesRegex(ValueError, '找不到搜索列表'):
                main_collection_targets.load_search_list()


if __name__ == '__main__':
    unittest.main()
