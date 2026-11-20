#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""CNIPA 费用信息独立采集程序。

自动模式处理费用数据集中必需费用栏目尚未采集的申请号。
`--input` 和 `--app` 可指定申请号，`--retry-failed` 可重试历史失败目标。
每轮每件只尝试一次，普通失败保留到失败清单，后续轮次再补采。
"""

import argparse
import json
import os
import random
import sys
import time

if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

# 虚拟显示器必须在 pyautogui / Xlib 任何 import 之前启动。
if os.getenv('USE_VIRTUAL_DISPLAY', '').lower() in ('true', '1', 'yes') \
        and sys.platform.startswith('linux'):
    try:
        from pyvirtualdisplay import Display as _VD

        _vd_w = int(os.getenv('VIRTUAL_DISPLAY_WIDTH', '1920'))
        _vd_h = int(os.getenv('VIRTUAL_DISPLAY_HEIGHT', '1080'))
        _vd_inst = _VD(visible=False, size=(_vd_w, _vd_h), color_depth=24)
        _vd_inst.start()
        print(f"✓ 虚拟显示器已启动 ({_vd_w}x{_vd_h})")
    except ImportError:
        print("⚠️  pyvirtualdisplay 未安装，使用物理桌面")

import pyautogui
from selenium.common.exceptions import WebDriverException

sys.path.insert(0, os.path.dirname(__file__))
from atomic_write import write_json_atomic
from browser_service import BrowserService
from cnipa_session import CNIPALoginRequired, raise_if_cnipa_login_required
from browser_utils import is_browser_alive, raise_system_exit_on_sigterm
from collection_checkpoint import CollectionBatch, CollectionBatchBusyError
from cache_utils import (
    clear_cache_key,
    parse_app_no_list,
    read_json_cache,
)
from coordinate_service import CoordinateService
from db_manager import PatentsDB
from detection_logger import DetectionLogger
from detail_attempt import (
    DetailCollectionFatalError,
    DetailIdentityTimeout,
    begin_detail_attempt,
    clear_matching_detail_attempt,
    matches_detail_attempt,
    wait_for_detail_identity,
)
from detail_search import (
    DetailSearchRetryableError,
    restore_detail_search_page,
    wait_for_detail_search_target,
    wait_for_unique_detail_window,
)
from input_service import InputService
from desktop_collection_lock import (
    DetailCollectionDesktopBusyError,
    reserve_detail_collection_desktop,
)
from settings import (
    CNIPA_URL,
    DETECTION_LOG_JSONL_FILE,
    FEE_UNMATCHED_FILE,
    FEE_COLLECTION_CHECKPOINT_FILE,
    FWXX_ANTI_CRAWL_BATCH_SIZE,
    FWXX_ANTI_CRAWL_WAIT_MAX,
    FWXX_ANTI_CRAWL_WAIT_MIN,
    FWXX_CACHE_POLL_TIMEOUT,
    FWXX_DETAIL_CLICK_WAIT,
    FWXX_DETAIL_CLOSE_WAIT,
    FWXX_INPUT_DELAY_MAX,
    FWXX_INPUT_DELAY_MIN,
    FWXX_INPUT_PAUSE_PROB,
    FWXX_MENU_CLICK_WAIT,
    FWXX_PAGE_LOAD_WAIT,
    FWXX_POST_SEARCH_WAIT,
    FWXX_STARTUP_COUNTDOWN,
    FWXX_TAB_SWITCH_WAIT,
    PATENT_FEE_CACHE_FILE,
    PATENTS_DB_FILE,
    PYAUTOGUI_FAILSAFE,
    PYAUTOGUI_PAUSE,
    USE_MITM_PROXY,
)

pyautogui.PAUSE = PYAUTOGUI_PAUSE
pyautogui.FAILSAFE = PYAUTOGUI_FAILSAFE

SEARCH_PAGE_URL = CNIPA_URL
PATENT_FEE_CACHE_FILE = str(PATENT_FEE_CACHE_FILE)
FEE_UNMATCHED_FILE = str(FEE_UNMATCHED_FILE)

_FEE_PAYLOAD_FIELDS = (
    'payable_fee_records',
    'late_fee_schedule_records',
    'paid_fee_records',
    'fee_receipt_dispatch_records',
    'fee_snapshot_at',
)
_REQUIRED_FEE_PAYLOAD_FIELDS = (
    'payable_fee_records',
    'paid_fee_records',
    'fee_receipt_dispatch_records',
)
_FEE_SECTION_LABELS = {
    'payable_fee_records': '应缴费',
    'late_fee_schedule_records': '应缴滞纳金',
    'paid_fee_records': '已缴费',
    'fee_receipt_dispatch_records': '收据发文',
}
FEE_COLLECTION_KIND = 'fees'


def countdown(seconds: int, message: str = "请手动记录坐标，倒计时") -> None:
    for remaining in range(seconds, 0, -1):
        print(f"\r{message}: {remaining:2d} 秒...", end="", flush=True)
        time.sleep(1)
    print(f"\r{message}: 0 秒...完成！    ")


def load_fee_dataset_targets(force: bool = False) -> list[str]:
    """返回费用数据集内的待采申请号（force 时为整个数据集）。

    费用采集范围由用户导入的数据集（fee_targets 表）决定，与驳回状态无关。
    """
    db = PatentsDB(PATENTS_DB_FILE)
    progress = db.fee_dataset_progress()

    if progress['total'] == 0:
        print("\n" + "=" * 60)
        print("📭 费用采集数据集为空")
        print("=" * 60)
        print("请先导入需要采集费用的申请号名单：")
        print("  uv run python import_fee_targets.py 名单.xlsx")
        print("或在 Dashboard「发文与费用」页上传 CSV/Excel 文件。")
        print("=" * 60 + "\n")
        return []

    if force:
        targets = db.fee_dataset_app_nos()
    else:
        targets = db.fee_dataset_pending_app_nos()

    print("\n" + "=" * 60)
    print("📊 费用信息采集统计（数据集口径）")
    print("=" * 60)
    print(f"✓ 数据集共: {progress['total']} 条")
    print(f"✓ 已完整采集必需费用栏目: {progress['collected']} 条")
    print(f"⏳ 待采集费用: {progress['pending']} 条")
    if progress['unregistered']:
        print(f"⚠️  未建档: {progress['unregistered']} 条（主库无记录，需先跑主采集建档）")
    if force:
        print(f"🔁 强制重采：本次目标为整个数据集 {len(targets)} 条")
    print("=" * 60 + "\n")

    if targets:
        print("待采集申请号列表:")
        for index, application_no in enumerate(targets[:10], 1):
            print(f"  {index}. {application_no}")
        if len(targets) > 10:
            print(f"  ... 及其他 {len(targets) - 10} 个")
        print()

    return targets


def load_failed_fee_targets() -> list[str]:
    """返回费用采集失败记录中的全部申请号。"""
    failures = PatentsDB(PATENTS_DB_FILE).failed_collection_targets(
        FEE_COLLECTION_KIND
    )
    targets = [failure['application_no'] for failure in failures]
    if targets:
        print(f"\n[*] 从费用失败记录读取 {len(targets)} 个待重试申请号")
    else:
        print("\n✓ 当前没有费用采集失败目标")
    return targets


def load_standalone_targets(
    input_file: str | None = None,
    app_nos: str | None = None,
    force: bool = False,
) -> list[str]:
    """从文件或命令行加载指定申请号，并支持断点续传。"""
    targets: list[str] = []

    if app_nos:
        targets = parse_app_no_list(app_nos)
        print(f"\n[*] 从命令行参数读取 {len(targets)} 个申请号")
    elif input_file:
        if not os.path.exists(input_file):
            print(f"[!] 文件不存在: {input_file}")
            return []
        with open(input_file, 'r', encoding='utf-8') as input_stream:
            targets = parse_app_no_list(input_stream.read())
        print(f"\n[*] 从文件读取 {len(targets)} 个申请号: {input_file}")

    if targets:
        if force:
            print("[*] 强制采集：不按案件状态筛选，也不跳过已有费用记录")
        else:
            collected = _load_standalone_collected()
            before = len(targets)
            targets = [target for target in targets if target not in collected]
            skipped = before - len(targets)
            if skipped:
                print(f"[*] 跳过已采集: {skipped} 个（断点续传）")

        print(f"[*] 待采集: {len(targets)} 个")
        print("\n待采集申请号列表:")
        for index, application_no in enumerate(targets[:10], 1):
            print(f"  {index}. {application_no}")
        if len(targets) > 10:
            print(f"  ... 及其他 {len(targets) - 10} 个")
        print()

    return targets


def _load_standalone_collected() -> set[str]:
    """返回必需费用栏目已采集的申请号。"""
    try:
        return PatentsDB(PATENTS_DB_FILE).fee_details_completed_app_nos()
    except Exception:
        return set()


def missing_required_fee_sections(fee_snapshot: dict) -> list[str]:
    """An explicit empty list is complete; an omitted or unknown section is not."""
    return [
        _FEE_SECTION_LABELS[field] for field in _REQUIRED_FEE_PAYLOAD_FIELDS
        if fee_snapshot.get(field) is None
    ]


def wait_for_fee_snapshot(application_no: str, attempt_id: str) -> dict | None:
    """Wait for a complete response from this attempt, retaining partial evidence on timeout."""
    deadline = time.monotonic() + FWXX_CACHE_POLL_TIMEOUT
    latest_snapshot = None
    while True:
        raise_if_cnipa_login_required()
        cached_snapshot = read_json_cache(PATENT_FEE_CACHE_FILE).get(application_no)
        if matches_detail_attempt(cached_snapshot, attempt_id):
            missing_sections = missing_required_fee_sections(cached_snapshot)
            if not missing_sections:
                return cached_snapshot
            if latest_snapshot is None:
                print(f"    [*] 已收到部分费用栏目，继续等待：缺少{'、'.join(missing_sections)}")
            # Do not combine different responses into a snapshot the server never returned.
            latest_snapshot = cached_snapshot
        remaining_wait = deadline - time.monotonic()
        if remaining_wait <= 0:
            return latest_snapshot
        time.sleep(min(0.5, remaining_wait))


def collect_one_fee(
    driver,
    application_no: str,
    input_x: int,
    input_y: int,
    button_x: int,
    button_y: int,
    link_x: int,
    link_y: int,
    fee_menu_x: int,
    fee_menu_y: int,
) -> dict | None:
    """在详情页采集单个申请号的费用信息。"""
    fee_fields: dict = {}
    detail_attempt = None
    detail_handle = None
    search_handle = None
    detail_click_started = False
    try:
        if not is_browser_alive(driver):
            raise DetailCollectionFatalError('浏览器已关闭，本条未采集，费用批次已中断')

        initial_handles = list(driver.window_handles)
        if len(initial_handles) != 1:
            raise DetailCollectionFatalError("费用采集开始时不是唯一搜索页，已停止批次")
        search_handle = initial_handles[0]
        driver.switch_to.window(search_handle)

        print(f"\n  [{application_no}] 开始采集费用信息...")
        try:
            clear_cache_key(PATENT_FEE_CACHE_FILE, application_no)
        except Exception as error:
            print(f"    [!] 无法清理旧费用缓存，已停止本件采集: {error}")
            return None

        print("    [*] 输入申请号并点击查询按钮...")
        detail_attempt = begin_detail_attempt(application_no)
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

        wait_for_detail_search_target(detail_attempt)
        print(f"    [✓] 本轮搜索响应已确认唯一目标申请号 {application_no}")

        print("    [*] 点击申请号链接进入详情页...")
        detail_click_started = True
        InputService.move_and_click(
            link_x,
            link_y,
            post_click_wait=FWXX_DETAIL_CLICK_WAIT,
        )
        detail_handle = wait_for_unique_detail_window(driver, search_handle)
        driver.switch_to.window(detail_handle)
        time.sleep(FWXX_TAB_SWITCH_WAIT)
        try:
            wait_for_detail_identity(detail_attempt)
        except DetailIdentityTimeout as error:
            raise DetailSearchRetryableError(str(error)) from error
        print("    [✓] 官方申请号已确认，开始采集费用")
        print("    [*] 点击'费用信息'菜单...")
        InputService.move_and_click(
            fee_menu_x,
            fee_menu_y,
            post_click_wait=FWXX_MENU_CLICK_WAIT,
        )

        print("    [*] 从 MITM 缓存读取费用信息...")
        fee_payload = wait_for_fee_snapshot(application_no, detail_attempt['attempt_id'])
        if fee_payload is None:
            print("    [!] 未从缓存中获得费用信息")
        else:
            fee_counts = []
            for field, label in _FEE_SECTION_LABELS.items():
                if fee_payload.get(field) is not None:
                    fee_counts.append(f"{label} {len(fee_payload[field])} 条")
                else:
                    fee_counts.append(f"{label} 未返回")
            for field, issue in fee_payload.get('fee_section_issues', {}).items():
                label = _FEE_SECTION_LABELS[field]
                evidence = ', '.join(f'{name}={value}' for name, value in issue.items())
                print(f"    [费用栏目诊断] {label}：{evidence}")
            missing_sections = missing_required_fee_sections(fee_payload)
            if missing_sections:
                print(
                    f"    [!] 等待 {FWXX_CACHE_POLL_TIMEOUT:g} 秒后费用栏目仍不完整："
                    f"缺少{'、'.join(missing_sections)}；{'; '.join(fee_counts)}"
                )
            else:
                print(f"    [✓] 已读取完整费用栏目：{'; '.join(fee_counts)}")
            fee_fields.update({
                field: fee_payload[field] for field in _FEE_PAYLOAD_FIELDS
                if field in fee_payload
            })

        return fee_fields or None

    except (CNIPALoginRequired, DetailCollectionFatalError):
        raise
    except DetailSearchRetryableError:
        if detail_click_started:
            restore_detail_search_page(driver, search_handle, detail_attempt['attempt_id'])
            detail_attempt = None
            detail_handle = None
        raise
    except pyautogui.FailSafeException as error:
        raise DetailCollectionFatalError('鼠标紧急停止已触发，费用批次已中断') from error
    except WebDriverException as error:
        raise DetailCollectionFatalError('浏览器连接失效，费用批次已中断') from error
    except Exception as error:
        if detail_click_started and detail_handle is None:
            restore_detail_search_page(driver, search_handle, detail_attempt['attempt_id'])
            detail_attempt = None
        raise DetailSearchRetryableError(f'费用采集失败: {type(error).__name__}: {str(error)[:200]}') from error
    finally:
        if detail_attempt is not None:
            clear_matching_detail_attempt(detail_attempt['attempt_id'])
        if detail_handle is not None:
            try:
                if detail_handle in driver.window_handles:
                    driver.switch_to.window(detail_handle)
                    driver.close()
                    time.sleep(FWXX_DETAIL_CLOSE_WAIT)
                if list(driver.window_handles) != [search_handle]:
                    raise DetailCollectionFatalError("费用详情页关闭后未恢复唯一搜索页")
                driver.switch_to.window(search_handle)
                time.sleep(FWXX_TAB_SWITCH_WAIT)
            except DetailCollectionFatalError:
                raise
            except Exception as error:
                raise DetailCollectionFatalError("无法确认费用详情页已关闭，已停止批次") from error


def persist_fee_fields(application_no: str, fee_fields: dict) -> dict | None:
    """按版本写入费用并返回主库实际保留的快照；失败时备份本次响应。"""
    try:
        db = PatentsDB(PATENTS_DB_FILE)
        persisted_fields = {
            field: fee_fields[field]
            for field in _FEE_PAYLOAD_FIELDS
            if field in fee_fields
        }
        stored_snapshot = db.update_fee_snapshot(application_no, persisted_fields)
        if stored_snapshot is None:
            print(f"    [!] {application_no} 的费用字段未更新到主库")
            _append_unmatched_fee(
                application_no,
                fee_fields,
                reason='not_found_in_db',
            )
        return stored_snapshot
    except Exception as error:
        print(f"    [!] 费用字段更新失败: {error}")
        _append_unmatched_fee(
            application_no,
            fee_fields,
            reason=f'update_failed: {error}',
        )
        return None


def _append_unmatched_fee(
    application_no: str,
    fee_fields: dict,
    reason: str = '',
) -> None:
    """将无法匹配到主库的费用字段追加到独立备份。"""
    try:
        if os.path.exists(FEE_UNMATCHED_FILE):
            with open(FEE_UNMATCHED_FILE, 'r', encoding='utf-8') as input_stream:
                unmatched_payload = json.load(input_stream)
        else:
            unmatched_payload = {'records': []}
        unmatched_record = {
            'application_no': application_no,
            'reason': reason,
        }
        for field in _FEE_PAYLOAD_FIELDS:
            if field in fee_fields:
                unmatched_record[field] = fee_fields[field]
        unmatched_payload['records'].append(unmatched_record)
        write_json_atomic(FEE_UNMATCHED_FILE, unmatched_payload)
    except Exception as error:
        print(f"    [!] 写入费用 unmatched 失败: {error}")


def run_fee_collection(args) -> None:
    """独占共享桌面并执行完整费用采集。"""
    with reserve_detail_collection_desktop("费用信息采集"):
        _run_fee_collection(args)


def _run_fee_collection(args) -> None:
    """执行已取得桌面独占权的费用采集主循环。"""
    if getattr(args, 'resume_batch', None):
        with CollectionBatch.resume('fees', FEE_COLLECTION_CHECKPOINT_FILE, args.resume_batch) as checkpoint:
            _collect_fee_batch(args, checkpoint)
        return
    standalone_mode = bool(getattr(args, 'input', None) or getattr(args, 'app', None))
    retry_failed_mode = bool(getattr(args, 'retry_failed', False))

    print("\n" + "=" * 70)
    if retry_failed_mode:
        print("🚀 费用信息采集程序启动（失败重试）")
    elif standalone_mode:
        print("🚀 费用信息采集程序启动（独立模式）")
    else:
        print("🚀 费用信息采集程序启动")
    print("=" * 70)

    if retry_failed_mode:
        targets = load_failed_fee_targets()
    elif standalone_mode:
        targets = load_standalone_targets(
            input_file=getattr(args, 'input', None),
            app_nos=getattr(args, 'app', None),
            force=bool(getattr(args, 'force', False)),
        )
    else:
        targets = load_fee_dataset_targets(force=bool(getattr(args, 'force', False)))

    if not targets:
        if retry_failed_mode:
            print("✓ 无费用采集失败目标")
        elif standalone_mode:
            print("✓ 无待采集的申请号（可能已全部采集完毕）")
        else:
            print("✓ 本次无采集目标（数据集为空或已全部采集，见上方统计）")
        return

    with CollectionBatch.create('fees', FEE_COLLECTION_CHECKPOINT_FILE, targets) as checkpoint:
        _collect_fee_batch(args, checkpoint)


def _collect_fee_batch(args, checkpoint: CollectionBatch) -> None:
    targets = checkpoint.select_pending(args.test)
    if not targets:
        print("✓ 本批没有待采集费用的申请号")
        return

    if args.test:
        print(f"📋 测试模式：仅采集前 {len(targets)} 个\n")

    driver = None
    try:
        failure_db = PatentsDB(PATENTS_DB_FILE)
        failed_count = 0
        registered_targets = []
        for application_no in targets:
            if failure_db.get_record(application_no) is None:
                reason = '主库未建档，请先完成主采集，再重试费用采集'
                print(f"  [!] {application_no}：{reason}；本次不打开详情页")
                checkpoint.record_started(application_no)
                checkpoint.record_failure(application_no, reason)
                failure_db.record_collection_failure(FEE_COLLECTION_KIND, application_no, 'not_found_in_db')
                failed_count += 1
            else:
                registered_targets.append(application_no)
        if not registered_targets:
            raise RuntimeError(f'本批 {failed_count} 件主库均未建档，请先完成主采集；未完成清单已保留')
        if failed_count:
            print(f"[*] {failed_count} 件待主采集建档，本次检索其余 {len(registered_targets)} 件")

        print("\n[*] 正在加载坐标配置...")
        input_x, input_y, button_x, button_y = (
            CoordinateService.load_search_coordinates()
        )
        link_x, link_y = CoordinateService.load_detail_link_coordinates()
        fee_menu_x, fee_menu_y = CoordinateService.load_fee_menu_coordinates()

        print(f"\n[*] 打开搜索页: {args.url}")
        driver = BrowserService.launch_and_login(
            args.url,
            page_load_wait=FWXX_PAGE_LOAD_WAIT,
        )
        countdown(FWXX_STARTUP_COUNTDOWN, "坐标已就绪，即将开始费用采集")

        print("\n" + "=" * 70)
        print("费用采集进度")
        print("=" * 70)
        print("[*] 每件仅尝试一次；失败后跳过，后续从费用失败清单补采")
        success_count = 0

        for index, application_no in enumerate(registered_targets, 1):
            if not is_browser_alive(driver):
                print("\n⚠️  浏览器已关闭，停止采集")
                remaining = len(registered_targets) - index + 1
                print(
                    f"\n已采集 {success_count} 条，失败 {failed_count} 条，"
                    f"还有 {remaining} 条未采集"
                )
                raise DetailCollectionFatalError('浏览器进程意外退出，费用采集已中断')

            print(f"\n[{index}/{len(registered_targets)}] 申请号: {application_no}")
            checkpoint.record_started(application_no)
            navigation_failure = ''
            fee_fields = None
            try:
                fee_fields = collect_one_fee(
                    driver=driver,
                    application_no=application_no,
                    input_x=input_x,
                    input_y=input_y,
                    button_x=button_x,
                    button_y=button_y,
                    link_x=link_x,
                    link_y=link_y,
                    fee_menu_x=fee_menu_x,
                    fee_menu_y=fee_menu_y,
                )
            except DetailSearchRetryableError as error:
                navigation_failure = str(error)

            if fee_fields:
                stored_snapshot = persist_fee_fields(application_no, fee_fields)
                if stored_snapshot is None:
                    print(f"  ⚠️  主库未更新，费用信息已备份到 {FEE_UNMATCHED_FILE}")
                    failure_db.record_collection_failure(
                        FEE_COLLECTION_KIND,
                        application_no,
                        'fee_persistence_failed',
                    )
                    failed_count += 1
                    checkpoint.record_failure(application_no, '费用数据未写入专利主库，已保存未匹配备份')
                elif missing_required_fee_sections(stored_snapshot):
                    reason = f"主库费用栏目仍不完整：缺少{'、'.join(missing_required_fee_sections(stored_snapshot))}"
                    print(f"  ⚠️  {reason}；已保存现有栏目，保留待重试")
                    failure_db.record_collection_failure(
                        FEE_COLLECTION_KIND,
                        application_no,
                        'incomplete_fee_payload',
                    )
                    failed_count += 1
                    checkpoint.record_failure(application_no, reason)
                else:
                    print("  ✅ 主库费用栏目已完整")
                    failure_db.clear_collection_failure(
                        FEE_COLLECTION_KIND,
                        application_no,
                    )
                    success_count += 1
                    checkpoint.record_success(application_no)
            else:
                reason = navigation_failure or '等待结束仍未收到本次费用数据'
                print(f"  ❌ {reason}；本轮跳过，已记入费用失败清单，继续后续申请号")
                failure_db.record_collection_failure(
                    FEE_COLLECTION_KIND,
                    application_no,
                    navigation_failure or 'no_fee_payload',
                )
                failed_count += 1
                checkpoint.record_failure(application_no, reason)

            if index % FWXX_ANTI_CRAWL_BATCH_SIZE == 0 and index < len(registered_targets):
                wait_time = random.uniform(
                    FWXX_ANTI_CRAWL_WAIT_MIN,
                    FWXX_ANTI_CRAWL_WAIT_MAX,
                )
                print(f"\n  [*] 防爬虫等待 {wait_time:.1f} 秒...")
                time.sleep(wait_time)

        print("\n" + "=" * 70)
        print(f"费用采集批次结束，成功: {success_count}, 失败: {failed_count}")
        print("=" * 70)

        print("\n[*] 导出 Excel...")
        logger = DetectionLogger()
        if logger.export_to_excel():
            print("[✓] Excel 导出成功!")

        exported = PatentsDB(PATENTS_DB_FILE).export_to_jsonl(
            DETECTION_LOG_JSONL_FILE
        )
        print(f"[✓] JSONL 备份已刷新：{exported} 条（含费用信息）")
        if failed_count:
            print("[*] 可在控制台费用面板点击「仅重试失败项」；下轮仅采集仍在失败清单中的申请号")
            print('    补采命令: python collect_fees.py --retry-failed')
            raise RuntimeError(
                f'费用采集失败 {failed_count} 条，未完成清单: {FEE_COLLECTION_CHECKPOINT_FILE}'
            )

    except Exception as error:
        print(f"\n[!] 费用采集过程出错: {error}")
        import traceback
        traceback.print_exc()
        raise
    finally:
        if checkpoint.remaining_count:
            print(f"\n[*] 未完成 {checkpoint.remaining_count} 条，清单已保存: {FEE_COLLECTION_CHECKPOINT_FILE}")
            print(f'    续跑命令: python collect_fees.py --resume-batch {checkpoint.id}')
        if driver:
            try:
                driver.quit()
            except Exception:
                pass
        print("\n[✓] 程序结束")


def _build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="费用信息采集模块")
    parser.add_argument('--test', type=int, help='测试模式，仅采集前 N 个申请号')
    parser.add_argument(
        '--url',
        type=str,
        default=SEARCH_PAGE_URL,
        help=f'搜索页 URL（默认：{SEARCH_PAGE_URL}）',
    )
    target_source = parser.add_mutually_exclusive_group()
    target_source.add_argument('--input', type=str, help='独立模式：从文件读取申请号列表')
    target_source.add_argument('--app', type=str, help='独立模式：直接指定申请号，多个用逗号分隔')
    target_source.add_argument('--resume-batch', metavar='ID', help='继续指定的未完成费用批次')
    target_source.add_argument(
        '--retry-failed',
        action='store_true',
        help='重试费用采集失败记录中的全部申请号',
    )
    parser.add_argument(
        '--force',
        action='store_true',
        help='独立模式（--input/--app）下不筛选不续传；单独使用时强制重采整个费用数据集',
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """运行费用采集 CLI，并将桌面占用转换为明确的退出状态。"""
    raise_system_exit_on_sigterm()
    parser = _build_argument_parser()
    arguments = parser.parse_args(argv)
    if arguments.retry_failed and arguments.force:
        parser.error("--retry-failed 不能与 --force 同时使用")
    if not USE_MITM_PROXY:
        print(
            "\n[!] MITM 代理未启用，费用采集依赖代理拦截，无法继续",
            file=sys.stderr,
        )
        print("    请先启动代理后重试：", file=sys.stderr)
        print("      python start_mitm_proxy.py", file=sys.stderr)
        print(
            "    或设置环境变量：USE_MITM_PROXY=true python collect_fees.py",
            file=sys.stderr,
        )
        return 1

    try:
        run_fee_collection(arguments)
    except CNIPALoginRequired as error:
        print(f"\n[!] {error}", file=sys.stderr)
        return 1
    except (DetailCollectionDesktopBusyError, CollectionBatchBusyError, ValueError) as error:
        print(f"\n[!] {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
