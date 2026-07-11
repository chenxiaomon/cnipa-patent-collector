#!/usr/bin/env python3

import tempfile
import unittest
from pathlib import Path

from code_release_safety import (
    CodeReleaseVerificationError,
    create_code_backup,
    install_staged_release,
    restore_code_backup,
    sha256_bytes,
    validate_release_manifest,
    verify_staged_release,
)


class TestCodeReleaseSafety(unittest.TestCase):
    def test_backup_and_restore_preserve_code_without_touching_data(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / 'project'
            backups = Path(tmpdir) / 'backups'
            (root / 'data').mkdir(parents=True)
            (root / 'app.py').write_text('before\n', encoding='utf-8')
            (root / 'data' / 'patents.db').write_text('runtime-data\n', encoding='utf-8')
            backup = create_code_backup(root, backups)

            staging = Path(tmpdir) / 'staging'
            staging.mkdir()
            (staging / 'app.py').write_text('after\n', encoding='utf-8')
            (staging / 'new_code.py').write_text('new\n', encoding='utf-8')
            install_staged_release(staging, [
                {'path': 'app.py', 'sha256': sha256_bytes(b'after\n')},
                {'path': 'new_code.py', 'sha256': sha256_bytes(b'new\n')},
            ], root)
            (root / 'data' / 'patents.db').write_text('new-runtime-data\n', encoding='utf-8')
            self.assertEqual((root / 'app.py').read_text(encoding='utf-8'), 'after\n')
            restored = restore_code_backup(backup, root)

            self.assertGreaterEqual(restored, 1)
            self.assertEqual((root / 'app.py').read_text(encoding='utf-8'), 'before\n')
            self.assertFalse((root / 'new_code.py').exists())
            self.assertEqual(
                (root / 'data' / 'patents.db').read_text(encoding='utf-8'),
                'new-runtime-data\n',
            )

    def test_hash_mismatch_rejects_staged_release(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            staging = Path(tmpdir)
            (staging / 'app.py').write_bytes(b'tampered')
            with self.assertRaises(CodeReleaseVerificationError):
                verify_staged_release(staging, [{
                    'path': 'app.py',
                    'sha256': sha256_bytes(b'expected'),
                }])

    def test_manifest_rejects_data_path(self):
        payload = {
            'manifest_version': 1,
            'files': [{
                'path': 'data/patents.db',
                'sha256': sha256_bytes(b'data'),
            }],
        }
        with self.assertRaises(CodeReleaseVerificationError):
            validate_release_manifest(payload)
