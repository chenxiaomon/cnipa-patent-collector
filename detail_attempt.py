"""Bind one detail-page collection to the identity returned by CNIPA sqxx."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from atomic_write import write_json_atomic
from cache_utils import normalize_app_no, parse_timestamp, poll_cache_for_key, read_json_cache
from settings import MARKER_FILE, PATENT_DETAIL_IDENTITY_CACHE_FILE, FWXX_CACHE_POLL_TIMEOUT


_DETAIL_ATTEMPT_LIFETIME = timedelta(minutes=5)


class DetailCollectionFatalError(RuntimeError):
    """The browser no longer has a verified, isolated detail-page lifecycle."""


def begin_detail_attempt(application_no: str) -> dict:
    normalized_app_no = normalize_app_no(application_no)
    if not normalized_app_no:
        raise ValueError("详情采集申请号不能为空")
    attempt = {
        "application_no": normalized_app_no,
        "attempt_id": uuid4().hex,
        "started_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    write_json_atomic(MARKER_FILE, attempt)
    return attempt


def read_detail_attempt_marker() -> dict | None:
    """Validate the collector-owned marker at the cross-process boundary."""
    marker = read_json_cache(str(MARKER_FILE))
    if not isinstance(marker, dict):
        return None
    application_no = normalize_app_no(marker.get("application_no"))
    attempt_id = str(marker.get("attempt_id") or "").strip()
    started_at = marker.get("started_at")
    started_timestamp = parse_timestamp(started_at)
    if not application_no or not attempt_id or started_timestamp is None:
        return None
    # A forced Windows process-tree termination can skip collector cleanup.
    if not timedelta(0) <= datetime.now(timezone.utc) - started_timestamp <= _DETAIL_ATTEMPT_LIFETIME:
        return None
    return {
        "application_no": application_no,
        "attempt_id": attempt_id,
        "started_at": str(started_at),
    }


def clear_matching_detail_attempt(attempt_id: str) -> None:
    current_attempt = read_detail_attempt_marker()
    if current_attempt is not None and current_attempt["attempt_id"] == attempt_id:
        write_json_atomic(MARKER_FILE, {})


def publish_detail_identity(bound_attempt: dict, application_no: str) -> None:
    """MITM alone writes confirmations; old nonces never confirm a new collector attempt."""
    current_attempt = read_detail_attempt_marker()
    if current_attempt != bound_attempt:
        return
    write_json_atomic(PATENT_DETAIL_IDENTITY_CACHE_FILE, {
        bound_attempt["attempt_id"]: {"application_no": application_no},
    })


def _is_detail_identity(identity: object) -> bool:
    return (
        isinstance(identity, dict)
        and isinstance(identity.get("application_no"), str)
        and bool(identity["application_no"])
    )


def wait_for_detail_identity(attempt: dict) -> None:
    identity = poll_cache_for_key(
        str(PATENT_DETAIL_IDENTITY_CACHE_FILE),
        attempt["attempt_id"],
        max_wait=FWXX_CACHE_POLL_TIMEOUT,
        validate=_is_detail_identity,
    )
    if identity is None:
        raise DetailCollectionFatalError("未收到当前详情页的官方申请号，已停止批次")
    if identity["application_no"] != attempt["application_no"]:
        raise DetailCollectionFatalError(
            f"详情页申请号不匹配：目标 {attempt['application_no']}，"
            f"官方返回 {identity['application_no']}，已停止批次"
        )


def read_detail_identity(attempt_id: str) -> str | None:
    identity_cache = read_json_cache(str(PATENT_DETAIL_IDENTITY_CACHE_FILE))
    identity = identity_cache.get(attempt_id) if isinstance(identity_cache, dict) else None
    if not _is_detail_identity(identity):
        return None
    return identity["application_no"]


def matches_detail_attempt(payload: object, expected_attempt_id: str) -> bool:
    """Reject delayed cached responses from any previous collection of this patent."""
    return isinstance(payload, dict) and payload.get("detail_attempt_id") == expected_attempt_id
