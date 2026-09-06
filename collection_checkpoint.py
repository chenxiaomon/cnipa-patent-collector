"""Persist collection batches independently of Dashboard process lifetime."""

import copy
import errno
import json
import os
import re
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from settings import COLLECTION_BATCHES_DIR


_BATCH_ID_PATTERN = re.compile(r'^[0-9a-f]{32}$')
_COLLECTORS = {'main', 'fwxx', 'fees'}


class CollectionBatchBusyError(RuntimeError):
    """The requested batch is already owned by another collector process."""


def _batch_path(batch_id: str) -> Path:
    if not isinstance(batch_id, str) or not _BATCH_ID_PATTERN.fullmatch(batch_id):
        raise ValueError('采集批次 ID 必须为 32 位小写十六进制字符')
    return Path(COLLECTION_BATCHES_DIR) / f'{batch_id}.json'


def _batch_time() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='microseconds').replace('+00:00', 'Z')


def _new_batch(collector: str, application_nos: list[str]) -> dict:
    if collector not in _COLLECTORS:
        raise ValueError('不支持的采集批次类型')
    created_at = _batch_time()
    return {
        'id': uuid.uuid4().hex, 'collector': collector, 'status': 'pending',
        'created_at': created_at, 'updated_at': created_at,
        'items': [
            {'application_no': application_no, 'status': 'pending', 'attempt_count': 0, 'reason': ''}
            for application_no in dict.fromkeys(application_nos)
        ],
        'runs': [],
    }


