#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Current agency-verification attempt shared between collector and MITM."""

from datetime import datetime, timezone
from uuid import uuid4

from atomic_write import write_json_atomic
from cache_utils import normalize_app_no, parse_timestamp, read_json_cache
from settings import AGENCY_ATTEMPT_MARKER_FILE


def begin_agency_attempt(application_no: str) -> dict:
    """Atomically publish a new agency attempt and return its marker payload."""
    normalized_app_no = normalize_app_no(application_no)
    if not normalized_app_no:
        raise ValueError("代理机构复核申请号不能为空")

    marker = {
        "application_no": normalized_app_no,
        "attempt_id": uuid4().hex,
        "started_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    write_json_atomic(AGENCY_ATTEMPT_MARKER_FILE, marker)
    return marker


def read_agency_attempt_marker() -> dict | None:
    """Return the validated current marker, or None when it is absent or malformed."""
    marker = read_json_cache(str(AGENCY_ATTEMPT_MARKER_FILE))
    if not isinstance(marker, dict):
        return None

    application_no = normalize_app_no(marker.get("application_no"))
    attempt_id = str(marker.get("attempt_id") or "").strip()
    started_at = marker.get("started_at")
    if not application_no or not attempt_id or parse_timestamp(started_at) is None:
        return None

    return {
        "application_no": application_no,
        "attempt_id": attempt_id,
        "started_at": str(started_at),
    }


def clear_matching_agency_attempt(attempt_id: str) -> bool:
    """Clear the marker only while it still belongs to the given attempt."""
    current_marker = read_agency_attempt_marker()
    if current_marker is None or current_marker["attempt_id"] != attempt_id:
        return False
    write_json_atomic(AGENCY_ATTEMPT_MARKER_FILE, {})
    return True
