#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""单元测试：原子化 JSON 写入"""

import json
import tempfile
import unittest
from pathlib import Path

from atomic_write import write_json_atomic


class TestWriteJsonAtomic(unittest.TestCase):
    def test_writes_json_and_leaves_no_tmp(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "config.json"
            write_json_atomic(target, {"input_x": 100, "备注": "坐标"})

            with open(target, encoding="utf-8") as f:
                self.assertEqual(json.load(f), {"input_x": 100, "备注": "坐标"})
            self.assertEqual(list(Path(tmpdir).iterdir()), [target])

    def test_replaces_existing_content(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "config.json"
            write_json_atomic(target, {"v": 1})
            write_json_atomic(target, {"v": 2})

            with open(target, encoding="utf-8") as f:
                self.assertEqual(json.load(f), {"v": 2})


if __name__ == "__main__":
    unittest.main()
