import json
import multiprocessing
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import collection_checkpoint
from collection_checkpoint import (
    CollectionBatch,
    CollectionBatchBusyError,
    list_collection_batches,
    read_collection_batch,
)


def _hold_batch_after_start(batch_directory, checkpoint_file, batch_id, ready, release):
    collection_checkpoint.COLLECTION_BATCHES_DIR = Path(batch_directory)
    with CollectionBatch.resume('main', Path(checkpoint_file), batch_id) as batch:
        application_no = batch.select_pending(None)[0]
        batch.record_started(application_no)
        ready.set()
        if release.wait(15):
            batch.record_success(application_no)


def _crash_after_start(batch_directory, checkpoint_file, batch_id):
    collection_checkpoint.COLLECTION_BATCHES_DIR = Path(batch_directory)
    with CollectionBatch.resume('main', Path(checkpoint_file), batch_id) as batch:
        batch.record_started(batch.select_pending(None)[0])
        os._exit(7)


def _write_after_snapshot_reader_opens(batch_directory, checkpoint_file, batch_id, ready, write_requested, write_started, write_finished):
    collection_checkpoint.COLLECTION_BATCHES_DIR = Path(batch_directory)
    with CollectionBatch.resume('main', Path(checkpoint_file), batch_id) as batch:
        batch.record_started(batch.select_pending(None)[0])
        ready.set()
        if not write_requested.wait(15):
            raise RuntimeError('snapshot writer was not released')
        write_started.set()
        batch.record_success('A')
        write_finished.set()


def _hold_snapshot_read_handle(batch_directory, batch_id, read_opened, read_released):
    collection_checkpoint.COLLECTION_BATCHES_DIR = Path(batch_directory)

    def read_with_open_handle(snapshot_file, **kwargs):
        with snapshot_file.open('r', **kwargs) as snapshot_stream:
            read_opened.set()
            if not read_released.wait(15):
                raise RuntimeError('snapshot reader was not released')
            return snapshot_stream.read()

    with patch.object(Path, 'read_text', read_with_open_handle):
        read_collection_batch(batch_id)


