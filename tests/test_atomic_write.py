#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""单元测试：原子化 JSON 写入"""

import json
import multiprocessing
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from atomic_write import write_json_atomic


def _publish_after_reader_opens(target, attempted, outcome):
    replace_file = os.replace

    def announce_replace(source, destination):
        try:
            return replace_file(source, destination)
        finally:
            attempted.set()

    try:
        with patch('atomic_write.os.replace', side_effect=announce_replace):
            write_json_atomic(target, {'response': 'received'})
        outcome.send('published')
    except OSError as error:
        outcome.send(str(error))
    finally:
        outcome.close()


def _publish_with_competing_writer(target, payload, ready, release, outcome):
    replace_file = os.replace

    def wait_before_replace(source, destination):
        ready.set()
        if not release.wait(10):
            raise RuntimeError('competing writer was not released')
        return replace_file(source, destination)

    try:
        with patch('atomic_write.os.replace', side_effect=wait_before_replace):
            write_json_atomic(target, payload)
        outcome.send('published')
    except OSError as error:
        outcome.send(str(error))
    finally:
        outcome.close()


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

    def test_serialization_failure_preserves_previous_snapshot_and_cleans_temp(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / 'cache.json'
            target.write_text('{"previous": true}', encoding='utf-8')
            with self.assertRaises(TypeError):
                write_json_atomic(target, {'invalid': object()})
            self.assertEqual(json.loads(target.read_text()), {'previous': True})
            self.assertEqual(list(Path(tmpdir).iterdir()), [target])

    def test_transient_windows_sharing_error_does_not_discard_response(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / 'cache.json'
            sharing_error = PermissionError('file is being read')
            sharing_error.winerror = 5
            replace_file = os.replace
            with patch('atomic_write.os.replace') as replace:
                def publish_after_contention(source, destination):
                    if replace.call_count == 1:
                        raise sharing_error
                    return replace_file(source, destination)

                replace.side_effect = publish_after_contention
                write_json_atomic(target, {'response': 'received'})
            self.assertEqual(json.loads(target.read_text()), {'response': 'received'})
            self.assertEqual(list(Path(tmpdir).iterdir()), [target])

    def test_persistent_access_error_is_bounded_and_preserves_previous_snapshot(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / 'cache.json'
            target.write_text('{"previous": true}', encoding='utf-8')
            sharing_error = PermissionError('file remains locked')
            sharing_error.winerror = 32
            with (
                patch('atomic_write.os.replace', side_effect=sharing_error),
                patch('time.monotonic', side_effect=[0.0, 2.0]),
            ):
                with self.assertRaises(PermissionError):
                    write_json_atomic(target, {'response': 'received'})
            self.assertEqual(json.loads(target.read_text()), {'previous': True})
            self.assertEqual(list(Path(tmpdir).iterdir()), [target])

    def test_non_sharing_error_is_reported_without_retry(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / 'cache.json'
            with (
                patch('atomic_write.os.replace', side_effect=OSError('disk error')) as replace,
                patch('time.sleep') as sleep,
            ):
                with self.assertRaisesRegex(OSError, 'disk error'):
                    write_json_atomic(target, {'response': 'received'})
            replace.assert_called_once()
            sleep.assert_not_called()
            self.assertEqual(list(Path(tmpdir).iterdir()), [])

    @unittest.skipUnless(os.name == 'nt', 'Windows rejects replacing an open read handle')
    def test_windows_reader_in_another_process_does_not_lose_response(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / 'cache.json'
            target.write_text('{}', encoding='utf-8')
            context = multiprocessing.get_context('spawn')
            attempted = context.Event()
            receiver, sender = context.Pipe(duplex=False)
            writer = context.Process(target=_publish_after_reader_opens, args=(target, attempted, sender))
            try:
                with target.open(encoding='utf-8'):
                    writer.start()
                    self.assertTrue(attempted.wait(10), 'writer never attempted publication')
                self.assertTrue(receiver.poll(10), 'writer did not finish')
                self.assertEqual(receiver.recv(), 'published')
                writer.join(10)
                self.assertEqual(writer.exitcode, 0)
                self.assertEqual(json.loads(target.read_text()), {'response': 'received'})
            finally:
                if writer.is_alive():
                    writer.terminate()
                    writer.join(10)
                receiver.close()
                sender.close()

    def test_competing_processes_publish_complete_independent_snapshots(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / 'cache.json'
            context = multiprocessing.get_context('spawn')
            release = context.Event()
            payloads = [{'writer': 1, 'text': 'a' * 10000}, {'writer': 2, 'text': 'b' * 10000}]
            writers = []
            try:
                for payload in payloads:
                    ready = context.Event()
                    receiver, sender = context.Pipe(duplex=False)
                    writer = context.Process(
                        target=_publish_with_competing_writer,
                        args=(target, payload, ready, release, sender),
                    )
                    writer.start()
                    writers.append((writer, receiver, sender))
                    self.assertTrue(ready.wait(10), 'writer never prepared its snapshot')
                release.set()
                for writer, receiver, sender in writers:
                    self.assertTrue(receiver.poll(10), 'writer did not finish')
                    self.assertEqual(receiver.recv(), 'published')
                    writer.join(10)
                    self.assertEqual(writer.exitcode, 0)
                self.assertIn(json.loads(target.read_text()), payloads)
                self.assertEqual(list(Path(tmpdir).iterdir()), [target])
            finally:
                release.set()
                for writer, receiver, sender in writers:
                    if writer.is_alive():
                        writer.terminate()
                        writer.join(10)
                    receiver.close()
                    sender.close()


if __name__ == "__main__":
    unittest.main()
