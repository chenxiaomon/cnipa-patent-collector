#!/usr/bin/env python3
"""Atomic heartbeat and alert state for unattended collection."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from settings import (
    ALERT_STATUS_FILE,
    COLLECTION_HEARTBEAT_FILE,
    WATCHDOG_EVENTS_FILE,
    WATCHDOG_FAILURE_THRESHOLD,
)


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


class CollectionFailureStreakExceeded(RuntimeError):
    """连续失败达到阈值，采集应立即停止而非继续烧完整个清单。"""


class CollectionFailureStreak:
    """跨采集脚本共用的连续失败熔断：达到阈值即记录报警并抛异常。

    连续 N 条失败几乎必然是系统性故障（坐标漂移、会话失效、代理断开），
    继续跑只会把清单烧完且每条都失败。阈值复用 WATCHDOG_FAILURE_THRESHOLD：
    watchdog 的重启判定和脚本自身的停止判定是同一个业务不变式，
    不引入第二个配置项。
    """

    def __init__(self, collector_label: str):
        self._collector_label = collector_label
        self._count = 0

    @property
    def count(self) -> int:
        return self._count

    def record_success(self) -> None:
        self._count = 0

    def record_failure(self) -> None:
        self._count += 1
        if self._count >= WATCHDOG_FAILURE_THRESHOLD:
            details = (
                f'{self._collector_label} 连续失败 {self._count} 条，已停止。'
                '请检查鼠标坐标、登录会话和 MITM 代理后重试。'
            )
            record_collection_alert('consecutive_failures', details, 0)
            raise CollectionFailureStreakExceeded(details)


def _write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(path.suffix + '.tmp')
    temporary_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary_path.replace(path)


def write_collection_start_heartbeat(total: int) -> None:
    _write_json_atomic(COLLECTION_HEARTBEAT_FILE, {
        'status': 'starting',
        'timestamp': utc_timestamp(),
        'application_no': None,
        'completed': 0,
        'total': total,
        'consecutive_failures': 0,
    })


def write_collection_progress_heartbeat(
    application_no: str,
    completed: int,
    total: int,
    consecutive_failures: int,
) -> None:
    _write_json_atomic(COLLECTION_HEARTBEAT_FILE, {
        'status': 'running',
        'timestamp': utc_timestamp(),
        'application_no': application_no,
        'completed': completed,
        'total': total,
        'consecutive_failures': consecutive_failures,
    })


def write_collection_stopped_heartbeat(completed: int, total: int) -> None:
    _write_json_atomic(COLLECTION_HEARTBEAT_FILE, {
        'status': 'stopped',
        'timestamp': utc_timestamp(),
        'application_no': None,
        'completed': completed,
        'total': total,
        'consecutive_failures': 0,
    })


def read_collection_heartbeat() -> dict | None:
    if not COLLECTION_HEARTBEAT_FILE.exists():
        return None
    try:
        return json.loads(COLLECTION_HEARTBEAT_FILE.read_text(encoding='utf-8'))
    except (json.JSONDecodeError, OSError):
        return None


def record_collection_alert(reason: str, details: str, restart_count: int) -> dict:
    payload = {
        'status': 'alert',
        'reason': reason,
        'details': details,
        'timestamp': utc_timestamp(),
        'restart_count': restart_count,
    }
    _write_json_atomic(ALERT_STATUS_FILE, payload)
    WATCHDOG_EVENTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with WATCHDOG_EVENTS_FILE.open('a', encoding='utf-8') as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + '\n')
        handle.flush()
        os.fsync(handle.fileno())
    return payload


def clear_collection_alert() -> None:
    _write_json_atomic(ALERT_STATUS_FILE, {
        'status': 'ok',
        'reason': None,
        'details': None,
        'timestamp': utc_timestamp(),
        'restart_count': 0,
    })


def read_alert_status() -> dict:
    if not ALERT_STATUS_FILE.exists():
        return {'status': 'unknown', 'reason': None, 'details': None, 'timestamp': None}
    try:
        return json.loads(ALERT_STATUS_FILE.read_text(encoding='utf-8'))
    except (json.JSONDecodeError, OSError):
        return {'status': 'invalid', 'reason': 'alert_status.json 无法解析', 'details': None, 'timestamp': None}
