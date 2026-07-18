"""Build payment-deadline and late-fee analyses from decoded patent records."""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Iterable


PAYABLE_FEE_FIELDS = (
    "yingjiaoffyzlmc",
    "yingjiaoje",
    "jiaofeijzr",
    "yingjiaoffyzt",
)
LATE_FEE_FIELDS = (
    "zhinajjfsj",
    "zhinajdqnfje",
    "zhinajyjznje",
    "zhinajzj",
)
PATENT_IDENTITY_FIELDS = (
    "application_no",
    "zhuanlimc",
    "shenqingrxm",
    "zhuanlilx",
    "shenqingr",
    "gongkaiggh",
    "shouquanggh",
    "falvzt",
    "anjianywzt",
)

LATE_FEE_APPLICABLE = "applicable"
LATE_FEE_NOT_COLLECTED = "not_collected"
LATE_FEE_NO_SCHEDULE = "no_schedule"
LATE_FEE_NO_APPLICABLE_BRACKET = "no_applicable_bracket"
LATE_FEE_INVALID_INTERVAL = "invalid_interval"
LATE_FEE_MULTIPLE_APPLICABLE_BRACKETS = "multiple_applicable_brackets"

_CHINESE_INTERVAL = re.compile(
    r"^\s*(\d{4})年(\d{1,2})月(\d{1,2})日\s*[到至]\s*"
    r"(\d{4})年(\d{1,2})月(\d{1,2})日\s*$"
)
_ISO_INTERVAL = re.compile(
    r"^\s*(\d{4}-\d{2}-\d{2})\s*[到至]\s*(\d{4}-\d{2}-\d{2})\s*$"
)


def build_payable_fee_analysis(records: Iterable[dict], as_of: date) -> list[dict]:
    """Return one deadline-analysis row for each fee whose status is exactly ``未缴``."""
    _require_plain_date(as_of)

    obligations: list[tuple[tuple, dict]] = []
    for patent_record in records:
        payable_fees = patent_record.get("payable_fee_records")
        if payable_fees is None:
            continue

        for payable_fee in payable_fees:
            if payable_fee.get("yingjiaoffyzt") != "未缴":
                continue

            deadline = _parse_deadline(payable_fee.get("jiaofeijzr"))
            days_to_deadline = None if deadline is None else (deadline - as_of).days
            obligation = _patent_identity(patent_record)
            obligation.update({field: payable_fee.get(field) for field in PAYABLE_FEE_FIELDS})
            obligation.update({
                "analysis_date": as_of.isoformat(),
                "days_to_deadline": days_to_deadline,
                "deadline_bucket": _deadline_bucket(days_to_deadline),
                "fee_snapshot_at": patent_record.get("fee_snapshot_at"),
            })
            sort_key = (
                deadline is None,
                deadline or date.max,
                str(patent_record.get("application_no") or ""),
                str(payable_fee.get("yingjiaoffyzlmc") or ""),
                str(payable_fee.get("yingjiaoje") or ""),
            )
            obligations.append((sort_key, obligation))

    obligations.sort(key=lambda indexed_obligation: indexed_obligation[0])
    return [obligation for _, obligation in obligations]


def build_current_late_fee_analysis(records: Iterable[dict], as_of: date) -> list[dict]:
    """Return the one late-fee bracket applicable to ``as_of`` for each patent.

    Date intervals are inclusive. A patent with overlapping applicable brackets is
    reported as ambiguous instead of selecting or summing those brackets.
    """
    _require_plain_date(as_of)

    analyses = [
        _current_late_fee_for_patent(patent_record, as_of)
        for patent_record in records
    ]
    analyses.sort(
        key=lambda analysis: (
            str(analysis.get("application_no") or ""),
            str(analysis.get("fee_snapshot_at") or ""),
        )
    )
    return analyses


