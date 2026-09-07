#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""单元测试：原子化 JSON 写入"""

import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import atomic_write
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

    def test_concurrent_writers_do_not_share_a_temporary_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "cache.json"
            writers_ready = threading.Barrier(2)
            original_dump = json.dump
            failures = []

            def synchronized_dump(payload, stream, **kwargs):
                writers_ready.wait(timeout=5)
                original_dump(payload, stream, **kwargs)

            def write_payload(payload):
                try:
                    write_json_atomic(target, payload)
                except Exception as error:
                    failures.append(error)

            with patch.object(atomic_write.json, "dump", side_effect=synchronized_dump):
                writers = [
                    threading.Thread(target=write_payload, args=({"writer": writer},))
                    for writer in (1, 2)
                ]
                for writer in writers:
                    writer.start()
                for writer in writers:
                    writer.join(timeout=5)

            self.assertTrue(all(not writer.is_alive() for writer in writers))
            self.assertEqual(failures, [])
            with target.open(encoding="utf-8") as stream:
                self.assertIn(json.load(stream), ({"writer": 1}, {"writer": 2}))
            self.assertEqual(list(Path(tmpdir).iterdir()), [target])


if __name__ == "__main__":
    unittest.main()
