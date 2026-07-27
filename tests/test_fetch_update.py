#!/usr/bin/env python3

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import fetch_update
from code_release_safety import (
    CodeReleaseVerificationError,
    latest_code_backup,
    restore_code_backup,
    sha256_bytes,
)


class TestFetchUpdate(unittest.TestCase):
    def test_verified_release_installs_and_can_rollback(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / 'project'
            backups = root / 'backups'
            root.mkdir()
            (root / 'app.py').write_bytes(b'before\n')
            (root / 'VERSION').write_bytes(b'2026.07.21\n')
            release_files = {
                'app.py': b'after\n',
                'VERSION': b'2026.07.27\n',
            }
            manifest = json.dumps({
                'manifest_version': 1,
                'files': [
                    {'path': path, 'sha256': sha256_bytes(content)}
                    for path, content in release_files.items()
                ],
            }).encode('utf-8')

            def download(path, timeout=30):
                del timeout
                return manifest if path == fetch_update.MANIFEST_NAME else release_files[path]

            with patch.object(fetch_update, 'download_release_file', side_effect=download):
                fetch_update.install_release(root, backups)
            self.assertEqual((root / 'app.py').read_bytes(), b'after\n')
            self.assertEqual((root / 'VERSION').read_bytes(), b'2026.07.27\n')

            restore_code_backup(latest_code_backup(backups), root)
            self.assertEqual((root / 'app.py').read_bytes(), b'before\n')
            self.assertEqual((root / 'VERSION').read_bytes(), b'2026.07.21\n')

    def test_hash_mismatch_restores_original_code(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / 'project'
            backups = root / 'backups'
            root.mkdir()
            (root / 'app.py').write_bytes(b'before\n')
            (root / 'VERSION').write_bytes(b'2026.07.21\n')
            release_files = {
                'app.py': b'tampered\n',
                'VERSION': b'2026.07.27\n',
            }
            manifest = json.dumps({
                'manifest_version': 1,
                'files': [
                    {'path': 'app.py', 'sha256': sha256_bytes(b'expected\n')},
                    {'path': 'VERSION', 'sha256': sha256_bytes(release_files['VERSION'])},
                ],
            }).encode('utf-8')

            def download(path, timeout=30):
                del timeout
                return manifest if path == fetch_update.MANIFEST_NAME else release_files[path]

            with patch.object(fetch_update, 'download_release_file', side_effect=download):
                with self.assertRaises(CodeReleaseVerificationError):
                    fetch_update.install_release(root, backups)
            self.assertEqual((root / 'app.py').read_bytes(), b'before\n')
            self.assertEqual((root / 'VERSION').read_bytes(), b'2026.07.21\n')

    def test_older_release_is_rejected_without_changing_installed_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / 'project'
            backups = root / 'backups'
            root.mkdir()
            (root / 'app.py').write_bytes(b'before\n')
            (root / 'VERSION').write_bytes(b'2026.07.27\n')
            release_files = {
                'app.py': b'after\n',
                'VERSION': b'2026.07.21\n',
            }
            manifest = json.dumps({
                'manifest_version': 1,
                'files': [
                    {'path': path, 'sha256': sha256_bytes(content)}
                    for path, content in release_files.items()
                ],
            }).encode('utf-8')

            def download(path, timeout=30):
                del timeout
                return manifest if path == fetch_update.MANIFEST_NAME else release_files[path]

            with patch.object(fetch_update, 'download_release_file', side_effect=download):
                with self.assertRaises(CodeReleaseVerificationError):
                    fetch_update.install_release(root, backups)

            self.assertEqual((root / 'app.py').read_bytes(), b'before\n')
            self.assertEqual((root / 'VERSION').read_bytes(), b'2026.07.27\n')

    def test_release_without_version_is_rejected_without_changing_code(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / 'project'
            backups = root / 'backups'
            root.mkdir()
            (root / 'app.py').write_bytes(b'before\n')
            (root / 'VERSION').write_bytes(b'2026.07.27\n')
            manifest = json.dumps({
                'manifest_version': 1,
                'files': [
                    {'path': 'app.py', 'sha256': sha256_bytes(b'after\n')},
                ],
            }).encode('utf-8')

            def download(path, timeout=30):
                del timeout
                return manifest if path == fetch_update.MANIFEST_NAME else b'after\n'

            with patch.object(fetch_update, 'download_release_file', side_effect=download):
                with self.assertRaises(CodeReleaseVerificationError):
                    fetch_update.install_release(root, backups)

            self.assertEqual((root / 'app.py').read_bytes(), b'before\n')
            self.assertEqual((root / 'VERSION').read_bytes(), b'2026.07.27\n')
