"""Observe CNIPA failures without making extra requests or retaining credentials."""

import json
import logging
import re
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from urllib.parse import urlsplit
from uuid import uuid4

from atomic_write import write_json_atomic
from cache_utils import read_json_cache
from collection_health import clear_collection_alert, record_collection_alert
from settings import CNIPA_API_EVENTS_FILE, CNIPA_SESSION_FAILURE_FILE, CNIPA_SESSION_FILE


class CNIPALoginRequired(RuntimeError):
    """An observed response explicitly invalidated the current confirmed login."""


def begin_cnipa_session() -> None:
    """A confirmed login replaces the nonce, so old responses cannot stop its work."""
    session = {
        'session_id': uuid4().hex,
        'started_at': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
    }
    write_json_atomic(CNIPA_SESSION_FILE, session)
    clear_collection_alert()
    print(f"[CNIPA_SESSION_STARTED] {session['session_id']}")


def read_cnipa_session() -> dict | None:
    session = read_json_cache(str(CNIPA_SESSION_FILE))
    if (
        not isinstance(session, dict)
        or not isinstance(session.get('session_id'), str)
        or re.fullmatch(r'[0-9a-f]{32}', session['session_id']) is None
        or not isinstance(session.get('started_at'), str)
    ):
        return None
    return {'session_id': session['session_id'], 'started_at': session['started_at']}


def observe_cnipa_api_response(bound_session: dict | None, url: str, http_status: int, payload: object) -> None:
    """Persist failure evidence; only explicit login failures invalidate the bound session."""
    api_code = payload.get('code') if isinstance(payload, dict) else None
    message = str(payload.get('msg') or payload.get('message') or '') if isinstance(payload, dict) else ''
    login_required = http_status == 401 or str(api_code) == '401' or any(
        phrase in message.lower() for phrase in (
            '未登录', '登录已过期', '登录已失效', '登录失效', '登录超时',
            '登录过期', '请重新登录', '登录状态失效', 'token expired',
        )
    )
    if not login_required and http_status == 200 and (api_code is None or str(api_code) == '200'):
        return
    # Do not persist query strings, headers, response bodies or free-form messages.
    failure = {
        'timestamp': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
        'session_id': bound_session['session_id'] if bound_session else None,
        'endpoint': urlsplit(url).path,
        'http_status': http_status,
        'api_code': str(api_code) if re.fullmatch(r'-?\d{1,10}', str(api_code)) else None,
        'reason': 'login_required' if login_required else ('transport_failed' if http_status == 0 else 'api_response_failed'),
    }
    if login_required and bound_session is not None and read_cnipa_session() == bound_session:
        write_json_atomic(CNIPA_SESSION_FAILURE_FILE, failure)
    failure_text = json.dumps(failure, ensure_ascii=False)
    print(f'[CNIPA_API_FAILURE] {failure_text}')
    # Failure logging owns its bounded append stream; snapshots still use atomic writes.
    try:
        CNIPA_API_EVENTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        event_log = RotatingFileHandler(CNIPA_API_EVENTS_FILE, maxBytes=1024 * 1024, backupCount=3, encoding='utf-8')
        try:
            event_log.emit(logging.LogRecord('cnipa.api', logging.WARNING, '', 0, failure_text, (), None))
        finally:
            event_log.close()
    except OSError:
        print('[CNIPA_API_FAILURE] 接口事件文件不可写，请检查日志目录权限与剩余空间')


def raise_if_cnipa_login_required() -> None:
    session = read_cnipa_session()
    if session is None:
        return
    failure = read_json_cache(str(CNIPA_SESSION_FAILURE_FILE))
    if (
        not isinstance(failure, dict)
        or failure.get('session_id') != session['session_id']
        or failure.get('reason') != 'login_required'
    ):
        return
    details = (
        f"国知局登录已失效（HTTP {failure.get('http_status')}，接口 {failure.get('endpoint')}）；"
        '已保留未完成批次，请重新登录后续跑该批次'
    )
    print(f'[LOGIN_REQUIRED] {details}')
    record_collection_alert('login_required', details, 0)
    raise CNIPALoginRequired(details)
