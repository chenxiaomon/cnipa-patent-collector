#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Verify current patent agencies by opening CNIPA detail pages only."""

from __future__ import annotations

import argparse
import csv
import os
import random
import sys
import time
from datetime import datetime, timezone
from functools import partial
from pathlib import Path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import pyautogui

sys.path.insert(0, os.path.dirname(__file__))

from atomic_write import write_json_atomic
from agency_attempt import begin_agency_attempt, clear_matching_agency_attempt
from browser_service import BrowserService
from browser_utils import is_browser_alive
from cache_utils import parse_app_no_list, poll_cache_for_key
from coordinate_service import CoordinateService
from db_manager import PatentsDB
from desktop_collection_lock import (
    DetailCollectionDesktopBusyError,
    reserve_detail_collection_desktop,
)
from input_service import InputService
from settings import (
    AGENCY_VERIFICATION_REPORT_CSV_FILE,
    AGENCY_VERIFICATION_REPORT_JSON_FILE,
    CNIPA_URL,
    FWXX_ANTI_CRAWL_BATCH_SIZE,
    FWXX_ANTI_CRAWL_WAIT_MAX,
    FWXX_ANTI_CRAWL_WAIT_MIN,
    FWXX_CACHE_POLL_TIMEOUT,
    FWXX_DETAIL_CLICK_WAIT,
    FWXX_DETAIL_CLOSE_WAIT,
    FWXX_INPUT_DELAY_MAX,
    FWXX_INPUT_DELAY_MIN,
    FWXX_INPUT_PAUSE_PROB,
    FWXX_PAGE_LOAD_WAIT,
    FWXX_POST_SEARCH_WAIT,
    FWXX_STARTUP_COUNTDOWN,
    FWXX_TAB_SWITCH_WAIT,
    PATENT_AGENCY_CACHE_FILE,
    PATENTS_DB_FILE,
    PYAUTOGUI_FAILSAFE,
    PYAUTOGUI_PAUSE,
    USE_MITM_PROXY,
)


SEARCH_PAGE_URL = CNIPA_URL
_ACK_STATUSES = frozenset(
    {"updated", "unmatched", "official_empty", "persistence_error"}
)
_CLASSIFICATIONS = (
    "changed",
    "unchanged",
    "first_collected",
    "official_empty",
    "unmatched",
    "persistence_error",
    "timeout",
)
_REPORT_COLUMNS = (
    "application_no",
    "classification",
    "old_daili_jg",
    "official_daili_jg",
    "official_daili_r",
    "persistence_status",
    "captured_at",
)

pyautogui.PAUSE = PYAUTOGUI_PAUSE
pyautogui.FAILSAFE = PYAUTOGUI_FAILSAFE


class AgencyCollectionFatalError(RuntimeError):
    """The browser can no longer be trusted to continue the current batch."""


def countdown(seconds: int, message: str = "即将开始代理机构复核，倒计时") -> None:
    for remaining in range(seconds, 0, -1):
        print(f"\r{message}: {remaining:2d} 秒...", end="", flush=True)
        time.sleep(1)
    print(f"\r{message}: 0 秒...完成！    ")


def load_requested_targets(
    input_file: str | None = None,
    app_nos: str | None = None,
) -> list[str]:
    """Load every explicitly requested application number without resume filtering."""
    if bool(input_file) == bool(app_nos):
        raise ValueError("请且仅请使用 --input 或 --app 指定待复核申请号")

    if input_file:
        request_text = Path(input_file).read_text(encoding="utf-8-sig")
    else:
        request_text = str(app_nos)

    targets = parse_app_no_list(request_text)
    if not targets:
        raise ValueError("未读取到有效申请号")
    return targets


def _clean_optional_text(value: object) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


def _is_agency_ack(payload: object, expected_attempt_id: str) -> bool:
    """Validate the MITM acknowledgement once at the cache boundary."""
    if not isinstance(payload, dict):
        return False
    if not {
        "attempt_id",
        "captured_at",
        "daili_jg",
        "daili_r",
        "persistence_status",
    }.issubset(payload):
        return False
    if payload["attempt_id"] != expected_attempt_id:
        return False
    persistence_status = payload["persistence_status"]
    if persistence_status not in _ACK_STATUSES:
        return False
    official_agency = _clean_optional_text(payload["daili_jg"])
    if persistence_status == "updated":
        return official_agency is not None
    if persistence_status == "official_empty":
        return official_agency is None
    return True


def _page_confirms_application_no(page_source: str, application_no: str) -> bool:
    searchable_page = "".join(character for character in page_source.upper() if character.isalnum())
    return application_no in searchable_page


def _close_detail_page(driver, detail_handle: str, search_handle: str) -> None:
    if detail_handle in driver.window_handles:
        driver.switch_to.window(detail_handle)
        pyautogui.hotkey("ctrl", "w")
        time.sleep(FWXX_DETAIL_CLOSE_WAIT)

    remaining_handles = list(driver.window_handles)
    if len(remaining_handles) != 1 or remaining_handles[0] != search_handle:
        raise AgencyCollectionFatalError("详情页关闭后无法确认已恢复唯一搜索页")
    driver.switch_to.window(search_handle)
    time.sleep(FWXX_TAB_SWITCH_WAIT)


