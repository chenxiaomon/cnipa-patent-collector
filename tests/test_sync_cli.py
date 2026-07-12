#!/usr/bin/env python3

import subprocess
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class TestSyncCli(unittest.TestCase):
    def test_legacy_directional_commands_are_rejected(self):
        for command in ('pull', 'push'):
            with self.subTest(command=command):
                completed = subprocess.run(
                    [sys.executable, str(PROJECT_ROOT / 'sync.py'), command],
                    cwd=PROJECT_ROOT,
                    text=True,
                    capture_output=True,
                    check=False,
                )
                self.assertEqual(completed.returncode, 2)
                self.assertIn('旧双向同步命令已禁用', completed.stdout)
                self.assertIn('sync_pull_from_master.py', completed.stdout)


if __name__ == '__main__':
    unittest.main()
