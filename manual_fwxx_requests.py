#!/usr/bin/env python3
"""Validate pasted patent numbers and persist one manual FWXX collection request."""

from __future__ import annotations

import os
import re
import uuid
from pathlib import Path

from cache_utils import normalize_app_no

_TARGET_SPLIT_RE = re.compile(r"[\s,;；，]+")
_NORMALIZED_TARGET_RE = re.compile(r"^[0-9]{7,15}[0-9X]$")
_RAW_TARGET_RE = re.compile(r"^(?:CN)?[0-9]+(?:\.[0-9X]|X)?$", re.IGNORECASE)


def parse_manual_fwxx_targets(raw_text: str, max_app_nos: int = 500) -> list[str]:
    """Return normalized unique application numbers or reject the whole request."""
    tokens = [token.strip() for token in _TARGET_SPLIT_RE.split(str(raw_text or "")) if token.strip()]
    if not tokens:
        raise ValueError("请输入至少一个申请号")

    targets: list[str] = []
    seen_targets: set[str] = set()
    invalid_tokens: list[str] = []
    for token in tokens:
        normalized = normalize_app_no(token)
        if (
            not _RAW_TARGET_RE.fullmatch(token)
            or not normalized
            or not _NORMALIZED_TARGET_RE.fullmatch(normalized)
        ):
            invalid_tokens.append(token[:32])
            continue
        if normalized not in seen_targets:
            seen_targets.add(normalized)
            targets.append(normalized)

    if invalid_tokens:
        sample = "、".join(invalid_tokens[:3])
        suffix = " 等" if len(invalid_tokens) > 3 else ""
        raise ValueError(f"申请号格式不正确：{sample}{suffix}")
    if len(targets) > max_app_nos:
        raise ValueError(f"单次最多允许 {max_app_nos} 个申请号，当前 {len(targets)} 个")
    return targets


def create_manual_fwxx_request(
    raw_text: str,
    request_dir: Path,
    max_app_nos: int = 500,
) -> tuple[Path, list[str]]:
    """Validate input and atomically create a unique collection-list file."""
    targets = parse_manual_fwxx_targets(raw_text, max_app_nos=max_app_nos)
    request_dir = Path(request_dir)
    request_dir.mkdir(parents=True, exist_ok=True)
    request_path = request_dir / f"manual_fwxx_{uuid.uuid4().hex}.txt"
    temporary_path = request_path.with_suffix(".tmp")
    temporary_path.write_text("\n".join(targets) + "\n", encoding="utf-8")
    os.replace(temporary_path, request_path)
    return request_path, targets
