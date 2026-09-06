#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""CNIPA 费用信息独立采集程序。

自动模式处理费用数据集中必需费用栏目尚未采集的申请号。
`--input` 和 `--app` 可指定申请号，`--retry-failed` 可重试历史失败目标。
"""

import argparse
import json
import os
import random
import sys
import time
from functools import partial

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

sys.path.insert(0, os.path.dirname(__file__))
from atomic_write import write_json_atomic
from browser_service import BrowserService
from browser_utils import is_browser_alive, raise_system_exit_on_sigterm
from collection_health import CollectionFailureStreak, CollectionFailureStreakExceeded
from cache_utils import (
    clear_cache_key,
    parse_app_no_list,
    poll_cache_for_key,
)
from coordinate_service import CoordinateService
from db_manager import PatentsDB
from detection_logger import DetectionLogger
from detail_attempt import (
    DetailCollectionFatalError,
    begin_detail_attempt,
    clear_matching_detail_attempt,
    matches_detail_attempt,
    wait_for_detail_identity,
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


def collect_one_fee(
    driver,
    application_no: str,
    input_x: int,
    input_y: int,
    button_x: int,
    button_y: int,
    link_x: int,
    link_y: int,
) -> dict | None:
    """在详情页采集单个申请号的费用信息。"""
    fee_fields: dict = {}
    detail_attempt = None
    detail_handle = None
    search_handle = None
    try:
        if not is_browser_alive(driver):
            print("    [!] 浏览器已关闭，无法采集")
            return None

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
            page_text = driver.page_source.lower()
            if any(
                keyword in page_text
                for keyword in ('无查询结果', '无搜索结果', '请输入查询', '没有找到')
            ):
                print("    [!] 搜索无结果或出现异常提示")
                return None
        except Exception:
            pass

        print("    [*] 点击申请号链接进入详情页...")
        detail_attempt = begin_detail_attempt(application_no)
        InputService.move_and_click(
            link_x,
            link_y,
            post_click_wait=FWXX_DETAIL_CLICK_WAIT,
        )
        new_handles = [handle for handle in driver.window_handles if handle != search_handle]
        if len(new_handles) != 1:
            raise DetailCollectionFatalError("费用详情页未唯一打开，已停止批次")

        detail_handle = new_handles[0]
        driver.switch_to.window(detail_handle)
        time.sleep(FWXX_TAB_SWITCH_WAIT)
        wait_for_detail_identity(detail_attempt)
        print("    [✓] 官方申请号已确认，开始采集费用")
        fee_menu_x, fee_menu_y = (
            CoordinateService.load_or_record_fee_menu_coordinates()
        )

        print("    [*] 点击'费用信息'菜单...")
        InputService.move_and_click(
            fee_menu_x,
            fee_menu_y,
            post_click_wait=FWXX_MENU_CLICK_WAIT,
        )

        print("    [*] 从 MITM 缓存读取费用信息...")
        fee_payload = poll_cache_for_key(
            PATENT_FEE_CACHE_FILE,
            application_no,
            max_wait=FWXX_CACHE_POLL_TIMEOUT,
            validate=partial(
                matches_detail_attempt,
                expected_attempt_id=detail_attempt['attempt_id'],
            ),
        )
        if fee_payload is None:
            print("    [!] 未从缓存中获得费用信息")
        else:
            fee_counts = []
            for field, label in (
                ('payable_fee_records', '应缴费'),
                ('late_fee_schedule_records', '应缴滞纳金'),
                ('paid_fee_records', '已缴费'),
                ('fee_receipt_dispatch_records', '收据发文'),
            ):
                if field in fee_payload:
                    fee_counts.append(f"{label} {len(fee_payload[field])} 条")
                else:
                    fee_counts.append(f"{label} 未返回")
            print(f"    [✓] 成功读取费用信息：{'; '.join(fee_counts)}")
            fee_fields.update({
                field: value for field, value in fee_payload.items()
                if field != 'detail_attempt_id'
            })

        return fee_fields or None

    except DetailCollectionFatalError:
        raise
    except Exception as error:
        print(f"    [!] 采集失败: {str(error)[:100]}")
        return fee_fields or None
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
    test_count = args.test
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

    if test_count:
        targets = targets[:test_count]
        print(f"📋 测试模式：仅采集前 {len(targets)} 个\n")

    driver = None
    try:
        print(f"\n[*] 打开搜索页: {args.url}")
        driver = BrowserService.launch_and_login(
            args.url,
            page_load_wait=FWXX_PAGE_LOAD_WAIT,
        )
        print("\n[*] 正在加载坐标配置...")
        input_x, input_y, button_x, button_y = (
            CoordinateService.load_or_record_search_coordinates()
        )
        link_x, link_y = (
            CoordinateService.load_or_record_detail_link_coordinates()
        )
        countdown(FWXX_STARTUP_COUNTDOWN, "搜索页坐标已就绪，即将开始费用采集")

        print("\n" + "=" * 70)
        print("费用采集进度")
        print("=" * 70)
        success_count = 0
        failed_count = 0
        failure_streak = CollectionFailureStreak('费用信息采集')
        failure_db = PatentsDB(PATENTS_DB_FILE)

        for index, application_no in enumerate(targets, 1):
            if not is_browser_alive(driver):
                print("\n⚠️  浏览器已关闭，停止采集")
                remaining = len(targets) - index + 1
                print(
                    f"\n已采集 {success_count} 条，失败 {failed_count} 条，"
                    f"还有 {remaining} 条未采集"
                )
                break

            print(f"\n[{index}/{len(targets)}] 申请号: {application_no}")
            fee_fields = collect_one_fee(
                driver=driver,
                application_no=application_no,
                input_x=input_x,
                input_y=input_y,
                button_x=button_x,
                button_y=button_y,
                link_x=link_x,
                link_y=link_y,
            )

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
                    failure_streak.record_failure()
                elif not all(
                    stored_snapshot[field] is not None for field in _REQUIRED_FEE_PAYLOAD_FIELDS
                ):
                    print("  ⚠️  主库费用栏目仍不完整，需重试")
                    failure_db.record_collection_failure(
                        FEE_COLLECTION_KIND,
                        application_no,
                        'incomplete_fee_payload',
                    )
                    failed_count += 1
                    failure_streak.record_failure()
                else:
                    print("  ✅ 主库费用栏目已完整")
                    failure_db.clear_collection_failure(
                        FEE_COLLECTION_KIND,
                        application_no,
                    )
                    success_count += 1
                    failure_streak.record_success()
            else:
                print("  ❌ 未采集到费用数据")
                failure_db.record_collection_failure(
                    FEE_COLLECTION_KIND,
                    application_no,
                    'no_fee_payload',
                )
                failed_count += 1
                failure_streak.record_failure()

            if index % FWXX_ANTI_CRAWL_BATCH_SIZE == 0 and index < len(targets):
                wait_time = random.uniform(
                    FWXX_ANTI_CRAWL_WAIT_MIN,
                    FWXX_ANTI_CRAWL_WAIT_MAX,
                )
                print(f"\n  [*] 防爬虫等待 {wait_time:.1f} 秒...")
                time.sleep(wait_time)

        print("\n" + "=" * 70)
        print(f"✓ 费用采集完成！成功: {success_count}, 失败: {failed_count}")
        print("=" * 70)

        print("\n[*] 导出 Excel...")
        logger = DetectionLogger()
        if logger.export_to_excel():
            print("[✓] Excel 导出成功!")

        exported = PatentsDB(PATENTS_DB_FILE).export_to_jsonl(
            DETECTION_LOG_JSONL_FILE
        )
        print(f"[✓] JSONL 备份已刷新：{exported} 条（含费用信息）")

    except CollectionFailureStreakExceeded:
        # The streak tracker already records the actionable alert details.
        raise
    except Exception as error:
        print(f"\n[!] 费用采集过程出错: {error}")
        import traceback
        traceback.print_exc()
        raise
    finally:
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
    except DetailCollectionDesktopBusyError as error:
        print(f"\n[!] {error}", file=sys.stderr)
        return 2
    except CollectionFailureStreakExceeded as error:
        print(f"\n⛔ {error}", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
