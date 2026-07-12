#!/usr/bin/env python3

import tempfile
import unittest
from pathlib import Path

from db_manager import PatentsDB
from update_readme_stats import END_MARKER, START_MARKER, update_readme_statistics


class TestUpdateReadmeStats(unittest.TestCase):
    def test_generated_block_uses_database_statistics(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            readme_path = root / 'README.md'
            database_path = root / 'patents.db'
            readme_path.write_text(
                f'# Project\n\n{START_MARKER}\nold\n{END_MARKER}\n\nend\n',
                encoding='utf-8',
            )
            db = PatentsDB(database_path)
            db.upsert({'application_no': '2023000000001', 'status_code': 200})
            db.upsert({'application_no': '2023000000002', 'status_code': -1})
            update_readme_statistics(readme_path, database_path)
            rendered = readme_path.read_text(encoding='utf-8')
            self.assertIn('| 唯一申请号 | 2 |', rendered)
            self.assertIn('| 成功采集 | 1 |', rendered)
            self.assertIn('| 待采记录 | 1 |', rendered)
            self.assertIn('end', rendered)
