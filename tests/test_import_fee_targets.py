#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""费用数据集导入（import_fee_targets.py）的行为契约。"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import import_fee_targets as importer


def _write_csv(directory: str, content: str, encoding: str = 'utf-8-sig') -> Path:
    csv_path = Path(directory) / 'targets.csv'
    csv_path.write_bytes(content.encode(encoding))
    return csv_path


class TestImportFeeTargets(unittest.TestCase):
    def _import_with_db(self, source: Path, dry_run: bool = False, registered=None):
        """跑导入并返回 (stats, mock_db)。"""
        with patch.object(importer, 'PatentsDB') as db_class:
            db = db_class.return_value
            db.get_all_app_nos.return_value = registered or set()
            db.replace_fee_targets.return_value = {
                'previous_count': 5, 'imported_count': 0,
            }
            db.fee_dataset_progress.return_value = {
                'total': 5, 'collected': 0, 'pending': 5, 'unregistered': 0,
            }
            stats = importer.import_fee_targets(source, dry_run=dry_run)
        return stats, db

    def test_recognizes_app_no_column_and_normalizes(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            source = _write_csv(tmpdir, '申请号\nCN202411006597.0\n202111504942X\n')
            stats, db = self._import_with_db(source)
        self.assertEqual(stats['imported'], 2)
        db.replace_fee_targets.assert_called_once_with(['2024110065970', '202111504942X'])

    def test_deduplicates_and_counts_invalid_rows(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            source = _write_csv(
                tmpdir,
                # 空行被 CSV 解析器直接跳过，不计入 invalid
                'application_no\nCN202411006597.0\n202411006597.0\n\n无效值不含数字\n',
            )
            stats, _db = self._import_with_db(source)
        self.assertEqual(stats['imported'], 1)
        self.assertEqual(stats['duplicates'], 1)
        self.assertEqual(stats['invalid'], 1)

    def test_gbk_encoded_csv_is_readable(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            source = _write_csv(tmpdir, '专利申请号\n2024110065970\n', encoding='gbk')
            stats, _db = self._import_with_db(source)
        self.assertEqual(stats['imported'], 1)

    def test_dry_run_does_not_write(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            source = _write_csv(tmpdir, '申请号\n2024110065970\n')
            stats, db = self._import_with_db(source, dry_run=True)
        db.replace_fee_targets.assert_not_called()
        self.assertEqual(stats['imported'], 1)
        self.assertEqual(stats['previous'], 5)

    def test_unregistered_app_nos_are_reported(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            source = _write_csv(tmpdir, '申请号\n2024110065970\n2029990000001\n')
            stats, _db = self._import_with_db(source, registered={'2024110065970'})
        self.assertEqual(stats['unregistered'], 1)
        self.assertEqual(stats['unregistered_sample'], ['2029990000001'])

    def test_headerless_list_counts_first_row_as_data(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            # 无表头：首行本身就是申请号，且与后面的行有重复
            source = _write_csv(tmpdir, '2019113997124\n2022206018051\n2019113997124\n')
            stats, db = self._import_with_db(source)
        self.assertEqual(stats['imported'], 2)
        self.assertEqual(stats['duplicates'], 1)
        db.replace_fee_targets.assert_called_once_with(['2019113997124', '2022206018051'])

    def test_missing_app_no_column_raises(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            source = _write_csv(tmpdir, '专利名称\n某发明\n')
            with self.assertRaisesRegex(ValueError, '未找到申请号列'):
                self._import_with_db(source)


if __name__ == '__main__':
    unittest.main()