def collect_one_agency(
    driver,
    application_no: str,
    input_x: int,
    input_y: int,
    button_x: int,
    button_y: int,
    link_x: int,
    link_y: int,
) -> dict | None:
    """Search, open one detail page, await its sqxx acknowledgement, then close it."""
    agency_attempt: dict | None = None
    detail_handle: str | None = None
    search_handle: str | None = None
    try:
        if not is_browser_alive(driver):
            print("    [!] 浏览器已关闭，无法复核")
            return None

        initial_handles = list(driver.window_handles)
        if len(initial_handles) != 1:
            raise AgencyCollectionFatalError("代理机构复核开始时浏览器不是唯一搜索页")
        search_handle = initial_handles[0]
        driver.switch_to.window(search_handle)

        print("    [*] 输入申请号并查询...")
        InputService.type_in_search(
            input_x,
            input_y,
            button_x,
            button_y,
            application_no,
            delay_range=(FWXX_INPUT_DELAY_MIN, FWXX_INPUT_DELAY_MAX),
            pause_prob=FWXX_INPUT_PAUSE_PROB,
            post_search_wait=FWXX_POST_SEARCH_WAIT,
        )

        try:
            page_source = str(driver.page_source)
        except Exception as error:
            raise AgencyCollectionFatalError("无法读取搜索结果页，已停止批次") from error
        page_text = page_source.lower()
        if any(
            keyword in page_text
            for keyword in ("无查询结果", "无搜索结果", "请输入查询", "没有找到")
        ):
            print("    [!] 搜索无结果或出现异常提示")
            return None
        if not _page_confirms_application_no(page_source, application_no):
            raise AgencyCollectionFatalError(
                f"搜索结果页无法确认当前申请号 {application_no}，已停止批次"
            )

        print("    [*] 点击申请号进入详情页...")
        handles_before = set(driver.window_handles)
        agency_attempt = begin_agency_attempt(application_no)
        InputService.move_and_click(
            link_x,
            link_y,
            post_click_wait=FWXX_DETAIL_CLICK_WAIT,
        )
        new_handles = [
            handle for handle in driver.window_handles if handle not in handles_before
        ]
        if len(new_handles) != 1:
            raise AgencyCollectionFatalError("详情页未唯一打开，已停止批次")

        detail_handle = new_handles[-1]
        driver.switch_to.window(detail_handle)
        time.sleep(FWXX_TAB_SWITCH_WAIT)

        print("    [*] 等待国知局代理机构确认...")
        agency_ack = poll_cache_for_key(
            PATENT_AGENCY_CACHE_FILE,
            application_no,
            max_wait=FWXX_CACHE_POLL_TIMEOUT,
            validate=partial(
                _is_agency_ack,
                expected_attempt_id=agency_attempt["attempt_id"],
            ),
        )
        if agency_ack is None:
            print("    [!] 等待代理机构确认超时")
        else:
            print("    [✓] 已收到代理机构确认")
        return agency_ack
    except AgencyCollectionFatalError:
        raise
    except Exception as error:
        print(f"    [!] 代理机构复核失败: {str(error)[:100]}")
        return None
    finally:
        if agency_attempt is not None:
            try:
                clear_matching_agency_attempt(agency_attempt["attempt_id"])
            except Exception as error:
                print(f"    [!] 清理代理机构复核尝试标记失败: {error}")
        if detail_handle is not None and search_handle is not None:
            _close_detail_page(driver, detail_handle, search_handle)


def classify_agency_ack(
    application_no: str,
    old_daili_jg: str | None,
    agency_ack: dict | None,
) -> dict:
    """Build one report row from the pre-entry database value and MITM ack."""
    previous_agency = _clean_optional_text(old_daili_jg)
    if agency_ack is None:
        return {
            "application_no": application_no,
            "classification": "timeout",
            "old_daili_jg": previous_agency,
            "official_daili_jg": None,
            "official_daili_r": None,
            "persistence_status": None,
            "captured_at": None,
        }

    persistence_status = agency_ack["persistence_status"]
    official_agency = _clean_optional_text(agency_ack["daili_jg"])
    if persistence_status in {"unmatched", "official_empty", "persistence_error"}:
        classification = persistence_status
    elif previous_agency is None:
        classification = "first_collected"
    elif previous_agency == official_agency:
        classification = "unchanged"
    else:
        classification = "changed"

    return {
        "application_no": application_no,
        "classification": classification,
        "old_daili_jg": previous_agency,
        "official_daili_jg": official_agency,
        "official_daili_r": _clean_optional_text(agency_ack["daili_r"]),
        "persistence_status": persistence_status,
        "captured_at": agency_ack["captured_at"],
    }