def _write_batch_file(batch_file: Path, batch: dict) -> None:
    temporary_file = batch_file.with_suffix('.json.tmp')
    try:
        temporary_file.write_text(json.dumps(batch, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        with _reserve_batch_snapshot(batch_file):
            temporary_file.replace(batch_file)
    finally:
        try:
            temporary_file.unlink(missing_ok=True)
        except OSError:
            pass


def _reserve_batch_file(batch_file: Path):
    lock_stream = batch_file.with_suffix('.lock').open('a+b')
    try:
        lock_stream.seek(0, os.SEEK_END)
        if lock_stream.tell() == 0:
            lock_stream.write(b'\0')
            lock_stream.flush()
        lock_stream.seek(0)
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(lock_stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(lock_stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as error:
        lock_stream.close()
        if error.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK} or getattr(error, 'winerror', None) in {33, 36}:
            raise CollectionBatchBusyError('该批次仍在运行，不能重复续跑') from error
        raise
    return lock_stream


@contextmanager
def _reserve_batch_snapshot(batch_file: Path):
    """Windows cannot replace a snapshot while a normal Python read handle is open."""
    with batch_file.with_suffix('.snapshot.lock').open('a+b') as lock_stream:
        lock_stream.seek(0, os.SEEK_END)
        if lock_stream.tell() == 0:
            lock_stream.write(b'\0')
            lock_stream.flush()
        lock_stream.seek(0)
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(lock_stream.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(lock_stream.fileno(), fcntl.LOCK_EX)
        yield


def _recover_interrupted_batch(batch: dict) -> None:
    if batch['status'] != 'running':
        return
    reason = '采集进程已退出，未记录结束状态'
    batch['status'] = 'interrupted'
    for item in batch['items']:
        if item['status'] == 'running':
            item['status'] = 'interrupted'
            item['reason'] = reason
    if batch['runs']:
        last_run = batch['runs'][-1]
        last_run.update(status='interrupted', finished_at=None, stop_reason=reason)
        for attempt in last_run['attempts']:
            if attempt['status'] == 'running':
                attempt.update(status='interrupted', finished_at=None, reason=reason)


def _read_batch_file(batch_file: Path) -> dict:
    try:
        with _reserve_batch_snapshot(batch_file):
            snapshot_text = batch_file.read_text(encoding='utf-8')
        batch = json.loads(snapshot_text)
    except FileNotFoundError as error:
        raise ValueError('采集批次不存在') from error
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f'无法读取采集批次: {error}') from error
    if (
        not isinstance(batch, dict) or batch.get('id') != batch_file.stem
        or not isinstance(batch.get('collector'), str) or batch['collector'] not in _COLLECTORS
    ):
        raise ValueError('采集批次记录格式不正确')
    if not isinstance(batch.get('items'), list) or not isinstance(batch.get('runs'), list):
        raise ValueError('采集批次记录缺少目标或运行历史')
    try:
        if batch['status'] not in {'pending', 'running', 'completed', 'paused', 'failed', 'interrupted'}:
            raise ValueError('采集批次状态无效')
        if not all(isinstance(batch[key], str) for key in ('created_at', 'updated_at')):
            raise ValueError('采集批次时间无效')
        applications = set()
        for item in batch['items']:
            if (
                not isinstance(item['application_no'], str) or not item['application_no']
                or item['application_no'] in applications
                or item['status'] not in {'pending', 'running', 'success', 'failed', 'interrupted'}
                or type(item['attempt_count']) is not int or item['attempt_count'] < 0
                or not isinstance(item['reason'], str)
            ):
                raise ValueError('采集目标记录无效')
            applications.add(item['application_no'])
        for run in batch['runs']:
            if (
                not isinstance(run['id'], str) or not _BATCH_ID_PATTERN.fullmatch(run['id'])
                or not isinstance(run['started_at'], str)
                or not (run['finished_at'] is None or isinstance(run['finished_at'], str))
                or run['status'] not in {'running', 'completed', 'paused', 'failed', 'interrupted'}
                or type(run['selected_count']) is not int or run['selected_count'] < 0
                or not isinstance(run['attempts'], list) or not isinstance(run['stop_reason'], str)
            ):
                raise ValueError('采集运行记录无效')
            for attempt in run['attempts']:
                if (
                    attempt['application_no'] not in applications
                    or attempt['status'] not in {'running', 'success', 'failed', 'interrupted'}
                    or not isinstance(attempt['reason'], str)
                    or not isinstance(attempt['started_at'], str)
                    or not (attempt['finished_at'] is None or isinstance(attempt['finished_at'], str))
                ):
                    raise ValueError('采集尝试记录无效')
    except (KeyError, TypeError) as error:
        raise ValueError('采集批次记录格式不正确') from error
    return batch


def _batch_snapshot(batch: dict) -> dict:
    succeeded = sum(item['status'] == 'success' for item in batch['items'])
    remaining = len(batch['items']) - succeeded
    return {
        **batch,
        'total': len(batch['items']),
        'succeeded': succeeded,
        'failed': sum(item['status'] == 'failed' for item in batch['items']),
        'remaining': remaining,
        'current_application': next((item['application_no'] for item in batch['items'] if item['status'] == 'running'), None),
    }


def read_collection_batch(batch_id: str) -> dict:
    """Read one local batch; an OS lease distinguishes an active run from a crash."""
    batch_file = _batch_path(batch_id)
    if not batch_file.is_file():
        raise ValueError('采集批次不存在')
    try:
        lock_stream = _reserve_batch_file(batch_file)
    except CollectionBatchBusyError:
        return {**_batch_snapshot(_read_batch_file(batch_file)), 'resumable': False}
    try:
        batch = _read_batch_file(batch_file)
        _recover_interrupted_batch(batch)
        snapshot = _batch_snapshot(batch)
        return {**snapshot, 'resumable': snapshot['remaining'] > 0}
    finally:
        lock_stream.close()


def list_collection_batches() -> list[dict]:
    """Return recent batch summaries without importing patent or browser modules."""
    summaries = []
    for batch_file in Path(COLLECTION_BATCHES_DIR).glob('*.json'):
        try:
            batch = read_collection_batch(batch_file.stem)
        except (ValueError, OSError) as error:
            summaries.append({
                'id': batch_file.stem, 'collector': '', 'status': 'unreadable',
                'created_at': '', 'updated_at': '', 'total': 0, 'succeeded': 0,
                'failed': 0, 'remaining': 0, 'current_application': None,
                'resumable': False, 'error': str(error),
            })
            continue
        summaries.append({key: value for key, value in batch.items() if key not in {'items', 'runs'}})
    return sorted(summaries, key=lambda batch: (batch['created_at'], batch['id']), reverse=True)


class CollectionBatch:
    """Own a durable batch and its process lease for one complete collection run."""

    @classmethod
    def prepare(cls, collector: str, application_nos: list[str]) -> str:
        """Freeze targets before a supervisor launches any collection process."""
        batch = _new_batch(collector, application_nos)
        if not batch['items']:
            raise ValueError('不能准备空采集批次')
        batch_file = _batch_path(batch['id'])
        batch_file.parent.mkdir(parents=True, exist_ok=True)
        with _reserve_batch_file(batch_file):
            _write_batch_file(batch_file, batch)
        return batch['id']

    @classmethod
    @contextmanager
    def create(cls, collector: str, checkpoint_file: Path, application_nos: list[str]):
        batch = _new_batch(collector, application_nos)
        batch_file = _batch_path(batch['id'])
        batch_file.parent.mkdir(parents=True, exist_ok=True)
        with _reserve_batch_file(batch_file):
            with cls(batch_file, checkpoint_file, batch) as session:
                yield session

    @classmethod
    @contextmanager
    def resume(cls, collector: str, checkpoint_file: Path, batch_id: str):
        if collector not in _COLLECTORS:
            raise ValueError('不支持的采集批次类型')
        batch_file = _batch_path(batch_id)
        batch_file.parent.mkdir(parents=True, exist_ok=True)
        with _reserve_batch_file(batch_file):
            batch = _read_batch_file(batch_file)
            if batch['collector'] != collector:
                raise ValueError('该批次不属于当前采集器')
            _recover_interrupted_batch(batch)
            session = cls(batch_file, checkpoint_file, batch)
            if not session.remaining_count:
                raise ValueError('该批次已全部完成，无需续跑')
            with session:
                yield session

    def __init__(self, batch_file: Path, checkpoint_file: Path, batch: dict):
        self._batch_file = batch_file
        self._checkpoint_file = Path(checkpoint_file)
        self._batch = batch

    def __enter__(self):
        self._batch['status'] = 'running'
        self._batch['runs'].append({
            'id': uuid.uuid4().hex, 'started_at': _batch_time(), 'finished_at': None,
            'status': 'running', 'stop_reason': '', 'selected_count': 0, 'attempts': [],
        })
        self._save(self._batch)
        self._save_resume_list()
        return self

    @property
    def id(self) -> str:
        return self._batch_file.stem

    @property
    def remaining_count(self) -> int:
        return sum(item['status'] != 'success' for item in self._batch['items'])

    def select_pending(self, limit: int | None) -> list[str]:
        if limit is not None and limit <= 0:
            raise ValueError('--test 必须为正整数')
        application_nos = [item['application_no'] for item in self._batch['items'] if item['status'] != 'success']
        selected = application_nos[:limit]
        batch = copy.deepcopy(self._batch)
        batch['runs'][-1]['selected_count'] = len(selected)
        self._save(batch)
        return selected

    def record_started(self, application_no: str) -> None:
        batch = copy.deepcopy(self._batch)
        item = next(item for item in batch['items'] if item['application_no'] == application_no)
        item.update(status='running', reason='', attempt_count=item['attempt_count'] + 1)
        batch['runs'][-1]['attempts'].append({
            'application_no': application_no, 'status': 'running', 'reason': '',
            'started_at': _batch_time(), 'finished_at': None,
        })
        self._save(batch)

    def record_success(self, application_no: str) -> None:
        batch = copy.deepcopy(self._batch)
        item = next(item for item in batch['items'] if item['application_no'] == application_no)
        item.update(status='success', reason='')
        batch['runs'][-1]['attempts'][-1].update(status='success', finished_at=_batch_time())
        self._save(batch)
        self._save_resume_list()

    def record_failure(self, application_no: str, reason: str) -> None:
        batch = copy.deepcopy(self._batch)
        item = next(item for item in batch['items'] if item['application_no'] == application_no)
        item.update(status='failed', reason=reason)
        batch['runs'][-1]['attempts'][-1].update(status='failed', reason=reason, finished_at=_batch_time())
        self._save(batch)

    def __exit__(self, exception_type, exception, traceback):
        batch = copy.deepcopy(self._batch)
        last_run = batch['runs'][-1]
        finished_at = _batch_time()
        reason = (str(exception) or exception_type.__name__) if exception_type else ''
        if exception_type is KeyboardInterrupt:
            reason = '用户中断采集'
        elif isinstance(exception, SystemExit):
            reason = f'采集进程退出，退出码: {exception.code}'
        for item in batch['items']:
            if item['status'] == 'running':
                item.update(status='interrupted', reason=reason)
        for attempt in last_run['attempts']:
            if attempt['status'] == 'running':
                attempt.update(status='interrupted', reason=reason, finished_at=finished_at)
        failed = sum(attempt['status'] == 'failed' for attempt in last_run['attempts'])
        succeeded = sum(attempt['status'] == 'success' for attempt in last_run['attempts'])
        if exception_type:
            status = 'failed' if failed + succeeded == last_run['selected_count'] and last_run['selected_count'] else 'interrupted'
        elif not self.remaining_count:
            status = 'completed'
        elif failed:
            status = 'failed'
        else:
            status = 'paused'
        last_run.update(status=status, stop_reason=reason, finished_at=finished_at)
        batch['status'] = status
        self._save(batch)

    def _save(self, batch: dict) -> None:
        batch['updated_at'] = _batch_time()
        last_run = batch['runs'][-1]
        last_run['succeeded'] = sum(attempt['status'] == 'success' for attempt in last_run['attempts'])
        last_run['failed'] = sum(attempt['status'] == 'failed' for attempt in last_run['attempts'])
        _write_batch_file(self._batch_file, batch)
        self._batch = batch

    def _save_resume_list(self) -> None:
        pending = [item['application_no'] for item in self._batch['items'] if item['status'] != 'success']
        CollectionCheckpoint(self._checkpoint_file, pending)


class CollectionCheckpoint:
    """A failed save stops collection while retaining the previous resume list."""

    def __init__(self, checkpoint_file: Path, application_nos: list[str]):
        self._checkpoint_file = checkpoint_file
        self._pending_applications = dict.fromkeys(application_nos)
        self._save_pending(self._pending_applications)

    @property
    def remaining_count(self) -> int:
        return len(self._pending_applications)

    def record_success(self, application_no: str) -> None:
        """Remove an application only after its successful collection is committed."""
        pending_applications = self._pending_applications.copy()
        del pending_applications[application_no]
        self._save_pending(pending_applications)
        self._pending_applications = pending_applications

    def _save_pending(self, pending_applications: dict[str, None]) -> None:
        self._checkpoint_file.parent.mkdir(parents=True, exist_ok=True)
        temporary_file = self._checkpoint_file.with_suffix(
            self._checkpoint_file.suffix + '.tmp'
        )
        temporary_file.write_text(
            ''.join(f'{application_no}\n' for application_no in pending_applications),
            encoding='utf-8',
        )
        temporary_file.replace(self._checkpoint_file)
