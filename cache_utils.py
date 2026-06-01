#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
JSON 缓存工具函数（跨脚本复用）
- 申请号规范化
- JSON 文件读写（统一容错）
- 轮询等待缓存就绪
"""

import json
import os
import time
from typing import Any, Callable, Optional, Tuple


def normalize_app_no(app_no: str) -> Optional[str]:
    """
    将申请号规范化为 API 标准格式（移除 CN 前缀和点号）

    示例：CN202310869634.X → 202310869634X
    """
    if not app_no:
        return None
    normalized = str(app_no).upper().replace('CN', '').replace('.', '')
    return normalized if normalized else None


def read_json_cache(cache_file: str) -> dict:
    """读取 JSON 缓存文件，文件不存在或格式错误时返回空字典"""
    try:
        with open(cache_file, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def write_json_cache(cache_file: str, data: dict) -> None:
    """原子写入 JSON 缓存文件（.tmp + os.replace 保证写入完整性）"""
    tmp = cache_file + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, cache_file)


def clear_cache_key(cache_file: str, key: str) -> bool:
    """
    删除缓存中指定键，防止旧数据干扰下次采集

    Returns:
        True 表示键存在并已删除；False 表示键不存在
    """
    cache = read_json_cache(cache_file)
    if key in cache:
        del cache[key]
        write_json_cache(cache_file, cache)
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
    return None, max_attempts
