#!/usr/bin/env python3

import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import generate_code_manifest


class TestGenerateCodeManifest(unittest.TestCase):
    def test_text_hash_is_independent_of_working_tree_line_endings(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            lf_path = Path(tmpdir) / 'lf.txt'
            crlf_path = Path(tmpdir) / 'crlf.txt'
            lf_path.write_bytes(b'first\nsecond\n')
            crlf_path.write_bytes(b'first\r\nsecond\r\n')

            expected = hashlib.sha256(b'first\nsecond\n').hexdigest()
            self.assertEqual(generate_code_manifest.release_file_sha256(lf_path), expected)
            self.assertEqual(generate_code_manifest.release_file_sha256(crlf_path), expected)

    def test_binary_hash_preserves_crlf_bytes(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            binary_path = Path(tmpdir) / 'payload.bin'
            content = b'prefix\0first\r\nsecond\r\n'
            binary_path.write_bytes(content)

            self.assertEqual(
                generate_code_manifest.release_file_sha256(binary_path),
                hashlib.sha256(content).hexdigest(),
            )

    def test_release_paths_use_case_sensitive_posix_order(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            for relative_path in ('z.py', 'a.py', 'A.py'):
                (root / relative_path).write_text(relative_path, encoding='utf-8')
            git_listing = mock.Mock(stdout=b'z.py\0a.py\0A.py\0')

            with (
                mock.patch.object(generate_code_manifest, 'ROOT', root),
                mock.patch.object(generate_code_manifest.subprocess, 'run', return_value=git_listing),
            ):
                release_paths = generate_code_manifest.tracked_release_files()

            self.assertEqual(
                [path.relative_to(root).as_posix() for path in release_paths],
                ['A.py', 'a.py', 'z.py'],
            )


if __name__ == '__main__':
    unittest.main()
