#!/usr/bin/env python3
"""Local API token lifecycle and constant-time request authentication."""

from __future__ import annotations

import secrets
import os

from settings import API_TOKEN_FILE


def _restrict_token_permissions() -> None:
    try:
        os.chmod(API_TOKEN_FILE, 0o600)
    except OSError:
        pass


def ensure_api_token() -> str:
    if API_TOKEN_FILE.exists():
        token = API_TOKEN_FILE.read_text(encoding='utf-8').strip()
        if len(token) >= 32:
            _restrict_token_permissions()
            return token
    token = secrets.token_urlsafe(32)
    API_TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = API_TOKEN_FILE.with_suffix('.tmp')
    temporary_path.write_text(token + '\n', encoding='utf-8')
    temporary_path.replace(API_TOKEN_FILE)
    _restrict_token_permissions()
    return token


def api_token_matches(candidate: str | None) -> bool:
    if not candidate:
        return False
    expected = ensure_api_token()
    return secrets.compare_digest(candidate, expected)
