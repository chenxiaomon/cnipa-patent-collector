#!/usr/bin/env python3
"""Aggregate status_code=0 collection failures by date, reason, and retry class."""

from __future__ import annotations

from collections import Counter

from db_manager import PatentsDB
from settings import PATENTS_DB_FILE

_TRANSIENT_KEYWORDS = ('timeout', '超时', 'network', '网络', 'proxy', '代理', 'browser', '浏览器', 'connection')
_PERMANENT_KEYWORDS = ('未公开', '不存在', 'not found', '无权', '申请号无效')


def normalized_failure_reason(record: dict) -> str:
    return str(record.get('error_message') or record.get('response_summary') or '无错误详情').strip()


def retry_class(reason: str) -> str:
    lowered = reason.lower()
    if any(keyword in lowered for keyword in _TRANSIENT_KEYWORDS):
        return '建议重采'
    if any(keyword in lowered for keyword in _PERMANENT_KEYWORDS):
        return '建议永久失败'
    return '需人工判断'


def failure_distribution(records: list[dict]) -> dict:
    failures = [record for record in records if record.get('status_code') == 0]
    by_date = Counter(str(record.get('timestamp') or '无时间')[:10] for record in failures)
    by_reason = Counter(normalized_failure_reason(record) for record in failures)
    by_retry_class = Counter(retry_class(normalized_failure_reason(record)) for record in failures)
    return {
        'total': len(failures),
        'by_date': by_date,
        'by_reason': by_reason,
        'by_retry_class': by_retry_class,
    }


def print_counter(title: str, counter: Counter, limit: int = 30) -> None:
    print(f'\n{title}')
    print('-' * 80)
    for label, count in counter.most_common(limit):
        print(f'{count:>6}  {label}')


def main() -> None:
    distribution = failure_distribution(PatentsDB(PATENTS_DB_FILE).get_all_records())
    print(f"status_code=0 失败总数: {distribution['total']}")
    print_counter('按重采建议', distribution['by_retry_class'])
    print_counter('按采集日期', distribution['by_date'])
    print_counter('按错误原因', distribution['by_reason'])


if __name__ == '__main__':
    main()
