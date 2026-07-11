#!/usr/bin/env python3
"""Atomic heartbeat and alert state for unattended collection."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from settings import ALERT_STATUS_FILE, COLLECTION_HEARTBEAT_FILE, WATCHDOG_EVENTS_FILE


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


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
