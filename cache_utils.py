#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
JSON 缓存工具函数（跨脚本复用）
- 申请号规范化
- JSON 文件读写（统一容错）
- 轮询等待缓存就绪
"""

import json
import re
import time
from datetime import datetime, timezone
from typing import Any, Callable, Optional, Tuple

from atomic_write import write_json_atomic
from file_update_lock import reserve_file_update


_APP_NO_SPLIT_RE = re.compile(r'[\s,;；，]+')
_SUPPORTED_CN_APPLICATION_NO_RE = re.compile(r'^[0-9]{7,15}[0-9X]$')
_SUPPORTED_CN_APPLICATION_NO_INPUT_RE = re.compile(
    r'^(?:CN)?[0-9]{7,15}(?:\.?[0-9X])$'
)
reserve_json_cache_updates = reserve_file_update


def normalize_app_no(app_no: str) -> Optional[str]:
    """
    将申请号规范化为 API 标准格式（移除 CN 前缀和点号）

    示例：CN202310869634.X → 202310869634X
    """
    if not app_no:
        return None
    normalized = str(app_no).strip().upper().replace('CN', '').replace('.', '')
    return normalized if normalized else None


def is_supported_cn_application_no(app_no: str) -> bool:
    """Return whether an application number belongs to the supported CN format."""
    if not app_no:
        return False
    raw_app_no = str(app_no).strip().upper()
    if not _SUPPORTED_CN_APPLICATION_NO_INPUT_RE.fullmatch(raw_app_no):
        return False
    normalized_app_no = normalize_app_no(app_no)
    return bool(
        normalized_app_no
        and _SUPPORTED_CN_APPLICATION_NO_RE.fullmatch(normalized_app_no)
    )


def split_app_no_tokens(text: str) -> list[str]:
    """Split pasted application-number text using the supported delimiters."""
    return [
        token.strip()
        for token in _APP_NO_SPLIT_RE.split(str(text))
        if token.strip()
    ]


def parse_app_no_list(text: str) -> list[str]:
    normalized_app_nos: list[str] = []
    seen_app_nos: set[str] = set()
    for raw_app_no in split_app_no_tokens(text):
        if not raw_app_no or not any(ch.isdigit() for ch in raw_app_no):
            continue
        normalized_app_no = normalize_app_no(raw_app_no)
        if (
            normalized_app_no
            and is_supported_cn_application_no(raw_app_no)
            and normalized_app_no not in seen_app_nos
        ):
            seen_app_nos.add(normalized_app_no)
            normalized_app_nos.append(normalized_app_no)
    return normalized_app_nos


def parse_timestamp(value: Any) -> Optional[datetime]:
    """
    解析 ISO 8601 时间戳，统一返回 UTC aware datetime。

    兼容 Python 3.9/3.10：'Z' 结尾替换为 '+00:00'。
    无时区信息的时间戳一律视作 UTC（DB 写入端约定）。
    返回 None 表示解析失败或输入为空。
    """
    if not value:
        return None
    try:
        ts = str(value).strip()
        if ts.upper().endswith('Z'):
            ts = ts[:-1] + '+00:00'
        parsed = datetime.fromisoformat(ts)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def read_json_cache(cache_file: str) -> dict:
    """读取 JSON 缓存文件，文件不存在或格式错误时返回空字典"""
    try:
        with open(cache_file, 'r', encoding='utf-8') as f:
            cache_entries = json.load(f)
            return cache_entries if isinstance(cache_entries, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def write_json_cache(cache_file: str, data: dict) -> None:
    """原子写入 JSON 缓存文件（.tmp + os.replace 保证写入完整性）"""
    write_json_atomic(cache_file, data)


def clear_cache_key(cache_file: str, key: str) -> bool:
    """
    删除缓存中指定键，防止旧数据干扰下次采集

    Returns:
        True 表示键存在并已删除；False 表示键不存在
    """
    with reserve_json_cache_updates(cache_file):
        cache_entries = read_json_cache(cache_file)
        if key in cache_entries:
            del cache_entries[key]
            write_json_cache(cache_file, cache_entries)
            return True
        return False


def poll_cache_for_key(
    cache_file: str,
    key: str,
    max_wait: float = 8.0,
    interval: float = 0.5,
    validate: Callable[[Any], bool] = None,
) -> Optional[Any]:
    """
    轮询缓存文件，直到找到指定 key 且通过校验函数

    Args:
        cache_file: 缓存文件路径
        key: 要查找的键（申请号）
        max_wait: 最长等待秒数
        interval: 轮询间隔秒数
        validate: 可选校验函数，返回 True 表示数据有效；为 None 时只要 key 存在即返回

    Returns:
        找到的数据；超时返回 None
    """
    deadline = time.monotonic() + max_wait
    while time.monotonic() < deadline:
        data = read_json_cache(cache_file)
        value = data.get(key)
        if value is not None:
            if validate is None or validate(value):
                return value
        time.sleep(interval)
    return None


def poll_cache_with_retry(
    cache_file: str,
    key: str,
    base_wait: float = 8.0,
    interval: float = 0.5,
    max_attempts: int = 3,
    validate: Callable[[Any], bool] = None,
    on_retry: Callable[[int], None] = None,
) -> Tuple[Optional[Any], int]:
    """
    带指数退避重试的缓存轮询。

    每次重试超时窗口翻倍（base_wait → base_wait*2 → base_wait*4），
    用于抵御短暂网络抖动导致的误报失败。

    Args:
        cache_file:    缓存文件路径
        key:           要查找的键（申请号）
        base_wait:     第一次尝试的最长等待秒数
        interval:      轮询间隔秒数
        max_attempts:  最大尝试次数（含首次）
        validate:      可选校验函数
        on_retry:      单次超时后、进入下一轮等待前调用；参数为已超时的尝试序号

    Returns:
        (数据 或 None, 实际尝试次数)
    """
    for attempt in range(1, max_attempts + 1):
        wait = base_wait * (2 ** (attempt - 1))
        result = poll_cache_for_key(cache_file, key, max_wait=wait,
                                    interval=interval, validate=validate)
        if result is not None:
            return result, attempt
        if attempt < max_attempts:
            print(f"  ⚠ MITM 轮询第 {attempt} 次超时（等待 {wait:.0f}s），重试...")
            if on_retry is not None:
                on_retry(attempt)
    return None, max_attempts
