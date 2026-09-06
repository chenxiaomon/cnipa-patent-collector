#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""桌面采集和代码维护共用的跨进程互斥约束。"""

import errno
import os
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO, Iterator

from settings import (
    DETAIL_COLLECTION_LOCK_FILE,
    PHASE0_BROWSER_LOCK_FILE,
    PUBLIC_BROWSER_LOCK_FILE,
    PUBLIC_PAGINATION_LOCK_FILE,
    SUPERVISED_COLLECTION_LOCK_FILE,
)


class DetailCollectionDesktopBusyError(RuntimeError):
    """另一个采集或代码维护进程正在运行。"""


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
def _reserve_operation_lock(lock_path: Path, operation_name: str) -> Iterator[None]:
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
                f"无法启动{operation_name}：另一个采集或更新/回滚任务"
                "正在运行，请等待该任务结束"
            ) from error
        yield
    finally:
        try:
            if locked:
                _unlock_first_byte(lock_stream)
        finally:
            lock_stream.close()


@contextmanager
def reserve_detail_collection_desktop(operation_name: str) -> Iterator[None]:
    """在整个详情页采集周期内独占共享桌面。"""
    with _reserve_operation_lock(Path(DETAIL_COLLECTION_LOCK_FILE), operation_name):
        yield


@contextmanager
def reserve_supervised_collection(operation_name: str) -> Iterator[None]:
    """监督进程退出前保留采集预约，包括子进程重试等待期间。"""
    with _reserve_operation_lock(Path(SUPERVISED_COLLECTION_LOCK_FILE), operation_name):
        yield


@contextmanager
def reserve_phase0_browser(operation_name: str) -> Iterator[None]:
    """保留 Phase 0 浏览器进程，阻止运行期间替换代码。"""
    with _reserve_operation_lock(Path(PHASE0_BROWSER_LOCK_FILE), operation_name):
        yield


@contextmanager
def reserve_public_browser(operation_name: str) -> Iterator[None]:
    """保留公开查询浏览器，同时允许配套翻页进程运行。"""
    with _reserve_operation_lock(Path(PUBLIC_BROWSER_LOCK_FILE), operation_name):
        yield


@contextmanager
def reserve_public_pagination(operation_name: str) -> Iterator[None]:
    """保留公开查询翻页进程，同时允许配套浏览器运行。"""
    with _reserve_operation_lock(Path(PUBLIC_PAGINATION_LOCK_FILE), operation_name):
        yield


@contextmanager
def reserve_code_maintenance(operation_name: str) -> Iterator[None]:
    """维护排除所有长时间运行的采集和浏览器入口。"""
    with reserve_supervised_collection(operation_name):
        with reserve_detail_collection_desktop(operation_name):
            with reserve_phase0_browser(operation_name):
                with reserve_public_browser(operation_name):
                    with reserve_public_pagination(operation_name):
                        yield