class TestCollectionBatches(unittest.TestCase):
    def setUp(self):
        temporary_directory = TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        self.batch_directory = Path(temporary_directory.name) / 'batches'
        self.checkpoint_file = Path(temporary_directory.name) / 'resume.txt'
        directory_patch = patch.object(collection_checkpoint, 'COLLECTION_BATCHES_DIR', self.batch_directory)
        directory_patch.start()
        self.addCleanup(directory_patch.stop)

    def test_limited_run_is_paused_then_resume_keeps_identity_and_history(self):
        with CollectionBatch.create('main', self.checkpoint_file, ['A', 'B', 'C']) as batch:
            self.assertEqual(batch.select_pending(1), ['A'])
            batch.record_started('A')
            batch.record_success('A')
            batch_id = batch.id

        paused = read_collection_batch(batch_id)
        self.assertEqual((paused['status'], paused['total'], paused['succeeded'], paused['remaining']), ('paused', 3, 1, 2))
        self.assertEqual(paused['runs'][0]['selected_count'], 1)
        self.assertEqual(self.checkpoint_file.read_text(encoding='utf-8'), 'B\nC\n')

        with CollectionBatch.resume('main', self.checkpoint_file, batch_id) as batch:
            self.assertEqual(batch.select_pending(None), ['B', 'C'])
            for application_no in ('B', 'C'):
                batch.record_started(application_no)
                batch.record_success(application_no)

        completed = read_collection_batch(batch_id)
        self.assertEqual(completed['id'], batch_id)
        self.assertEqual((completed['status'], completed['remaining']), ('completed', 0))
        self.assertFalse(completed['resumable'])
        self.assertEqual([run['status'] for run in completed['runs']], ['paused', 'completed'])
        self.assertEqual([run['succeeded'] for run in completed['runs']], [1, 2])
        with self.assertRaisesRegex(ValueError, '全部完成'):
            with CollectionBatch.resume('main', self.checkpoint_file, batch_id):
                self.fail('completed batch was resumed')

    def test_prepare_freezes_deduplicated_targets_for_later_resume(self):
        batch_id = CollectionBatch.prepare('main', ['A', 'A', 'B'])

        prepared = read_collection_batch(batch_id)
        self.assertEqual(prepared['status'], 'pending')
        self.assertEqual([item['application_no'] for item in prepared['items']], ['A', 'B'])
        self.assertEqual(prepared['runs'], [])
        self.assertTrue(prepared['resumable'])

        with CollectionBatch.resume('main', self.checkpoint_file, batch_id) as batch:
            self.assertEqual(batch.select_pending(None), ['A', 'B'])
            for application_no in ('A', 'B'):
                batch.record_started(application_no)
                batch.record_success(application_no)

        completed = read_collection_batch(batch_id)
        self.assertEqual((completed['status'], completed['remaining']), ('completed', 0))

    def test_prepare_rejects_empty_target_list_without_creating_record(self):
        with self.assertRaisesRegex(ValueError, '空采集批次'):
            CollectionBatch.prepare('main', [])

        self.assertEqual(list(self.batch_directory.glob('*.json')), [])

    def test_failed_prepare_does_not_publish_a_resumable_batch(self):
        with patch.object(Path, 'replace', side_effect=OSError('disk full')):
            with self.assertRaisesRegex(OSError, 'disk full'):
                CollectionBatch.prepare('main', ['A'])

        self.assertEqual(list_collection_batches(), [])
        self.assertEqual(list(self.batch_directory.glob('*.json')), [])
        self.assertEqual(list(self.batch_directory.glob('*.tmp')), [])

    def test_failure_interruption_and_unattempted_targets_stay_distinct(self):
        with self.assertRaisesRegex(RuntimeError, 'browser closed'):
            with CollectionBatch.create('fees', self.checkpoint_file, ['A', 'B', 'C']) as batch:
                batch_id = batch.id
                batch.select_pending(None)
                batch.record_started('A')
                batch.record_failure('A', 'empty payload')
                batch.record_started('B')
                raise RuntimeError('browser closed')

        interrupted = read_collection_batch(batch_id)
        self.assertEqual([item['status'] for item in interrupted['items']], ['failed', 'interrupted', 'pending'])
        self.assertEqual([item['attempt_count'] for item in interrupted['items']], [1, 1, 0])
        self.assertEqual((interrupted['failed'], interrupted['remaining']), (1, 3))
        self.assertEqual(interrupted['items'][0]['reason'], 'empty payload')
        self.assertEqual(interrupted['runs'][0]['stop_reason'], 'browser closed')

        with CollectionBatch.resume('fees', self.checkpoint_file, batch_id) as batch:
            self.assertEqual(batch.select_pending(1), ['A'])
            batch.record_started('A')
            batch.record_success('A')
        resumed = read_collection_batch(batch_id)
        self.assertEqual(resumed['failed'], 0)
        self.assertEqual(resumed['items'][0]['attempt_count'], 2)
        self.assertEqual(resumed['runs'][0]['attempts'][0]['reason'], 'empty payload')

    def test_normal_failed_run_remains_failed_and_new_batch_keeps_history(self):
        with CollectionBatch.create('fwxx', self.checkpoint_file, ['A']) as first_batch:
            first_batch.select_pending(None)
            first_batch.record_started('A')
            first_batch.record_failure('A', 'not persisted')
        with CollectionBatch.create('fwxx', self.checkpoint_file, ['B']) as second_batch:
            second_batch.select_pending(None)
            second_batch.record_started('B')
            second_batch.record_success('B')
        summaries = list_collection_batches()
        self.assertEqual([batch['id'] for batch in summaries], [second_batch.id, first_batch.id])
        self.assertEqual([batch['status'] for batch in summaries], ['completed', 'failed'])
        self.assertNotIn('items', summaries[0])

    def test_current_run_is_visible_but_cannot_be_resumed(self):
        with CollectionBatch.create('main', self.checkpoint_file, ['A']) as batch:
            batch.select_pending(None)
            batch.record_started('A')
            running = read_collection_batch(batch.id)
            self.assertEqual((running['status'], running['current_application']), ('running', 'A'))
            self.assertFalse(running['resumable'])
            with self.assertRaises(CollectionBatchBusyError):
                with CollectionBatch.resume('main', self.checkpoint_file, batch.id):
                    self.fail('active batch was resumed')
            batch.record_success('A')

    def test_live_process_lease_survives_dashboard_state_loss(self):
        with CollectionBatch.create('main', self.checkpoint_file, ['A']) as batch:
            batch.select_pending(None)
        context = multiprocessing.get_context('spawn')
        ready, release = context.Event(), context.Event()
        collector = context.Process(
            target=_hold_batch_after_start,
            args=(str(self.batch_directory), str(self.checkpoint_file), batch.id, ready, release),
        )
        collector.start()
        try:
            self.assertTrue(ready.wait(10), 'collector did not acquire batch')
            self.assertFalse(read_collection_batch(batch.id)['resumable'])
            with self.assertRaises(CollectionBatchBusyError):
                with CollectionBatch.resume('main', self.checkpoint_file, batch.id):
                    self.fail('concurrent resume acquired live lease')
        finally:
            release.set()
            collector.join(10)
            if collector.is_alive():
                collector.terminate()
                collector.join(5)
        self.assertEqual(collector.exitcode, 0)
        self.assertEqual(read_collection_batch(batch.id)['status'], 'completed')

    def test_hard_exit_is_detected_and_resumed_in_same_batch(self):
        with CollectionBatch.create('main', self.checkpoint_file, ['A', 'B']) as batch:
            batch.select_pending(None)
        collector = multiprocessing.get_context('spawn').Process(
            target=_crash_after_start,
            args=(str(self.batch_directory), str(self.checkpoint_file), batch.id),
        )
        collector.start()
        collector.join(10)
        if collector.is_alive():
            collector.terminate()
            collector.join(5)
        self.assertEqual(collector.exitcode, 7)
        interrupted = read_collection_batch(batch.id)
        self.assertTrue(interrupted['resumable'])
        self.assertEqual(interrupted['status'], 'interrupted')
        self.assertEqual([item['status'] for item in interrupted['items']], ['interrupted', 'pending'])
        self.assertIsNone(interrupted['runs'][-1]['finished_at'])
        self.assertEqual(read_collection_batch(batch.id)['updated_at'], interrupted['updated_at'])
        with CollectionBatch.resume('main', self.checkpoint_file, batch.id) as resumed:
            self.assertEqual(resumed.select_pending(None), ['A', 'B'])
        self.assertEqual(read_collection_batch(batch.id)['runs'][-2]['status'], 'interrupted')

    def test_snapshot_replace_waits_for_other_process_read_handle(self):
        with CollectionBatch.create('main', self.checkpoint_file, ['A']) as batch:
            batch.select_pending(None)
        context = multiprocessing.get_context('spawn')
        writer_ready, write_requested = context.Event(), context.Event()
        write_started, write_finished = context.Event(), context.Event()
        read_opened, read_released = context.Event(), context.Event()
        writer = context.Process(
            target=_write_after_snapshot_reader_opens,
            args=(str(self.batch_directory), str(self.checkpoint_file), batch.id,
                  writer_ready, write_requested, write_started, write_finished),
        )
        reader = context.Process(
            target=_hold_snapshot_read_handle,
            args=(str(self.batch_directory), batch.id, read_opened, read_released),
        )
        writer.start()
        reader_started = False
        try:
            self.assertTrue(writer_ready.wait(10))
            reader.start()
            reader_started = True
            self.assertTrue(read_opened.wait(10))
            write_requested.set()
            self.assertTrue(write_started.wait(10))
            self.assertFalse(write_finished.wait(0.2), 'writer replaced a snapshot with an active read handle')
            read_released.set()
            self.assertTrue(write_finished.wait(10))
        finally:
            write_requested.set()
            read_released.set()
            for worker in ([writer, reader] if reader_started else [writer]):
                worker.join(10)
                if worker.is_alive():
                    worker.terminate()
                    worker.join(5)
        self.assertEqual(writer.exitcode, 0)
        self.assertEqual(reader.exitcode, 0)
        self.assertEqual(read_collection_batch(batch.id)['status'], 'completed')

    def test_corrupt_file_does_not_hide_other_batches_or_offer_resume(self):
        with CollectionBatch.create('main', self.checkpoint_file, ['A']) as batch:
            batch.select_pending(None)
        damaged = self.batch_directory / f'{"f" * 32}.json'
        damaged.write_text('{', encoding='utf-8')
        summaries = list_collection_batches()
        self.assertEqual(len(summaries), 2)
        self.assertEqual(summaries[-1]['status'], 'unreadable')
        self.assertFalse(summaries[-1]['resumable'])
        self.assertIn('error', summaries[-1])
        damaged.write_text(json.dumps({'id': damaged.stem, 'collector': 'main', 'items': [None], 'runs': []}), encoding='utf-8')
        self.assertEqual(list_collection_batches()[-1]['status'], 'unreadable')

    def test_invalid_ids_and_wrong_collector_do_not_modify_existing_batch(self):
        with CollectionBatch.create('main', self.checkpoint_file, ['A']) as batch:
            batch.select_pending(None)
        before = (self.batch_directory / f'{batch.id}.json').read_bytes()
        for batch_id in ('../secret', 'A' * 32, 'a' * 31, ['a']):
            with self.subTest(batch_id=batch_id), self.assertRaises(ValueError):
                read_collection_batch(batch_id)
        with self.assertRaisesRegex(ValueError, '不属于'):
            with CollectionBatch.resume('fees', self.checkpoint_file, batch.id):
                self.fail('wrong collector resumed')
        self.assertEqual((self.batch_directory / f'{batch.id}.json').read_bytes(), before)

    def test_failed_atomic_batch_write_keeps_target_resumable(self):
        with self.assertRaisesRegex(OSError, 'disk full'):
            with CollectionBatch.create('main', self.checkpoint_file, ['A']) as batch:
                batch.select_pending(None)
                batch.record_started('A')
                with patch.object(Path, 'replace', side_effect=OSError('disk full')):
                    batch.record_success('A')
        interrupted = read_collection_batch(batch.id)
        self.assertEqual((interrupted['succeeded'], interrupted['remaining']), (0, 1))
        self.assertEqual(self.checkpoint_file.read_text(encoding='utf-8'), 'A\n')

    def test_operator_interrupt_records_reason_and_releases_lease(self):
        with self.assertRaises(KeyboardInterrupt):
            with CollectionBatch.create('main', self.checkpoint_file, ['A']) as batch:
                batch.select_pending(None)
                batch.record_started('A')
                raise KeyboardInterrupt()
        interrupted = read_collection_batch(batch.id)
        self.assertTrue(interrupted['resumable'])
        self.assertEqual(interrupted['runs'][-1]['stop_reason'], '用户中断采集')


if __name__ == '__main__':
    unittest.main()
