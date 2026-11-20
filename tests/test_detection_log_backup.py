import gzip
import tempfile
import unittest
from pathlib import Path

from detection_log_backup import append_backup_lines, read_backup_lines, write_backup_lines


class TestDetectionLogBackupFormat(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name)
        self.backup_path = self.root / 'detection_log.jsonl.gz'

    def test_written_lines_read_back_unchanged(self):
        lines = ['{"application_no": "2023000000001", "zhuanlimc": "一种装置"}', '', '{"application_no": "2023000000002"}']

        write_backup_lines(self.backup_path, lines)

        self.assertEqual(list(read_backup_lines(self.backup_path)), lines)

    def test_same_lines_produce_identical_bytes_with_lf_endings(self):
        other_path = self.root / 'other.jsonl.gz'
        lines = ['{"application_no": "2023000000001"}', '{"application_no": "2023000000002"}']

        write_backup_lines(self.backup_path, lines)
        write_backup_lines(other_path, lines)

        written = self.backup_path.read_bytes()
        self.assertEqual(written, other_path.read_bytes())
        self.assertEqual(written[4:8], b'\x00\x00\x00\x00')
        self.assertEqual(written[3] & 0x08, 0)
        self.assertEqual(gzip.decompress(written), ('\n'.join(lines) + '\n').encode('utf-8'))

    def test_appended_lines_follow_existing_lines(self):
        write_backup_lines(self.backup_path, ['first', 'second'])

        append_backup_lines(self.backup_path, ['third'])
        append_backup_lines(self.backup_path, (line for line in ['fourth', 'fifth']))

        self.assertEqual(
            list(read_backup_lines(self.backup_path)),
            ['first', 'second', 'third', 'fourth', 'fifth'],
        )

    def test_append_to_empty_file_starts_backup(self):
        self.backup_path.touch()

        self.assertEqual(list(read_backup_lines(self.backup_path)), [])
        append_backup_lines(self.backup_path, ['only'])

        self.assertEqual(list(read_backup_lines(self.backup_path)), ['only'])

    def test_failed_rewrite_keeps_previous_backup_and_no_temporary_file(self):
        write_backup_lines(self.backup_path, ['previous'])
        previous_bytes = self.backup_path.read_bytes()

        def interrupted_lines():
            yield 'partial'
            raise RuntimeError('export interrupted')

        with self.assertRaises(RuntimeError):
            write_backup_lines(self.backup_path, interrupted_lines())

        self.assertEqual(self.backup_path.read_bytes(), previous_bytes)
        self.assertEqual(sorted(path.name for path in self.root.iterdir()), ['detection_log.jsonl.gz'])

    def test_plain_or_truncated_file_is_rejected(self):
        write_backup_lines(self.backup_path, [f'{{"application_no": "20230000000{index:02d}"}}' for index in range(50)])
        truncated_path = self.root / 'truncated.jsonl.gz'
        truncated_path.write_bytes(self.backup_path.read_bytes()[:-12])
        plain_path = self.root / 'detection_log.jsonl'
        plain_path.write_text('{"application_no": "2023000000001"}\n', encoding='utf-8')

        for invalid_path in (plain_path, truncated_path):
            with self.subTest(invalid_path=invalid_path.name), self.assertRaisesRegex(ValueError, 'gzip'):
                list(read_backup_lines(invalid_path))


if __name__ == '__main__':
    unittest.main()
