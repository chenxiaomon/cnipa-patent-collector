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
    try:
        heartbeat = _read_health_snapshot(COLLECTION_HEARTBEAT_FILE)
        if heartbeat.get('status') not in ('starting', 'running', 'stopped'):
            raise ValueError('Invalid heartbeat status')
        if 'application_no' not in heartbeat or not isinstance(heartbeat['application_no'], (str, type(None))):
            raise ValueError('Invalid heartbeat application number')
        for counter in ('completed', 'total', 'consecutive_failures'):
            if type(heartbeat.get(counter)) is not int or heartbeat[counter] < 0:
                raise ValueError(f'Invalid heartbeat {counter}')
        return heartbeat
    except (ValueError, OSError):
        return None


def _read_health_snapshot(path: Path) -> dict:
    snapshot = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get('timestamp'), str):
        raise ValueError('Health state must be an object with a timestamp')
    timestamp = datetime.fromisoformat(snapshot['timestamp'].replace('Z', '+00:00'))
    if timestamp.utcoffset() is None:
        raise ValueError('Health state timestamp must include a timezone')
    return snapshot


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
    try:
        alert = _read_health_snapshot(ALERT_STATUS_FILE)
        if type(alert.get('restart_count')) is not int or alert['restart_count'] < 0:
            raise ValueError('Invalid alert restart count')
        if alert.get('status') == 'alert':
            if not isinstance(alert.get('reason'), str) or not isinstance(alert.get('details'), str):
                raise ValueError('Invalid alert reason or details')
        elif alert.get('status') == 'ok':
            if alert.get('reason') is not None or alert.get('details') is not None:
                raise ValueError('Cleared alert must not contain a reason or details')
        else:
            raise ValueError('Invalid alert status')
        return alert
    except FileNotFoundError:
        return {'status': 'unknown', 'reason': None, 'details': None, 'timestamp': None}
    except (ValueError, OSError):
        return {'status': 'invalid', 'reason': 'alert_status.json 无法解析', 'details': None, 'timestamp': None}
