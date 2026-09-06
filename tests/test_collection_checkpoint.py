import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from collection_checkpoint import CollectionCheckpoint


class TestCollectionCheckpoint(unittest.TestCase):
    def test_success_removes_only_the_persisted_application(self):
        with TemporaryDirectory() as temporary_directory:
            checkpoint_file = Path(temporary_directory) / 'nested' / 'resume.txt'
            checkpoint = CollectionCheckpoint(checkpoint_file, ['A', 'B', 'C'])

            self.assertEqual(checkpoint_file.read_text(encoding='utf-8'), 'A\nB\nC\n')
            checkpoint.record_success('B')

            self.assertEqual(checkpoint_file.read_text(encoding='utf-8'), 'A\nC\n')
            self.assertEqual(checkpoint.remaining_count, 2)
            checkpoint.record_success('A')
            checkpoint.record_success('C')
            self.assertEqual(checkpoint_file.read_text(encoding='utf-8'), '')
            self.assertEqual(checkpoint.remaining_count, 0)

    def test_failed_atomic_replace_preserves_previous_pending_list(self):
        with TemporaryDirectory() as temporary_directory:
            checkpoint_file = Path(temporary_directory) / 'resume.txt'
            checkpoint = CollectionCheckpoint(checkpoint_file, ['A', 'B'])

            with patch.object(Path, 'replace', side_effect=OSError('disk unavailable')):
                with self.assertRaisesRegex(OSError, 'disk unavailable'):
                    checkpoint.record_success('A')

            self.assertEqual(checkpoint_file.read_text(encoding='utf-8'), 'A\nB\n')
            self.assertEqual(checkpoint.remaining_count, 2)
            checkpoint.record_success('A')
            self.assertEqual(checkpoint_file.read_text(encoding='utf-8'), 'B\n')


if __name__ == '__main__':
    unittest.main()