def write_verification_reports(records: list[dict], target_count: int) -> None:
    """Atomically replace the fixed JSON and CSV agency verification reports."""
    classification_counts = {name: 0 for name in _CLASSIFICATIONS}
    for record in records:
        classification_counts[record["classification"]] += 1

    report_payload = {
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "target_count": target_count,
        "completed_count": len(records),
        "counts": classification_counts,
        "records": records,
    }
    write_json_atomic(AGENCY_VERIFICATION_REPORT_JSON_FILE, report_payload)

    csv_path = Path(AGENCY_VERIFICATION_REPORT_CSV_FILE)
    temporary_csv_path = Path(f"{csv_path}.tmp")
    with temporary_csv_path.open("w", encoding="utf-8-sig", newline="") as csv_stream:
        writer = csv.DictWriter(csv_stream, fieldnames=_REPORT_COLUMNS)
        writer.writeheader()
        writer.writerows(records)
    os.replace(temporary_csv_path, csv_path)


def run_agency_collection(arguments) -> list[dict]:
    """Reserve the shared desktop for the complete official agency verification."""
    with reserve_detail_collection_desktop("代理机构官方复核"):
        return _run_agency_collection(arguments)


def _run_agency_collection(arguments) -> list[dict]:
    targets = load_requested_targets(
        input_file=getattr(arguments, "input", None),
        app_nos=getattr(arguments, "app", None),
    )
    test_count = getattr(arguments, "test", None)
    if test_count:
        targets = targets[:test_count]
        print(f"[*] 测试模式：仅复核前 {len(targets)} 个申请号")

    print(f"[*] 本次将逐件复核 {len(targets)} 个申请号，不跳过已有机构信息")
    verification_records: list[dict] = []
    write_verification_reports(verification_records, len(targets))

    driver = None
    try:
        driver = BrowserService.launch_and_login(
            arguments.url,
            page_load_wait=FWXX_PAGE_LOAD_WAIT,
        )
        input_x, input_y, button_x, button_y = (
            CoordinateService.load_or_record_search_coordinates()
        )
        link_x, link_y = CoordinateService.load_or_record_detail_link_coordinates()
        countdown(FWXX_STARTUP_COUNTDOWN)

        patents_db = PatentsDB(PATENTS_DB_FILE)
        for index, application_no in enumerate(targets, 1):
            print(f"\n[{index}/{len(targets)}] 申请号: {application_no}")

            # sqxx may update the database immediately after the click, so snapshot first.
            old_record = patents_db.get_record(application_no)
            old_daili_jg = old_record.get("daili_jg") if old_record else None
            agency_ack = collect_one_agency(
                driver=driver,
                application_no=application_no,
                input_x=input_x,
                input_y=input_y,
                button_x=button_x,
                button_y=button_y,
                link_x=link_x,
                link_y=link_y,
            )
            report_record = classify_agency_ack(
                application_no,
                old_daili_jg,
                agency_ack,
            )
            verification_records.append(report_record)
            write_verification_reports(verification_records, len(targets))
            print(f"    [✓] 分类: {report_record['classification']}")

            if (
                index % FWXX_ANTI_CRAWL_BATCH_SIZE == 0
                and index < len(targets)
            ):
                wait_time = random.uniform(
                    FWXX_ANTI_CRAWL_WAIT_MIN,
                    FWXX_ANTI_CRAWL_WAIT_MAX,
                )
                print(f"    [*] 防爬虫等待 {wait_time:.1f} 秒...")
                time.sleep(wait_time)

        print(
            "\n[✓] 代理机构复核完成："
            + ", ".join(
                f"{name}={sum(1 for row in verification_records if row['classification'] == name)}"
                for name in _CLASSIFICATIONS
            )
        )
        return verification_records
    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass


def _positive_count(raw_count: str) -> int:
    count = int(raw_count)
    if count < 1:
        raise argparse.ArgumentTypeError("数量必须大于 0")
    return count


def _build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="代理机构官方复核")
    target_source = parser.add_mutually_exclusive_group(required=True)
    target_source.add_argument("--input", help="从文件读取申请号列表")
    target_source.add_argument("--app", help="直接指定申请号，多个用逗号分隔")
    parser.add_argument("--test", type=_positive_count, help="仅复核前 N 个申请号")
    parser.add_argument(
        "--url",
        default=SEARCH_PAGE_URL,
        help=f"搜索页 URL（默认：{SEARCH_PAGE_URL}）",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _build_argument_parser().parse_args(argv)
    if not USE_MITM_PROXY:
        print("[!] 代理机构复核依赖 MITM 代理，当前未启用", file=sys.stderr)
        print("    请先启动 python start_mitm_proxy.py", file=sys.stderr)
        return 1

    try:
        run_agency_collection(arguments)
    except DetailCollectionDesktopBusyError as error:
        print(f"[!] {error}", file=sys.stderr)
        return 2
    except (AgencyCollectionFatalError, OSError, ValueError) as error:
        print(f"[!] {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
