#!/usr/bin/env python3
"""Cross-platform serialization for complete file update operations."""

import errno
import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO, Iterator


_THREAD_LOCKS_GUARD = threading.Lock()
_THREAD_LOCKS_BY_PATH: dict[str, threading.RLock] = {}
_HELD_RESERVATIONS = threading.local()


def _thread_lock_for(normalized_path: str) -> threading.RLock:
    with _THREAD_LOCKS_GUARD:
        return _THREAD_LOCKS_BY_PATH.setdefault(normalized_path, threading.RLock())


def _current_thread_reservations() -> dict[str, BinaryIO]:
    reservations = getattr(_HELD_RESERVATIONS, 'streams_by_path', None)
    if reservations is None:
        reservations = {}
        _HELD_RESERVATIONS.streams_by_path = reservations
    return reservations


def _lock_first_byte(lock_stream: BinaryIO) -> None:
    lock_stream.seek(0, os.SEEK_END)
    if lock_stream.tell() == 0:
        lock_stream.write(b'\0')
        lock_stream.flush()
    lock_stream.seek(0)

    while True:
        try:
            if os.name == 'nt':
                import msvcrt

                msvcrt.locking(lock_stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(lock_stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except OSError as error:
            lock_contended = error.errno in {
                errno.EACCES,
                errno.EAGAIN,
                errno.EDEADLK,
            } or getattr(error, 'winerror', None) in {33, 36}
            if not lock_contended:
                raise
            time.sleep(0.05)


def _unlock_first_byte(lock_stream: BinaryIO) -> None:
    lock_stream.seek(0)
    if os.name == 'nt':
        import msvcrt

        msvcrt.locking(lock_stream.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(lock_stream.fileno(), fcntl.LOCK_UN)


@contextmanager
def reserve_file_update(file_path: str | Path) -> Iterator[None]:
    """Hold one update boundary for a target file across threads and processes."""
    target_path = Path(os.path.abspath(os.fspath(file_path)))
    normalized_path = os.path.normcase(str(target_path.resolve()))
    target_path.parent.mkdir(parents=True, exist_ok=True)
    with _thread_lock_for(normalized_path):
        reservations = _current_thread_reservations()
        if normalized_path in reservations:
            yield
            return
        with Path(f'{target_path}.lock').open('a+b') as lock_stream:
            _lock_first_byte(lock_stream)
            reservations[normalized_path] = lock_stream
            try:
                yield
            finally:
                del reservations[normalized_path]
                _unlock_first_byte(lock_stream)
