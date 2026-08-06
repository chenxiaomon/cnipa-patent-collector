#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""详情页采集任务共用桌面的跨进程互斥约束。"""

import errno
import os
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO, Iterator

from settings import DETAIL_COLLECTION_LOCK_FILE


class DetailCollectionDesktopBusyError(RuntimeError):
    """另一个详情页采集进程正在占用桌面。"""


def _lock_first_byte(lock_stream: BinaryIO) -> None:
    lock_stream.seek(0, os.SEEK_END)
    if lock_stream.tell() == 0:
        lock_stream.write(b"\0")
        lock_stream.flush()
    lock_stream.seek(0)

    if os.name == "nt":
        import msvcrt

        msvcrt.locking(lock_stream.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl

        fcntl.flock(lock_stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock_first_byte(lock_stream: BinaryIO) -> None:
    lock_stream.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(lock_stream.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(lock_stream.fileno(), fcntl.LOCK_UN)


def _is_lock_contention(error: OSError) -> bool:
    return error.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK} or getattr(
        error,
        "winerror",
        None,
    ) in {33, 36}


@contextmanager
def reserve_detail_collection_desktop(operation_name: str) -> Iterator[None]:
    """在整个详情页采集周期内独占共享桌面。"""
    lock_path = Path(DETAIL_COLLECTION_LOCK_FILE)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_stream = lock_path.open("a+b")
    locked = False
    try:
        try:
            _lock_first_byte(lock_stream)
            locked = True
        except OSError as error:
            if not _is_lock_contention(error):
                raise
            raise DetailCollectionDesktopBusyError(
                f"无法启动{operation_name}：另一个详情页采集任务"
                "正在占用桌面浏览器"
            ) from error
        yield
    finally:
        if locked:
            _unlock_first_byte(lock_stream)
        lock_stream.close()
