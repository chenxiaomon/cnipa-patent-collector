#!/usr/bin/env python
# -*- coding: utf-8 -*-

import unittest

import settings


class TestCollectionRuntimeIgnores(unittest.TestCase):
    def test_fee_backup_and_desktop_lock_are_ignored(self):
        ignored_paths = {
            line.strip()
            for line in (settings.BASE_DIR / ".gitignore").read_text(
                encoding="utf-8"
            ).splitlines()
        }

        self.assertEqual(
            settings.FEE_UNMATCHED_FILE,
            settings.DATA_DIR / "fee_unmatched.json",
        )
        self.assertEqual(
            settings.DETAIL_COLLECTION_LOCK_FILE,
            settings.DATA_DIR / "detail_collection.lock",
        )
        self.assertIn("data/fee_unmatched.json", ignored_paths)
        self.assertIn("data/detail_collection.lock", ignored_paths)


if __name__ == "__main__":
    unittest.main()
