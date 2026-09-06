#!/usr/bin/env python3

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from code_release_safety import (
    CodeReleaseVerificationError,
    create_code_backup,
    install_staged_release,
    latest_code_backup,
    restore_code_backup,
    sha256_bytes,
    validate_release_manifest,
    verify_staged_release,
)


class TestCodeReleaseSafety(unittest.TestCase):
    def test_failed_backup_is_not_published_as_the_latest_backup(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / 'project'
            backups = Path(tmpdir) / 'backups'
            root.mkdir()
            (root / 'app.py').write_bytes(b'before\n')
            completed_backup = create_code_backup(root, backups)

            with patch('code_release_safety.shutil.copy2', side_effect=OSError('disk full')):
                with self.assertRaises(OSError):
                    create_code_backup(root, backups)

            self.assertEqual(latest_code_backup(backups), completed_backup)
            self.assertEqual(list(backups.glob('code_*')), [completed_backup])

    def test_missing_backup_file_does_not_partially_restore_or_delete_installed_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / 'project'
            root.mkdir()
            (root / 'app.py').write_bytes(b'before\n')
            (root / 'z.py').write_bytes(b'before-z\n')
            backup = create_code_backup(root, Path(tmpdir) / 'backups')
            (backup / 'z.py').unlink()
            (root / 'app.py').write_bytes(b'installed\n')
            (root / 'z.py').write_bytes(b'installed-z\n')
            (root / 'new.py').write_bytes(b'new\n')

            with self.assertRaises(CodeReleaseVerificationError):
                restore_code_backup(backup, root)

            self.assertEqual((root / 'app.py').read_bytes(), b'installed\n')
            self.assertEqual((root / 'z.py').read_bytes(), b'installed-z\n')
            self.assertEqual((root / 'new.py').read_bytes(), b'new\n')

    def test_corrupted_backup_is_rejected_before_touching_installed_code(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / 'project'
            root.mkdir()
            (root / 'app.py').write_bytes(b'before\n')
            backup = create_code_backup(root, Path(tmpdir) / 'backups')
            (backup / 'app.py').write_bytes(b'corrupted\n')
            (root / 'app.py').write_bytes(b'installed\n')

            with self.assertRaises(CodeReleaseVerificationError):
                restore_code_backup(backup, root)

            self.assertEqual((root / 'app.py').read_bytes(), b'installed\n')

    def test_legacy_backup_without_hashes_is_preserved_but_not_automatically_restored(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / 'project'
            backup = Path(tmpdir) / 'legacy backup'
            root.mkdir()
            backup.mkdir()
            (root / 'app.py').write_bytes(b'installed\n')
            (backup / 'app.py').write_bytes(b'legacy\n')
            (backup / '.code_backup_index.json').write_text(
                json.dumps({'files': ['app.py']}), encoding='utf-8',
            )

            with self.assertRaisesRegex(CodeReleaseVerificationError, '哈希'):
                restore_code_backup(backup, root)

            self.assertEqual((root / 'app.py').read_bytes(), b'installed\n')
            self.assertEqual((backup / 'app.py').read_bytes(), b'legacy\n')

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

    def test_manifest_rejects_windows_aliases_for_runtime_paths(self):
        for relative_path in ('DATA/patents.db', 'data./patents.db', 'data\\patents.db', 'C:app.py'):
            with self.subTest(relative_path=relative_path):
                with self.assertRaises(CodeReleaseVerificationError):
                    validate_release_manifest({
                        'manifest_version': 1,
                        'files': [{'path': relative_path, 'sha256': sha256_bytes(b'content')}],
                    })