def _current_late_fee_for_patent(patent_record: dict, as_of: date) -> dict:
    analysis = _patent_identity(patent_record)
    analysis.update({field: None for field in LATE_FEE_FIELDS})
    analysis.update({
        "analysis_date": as_of.isoformat(),
        "late_fee_analysis_status": None,
        "interval_start": None,
        "interval_end": None,
        "invalid_interval_count": 0,
        "applicable_bracket_count": 0,
        "fee_snapshot_at": patent_record.get("fee_snapshot_at"),
    })

    schedules = patent_record.get("late_fee_schedule_records")
    if schedules is None:
        analysis["late_fee_analysis_status"] = LATE_FEE_NOT_COLLECTED
        return analysis
    if schedules == []:
        analysis["late_fee_analysis_status"] = LATE_FEE_NO_SCHEDULE
        return analysis

    applicable_schedules = []
    invalid_interval_count = 0
    for schedule in schedules:
        if not isinstance(schedule, dict):
            invalid_interval_count += 1
            continue
        interval = _parse_late_fee_interval(schedule.get("zhinajjfsj"))
        if interval is None:
            invalid_interval_count += 1
            continue
        interval_start, interval_end = interval
        if interval_start <= as_of <= interval_end:
            applicable_schedules.append((schedule, interval_start, interval_end))

    analysis["invalid_interval_count"] = invalid_interval_count
    analysis["applicable_bracket_count"] = len(applicable_schedules)
    if len(applicable_schedules) > 1:
        analysis["late_fee_analysis_status"] = LATE_FEE_MULTIPLE_APPLICABLE_BRACKETS
        return analysis
    if len(applicable_schedules) == 1:
        applicable_schedule, interval_start, interval_end = applicable_schedules[0]
        analysis["late_fee_analysis_status"] = LATE_FEE_APPLICABLE
        analysis.update({
            field: applicable_schedule.get(field)
            for field in LATE_FEE_FIELDS
        })
        analysis["interval_start"] = interval_start.isoformat()
        analysis["interval_end"] = interval_end.isoformat()
        return analysis

    analysis["late_fee_analysis_status"] = (
        LATE_FEE_INVALID_INTERVAL
        if invalid_interval_count
        else LATE_FEE_NO_APPLICABLE_BRACKET
    )
    return analysis


def _patent_identity(patent_record: dict) -> dict:
    return {field: patent_record.get(field) for field in PATENT_IDENTITY_FIELDS}


def _require_plain_date(as_of: date) -> None:
    if not isinstance(as_of, date) or isinstance(as_of, datetime):
        raise TypeError("as_of must be a datetime.date")


def _parse_deadline(raw_deadline: object) -> date | None:
    if isinstance(raw_deadline, date) and not isinstance(raw_deadline, datetime):
        return raw_deadline
    if not isinstance(raw_deadline, str):
        return None
    try:
        return date.fromisoformat(raw_deadline.strip())
    except ValueError:
        return None


def _deadline_bucket(days_to_deadline: int | None) -> str:
    if days_to_deadline is None:
        return "日期未知"
    if days_to_deadline < 0:
        return "已逾期"
    if days_to_deadline == 0:
        return "今日截止"
    if days_to_deadline <= 30:
        return "30天内"
    return "未来"


def _parse_late_fee_interval(raw_interval: object) -> tuple[date, date] | None:
    if not isinstance(raw_interval, str):
        return None

    chinese_match = _CHINESE_INTERVAL.fullmatch(raw_interval)
    try:
        if chinese_match:
            values = [int(part) for part in chinese_match.groups()]
            interval_start = date(*values[:3])
            interval_end = date(*values[3:])
        else:
            iso_match = _ISO_INTERVAL.fullmatch(raw_interval)
            if not iso_match:
                return None
            interval_start = date.fromisoformat(iso_match.group(1))
            interval_end = date.fromisoformat(iso_match.group(2))
    except ValueError:
        return None

    if interval_start > interval_end:
        return None
    return interval_start, interval_end


__all__ = [
    "LATE_FEE_APPLICABLE",
    "LATE_FEE_INVALID_INTERVAL",
    "LATE_FEE_MULTIPLE_APPLICABLE_BRACKETS",
    "LATE_FEE_NOT_COLLECTED",
    "LATE_FEE_NO_APPLICABLE_BRACKET",
    "LATE_FEE_NO_SCHEDULE",
    "build_current_late_fee_analysis",
    "build_payable_fee_analysis",
]
