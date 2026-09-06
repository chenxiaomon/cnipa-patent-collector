#!/usr/bin/env python
# -*- coding: utf-8 -*-
import sys
# 解决 Windows 控制台编码问题
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

"""
发文信息采集模块 (FWXX Collection Module)

功能：对已采集的专利记录中状态为"驳回等复审请求"的案件，
      进一步采集其"发文信息"页面的数据，包括发文列表、驳回决定时间等。

运行方式：
  # 正常运行（从 PatentsDB 筛选）
  USE_MITM_PROXY=true python collect_fwxx.py

  # 测试模式（仅采集 3 个）
  USE_MITM_PROXY=true python collect_fwxx.py --test 3

  # 独立模式：直接指定申请号列表文件
  USE_MITM_PROXY=true python collect_fwxx.py --input data/fwxx_list.txt

  # 独立模式：直接指定单个申请号
  USE_MITM_PROXY=true python collect_fwxx.py --app 2022108424726

  # 独立模式：指定多个申请号（逗号分隔）
  USE_MITM_PROXY=true python collect_fwxx.py --app 2022108424726,2023101501868

  # 强制模式：指定列表全部采集，不限制案件状态，也不跳过已有发文记录
  USE_MITM_PROXY=true python collect_fwxx.py --input data/fwxx_list.txt --force

架构设计：
  1. 筛选目标：从 PatentsDB 中找出待采集发文的申请号
     或通过 --input / --app 直接指定申请号（独立模式）
  2. 配置坐标：如果未有坐标配置，手动记录 2 个关键坐标
  3. 逐个采集：搜索 → 进详情页 → 点击发文信息 → 读取缓存 → 更新日志
  4. 导出结果：Excel 两个 Sheet（主信息 + 发文信息）
     独立模式结果保存到 data/results/fwxx_standalone_results.json
"""

import json
import os
import time
import random
import argparse
from datetime import datetime, timezone
from functools import partial

# 虚拟显示器必须在 pyautogui / Xlib 任何 import 之前启动
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

# PyAutoGUI 和 Selenium
import pyautogui
from selenium.common.exceptions import WebDriverException

# 导入现有模块
sys.path.insert(0, os.path.dirname(__file__))
from atomic_write import write_json_atomic
from detection_logger import DetectionLogger
from detail_attempt import (
    DetailCollectionFatalError,
    begin_detail_attempt,
    clear_matching_detail_attempt,
    matches_detail_attempt,
    wait_for_detail_identity,
)
from browser_utils import is_browser_alive, raise_system_exit_on_sigterm
from collection_health import CollectionFailureStreak, CollectionFailureStreakExceeded
from collection_checkpoint import CollectionBatch, CollectionBatchBusyError
from coordinate_service import CoordinateService
from browser_service import BrowserService
from input_service import InputService
from cache_utils import (
    poll_cache_for_key,
    clear_cache_key,
    parse_app_no_list,
)
from desktop_collection_lock import (
    DetailCollectionDesktopBusyError,
    reserve_detail_collection_desktop,
)
from settings import (
    CNIPA_URL, DETECTION_LOG_JSONL_FILE, CONFIG_FILE, CONFIG_FWXX_FILE,
    PATENT_CACHE_FILE, PATENT_FWXX_CACHE_FILE,
    FWXX_UNMATCHED_FILE, PYAUTOGUI_PAUSE, PYAUTOGUI_FAILSAFE,
    USE_MITM_PROXY,
    PATENTS_DB_FILE,
    FWXX_COLLECTION_CHECKPOINT_FILE,
    FWXX_PAGE_LOAD_WAIT, FWXX_STARTUP_COUNTDOWN,
    FWXX_INPUT_DELAY_MIN, FWXX_INPUT_DELAY_MAX, FWXX_INPUT_PAUSE_PROB,
    FWXX_POST_SEARCH_WAIT, FWXX_DETAIL_CLICK_WAIT, FWXX_TAB_SWITCH_WAIT,
    FWXX_MENU_CLICK_WAIT, FWXX_CACHE_POLL_TIMEOUT, FWXX_DETAIL_CLOSE_WAIT,
    FWXX_ANTI_CRAWL_BATCH_SIZE, FWXX_ANTI_CRAWL_WAIT_MIN, FWXX_ANTI_CRAWL_WAIT_MAX,
)
from db_manager import PatentsDB

# PyAutoGUI 配置
pyautogui.PAUSE = PYAUTOGUI_PAUSE
pyautogui.FAILSAFE = PYAUTOGUI_FAILSAFE

# ============================================================================
# 常量和配置
# ============================================================================

SEARCH_PAGE_URL = CNIPA_URL

# 将 Path 对象转换为字符串（用于文件操作）
DETECTION_LOG_FILE = str(DETECTION_LOG_JSONL_FILE)
CONFIG_FILE = str(CONFIG_FILE)
CONFIG_FWXX_FILE = str(CONFIG_FWXX_FILE)
PATENT_CACHE_FILE = str(PATENT_CACHE_FILE)
PATENT_FWXX_CACHE_FILE = str(PATENT_FWXX_CACHE_FILE)
FWXX_UNMATCHED_FILE = str(FWXX_UNMATCHED_FILE)

# ============================================================================
# Part 1: 工具函数（复用现有防爬虫逻辑）
# ============================================================================



def countdown(seconds: int, message: str = "请手动记录坐标，倒计时"):
    """倒计时提示"""
    for i in range(seconds, 0, -1):
        print(f"\r{message}: {i:2d} 秒...", end="", flush=True)
        time.sleep(1)
    print(f"\r{message}: 0 秒...完成！    ")



# ============================================================================
# Part 2: 目标筛选函数
# ============================================================================

def load_target_applications() -> list:
    """
    从 PatentsDB 筛选待采集的目标申请号。

    筛选条件：
    1. anjianywzt == '驳回等复审请求'
    2. 发文信息尚未采集（支持断点续传）
    """
    db = PatentsDB(PATENTS_DB_FILE)
    summary = db.get_summary()
    total_bhsj = summary['rejection']
    targets = db.fwxx_uncollected_app_nos()
    already_collected = max(0, total_bhsj - len(targets))

    print("\n" + "="*60)
    print("📊 发文信息采集统计")
    print("="*60)
    print(f"✓ 驳回等复审请求: 共 {total_bhsj} 条")
    print(f"✓ 已采集发文: {already_collected} 条")
    print(f"⏳ 待采集发文: {len(targets)} 条")
    print("="*60 + "\n")

    if targets:
        print("待采集申请号列表:")
        for i, app_no in enumerate(targets[:10], 1):
            print(f"  {i}. {app_no}")
        if len(targets) > 10:
            print(f"  ... 及其他 {len(targets)-10} 个")
        print()

    return targets


def load_standalone_targets(
    input_file: str = None,
    app_nos: str = None,
    force: bool = False,
) -> list:
    """
    独立模式：直接从文件或命令行参数加载申请号列表

    不依赖 detection_log.json，不做状态筛选

    Args:
        input_file: 申请号列表文件路径（一行一个）
        app_nos: 逗号分隔的申请号字符串
        force: 不跳过已有完整详情信息的申请号

    Returns:
        申请号列表
    """
    targets = []

    if app_nos:
        # 从命令行参数解析
        targets = parse_app_no_list(app_nos)
        print(f"\n[*] 从命令行参数读取 {len(targets)} 个申请号")

    elif input_file:
        # 从文件读取
        if not os.path.exists(input_file):
            print(f"[!] 文件不存在: {input_file}")
            return []

        with open(input_file, 'r', encoding='utf-8') as f:
            targets = parse_app_no_list(f.read())
        print(f"\n[*] 从文件读取 {len(targets)} 个申请号: {input_file}")

    if targets:
        if force:
            print("[*] 强制采集：不按案件状态筛选，也不跳过已有发文记录")
        else:
            # 加载已采集的结果，支持断点续传
            collected = _load_standalone_collected()

            before = len(targets)
            targets = [t for t in targets if t not in collected]
            skipped = before - len(targets)

            if skipped > 0:
                print(f"[*] 跳过已采集: {skipped} 个（断点续传）")

        print(f"[*] 待采集: {len(targets)} 个")

        # 打印列表
        print("\n待采集申请号列表:")
        for i, app_no in enumerate(targets[:10], 1):
            print(f"  {i}. {app_no}")
        if len(targets) > 10:
            print(f"  ... 及其他 {len(targets)-10} 个")
        print()

    return targets


def _load_standalone_collected() -> set:
    """返回发文已采集的申请号，供独立模式断点续传。"""
    try:
        return PatentsDB(PATENTS_DB_FILE).fwxx_collected_app_nos()
    except Exception:
        return set()





# ============================================================================
# Part 4: 单个申请号采集流程
# ============================================================================

def collect_one_fwxx(
    driver,
    application_no: str,
    input_x: int,
    input_y: int,
    button_x: int,
    button_y: int,
    link_x: int,
    link_y: int,
    fwxx_menu_x: int,
    fwxx_menu_y: int,
) -> dict:
    """
    在详情页采集单个申请号的发文信息。

    步骤：
    1. 搜索申请号（PyAutoGUI 输入）
    2. 点击申请号链接进入详情页（新标签）
    3. 点击"发文信息"菜单
    4. 从缓存读取发文数据
    5. 关闭详情页标签，回到搜索页

    Args:
        driver: Selenium WebDriver 实例
        application_no: 申请号
        input_x, input_y: 搜索输入框坐标
        button_x, button_y: 查询按钮坐标
        link_x, link_y: 申请号链接坐标
        fwxx_menu_x, fwxx_menu_y: 发文信息菜单坐标

    Returns:
        本次成功采集到的字段，或 None
    """
    collected_fields = {}
    detail_attempt = None
    detail_handle = None
    search_handle = None
    try:
        # 检测浏览器是否还活着
        if not is_browser_alive(driver):
            raise DetailCollectionFatalError('浏览器已关闭，本条未采集，发文批次已中断')

        initial_handles = list(driver.window_handles)
        if len(initial_handles) != 1:
            raise DetailCollectionFatalError("发文采集开始时不是唯一搜索页，已停止批次")
        search_handle = initial_handles[0]
        driver.switch_to.window(search_handle)

        print(f"\n  [{application_no}] 开始采集发文信息...")

        # 清理缓存中的旧数据（防止脏数据干扰）
        try:
            clear_cache_key(PATENT_FWXX_CACHE_FILE, application_no)
        except Exception as error:
            print(f"    [!] 无法清理旧发文缓存，已停止本件采集: {error}")
            return None

        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # 步骤 1：搜索申请号（复用 PyAutoGUI 防爬虫逻辑）
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

        print(f"    [*] 输入申请号...")

        # 输入申请号并点击查询（保持原始防爬虫延迟）
        print(f"    [*] 点击查询按钮...")
        InputService.type_in_search(
            input_x, input_y, button_x, button_y, application_no,
            delay_range=(FWXX_INPUT_DELAY_MIN, FWXX_INPUT_DELAY_MAX),
            pause_prob=FWXX_INPUT_PAUSE_PROB,
            post_search_wait=FWXX_POST_SEARCH_WAIT,
        )

        # 验证搜索结果是否正常（检查页面是否有异常提示）
        try:
            # 检查页面上是否存在常见的"无结果"提示
            page_text = driver.page_source.lower()
            if any(keyword in page_text for keyword in ['无查询结果', '无搜索结果', '请输入查询', '没有找到']):
                print(f"    [!] 搜索无结果或出现异常提示")
                print(f"    [*] 跳过此申请号，继续下一个...")
                return None
        except Exception:
            pass

        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # 步骤 2：点击申请号链接进入详情页（新标签）
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

        # 自动点击申请号链接
        print(f"    [*] 点击申请号链接进入详情页...")
        detail_attempt = begin_detail_attempt(application_no)

        InputService.move_and_click(link_x, link_y, post_click_wait=FWXX_DETAIL_CLICK_WAIT)

        new_handles = [handle for handle in driver.window_handles if handle != search_handle]
        if len(new_handles) != 1:
            raise DetailCollectionFatalError("发文详情页未唯一打开，已停止批次")

        detail_handle = new_handles[0]
        driver.switch_to.window(detail_handle)
        time.sleep(FWXX_TAB_SWITCH_WAIT)
        wait_for_detail_identity(detail_attempt)
        print("    [✓] 官方申请号已确认，开始采集发文")

        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # 步骤 4：点击"发文信息"菜单
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

        print(f"    [*] 点击'发文信息'菜单...")
        InputService.move_and_click(
            fwxx_menu_x,
            fwxx_menu_y,
            post_click_wait=FWXX_MENU_CLICK_WAIT,
        )

        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # 步骤 5：从缓存读取发文信息（轮询等待）
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

        print(f"    [*] 从 MITM 缓存读取发文信息...")
        fwxx_data = poll_cache_for_key(
            PATENT_FWXX_CACHE_FILE,
            application_no,
            max_wait=FWXX_CACHE_POLL_TIMEOUT,
            validate=partial(
                matches_detail_attempt,
                expected_attempt_id=detail_attempt['attempt_id'],
            ),
        )

        if not fwxx_data:
            print(f"    [!] 未从缓存中获得发文信息")
            # 降级处理：关闭标签但继续
        else:
            print(f"    [✓] 成功读取发文信息")
            collected_fields.update({
                field: value for field, value in fwxx_data.items()
                if field != 'detail_attempt_id'
            })
        return collected_fields or None

    except DetailCollectionFatalError:
        raise
    except WebDriverException as error:
        raise DetailCollectionFatalError('浏览器连接失效，发文批次已中断') from error
    except Exception as e:
        print(f"    [!] 采集失败: {str(e)[:100]}")
        return collected_fields or None
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
                    raise DetailCollectionFatalError("发文详情页关闭后未恢复唯一搜索页")
                driver.switch_to.window(search_handle)
                time.sleep(FWXX_TAB_SWITCH_WAIT)
            except DetailCollectionFatalError:
                raise
            except Exception as error:
                raise DetailCollectionFatalError("无法确认发文详情页已关闭，已停止批次") from error


# ============================================================================
# Part 5: 日志更新函数
# ============================================================================

def persist_fwxx_fields(application_no: str, fwxx_fields: dict) -> bool:
    """将本次采集成功的发文字段写回 PatentsDB。

    使用 update_fields（字段级更新）而非 upsert（整行覆盖），
    避免与 main_automation.py 的并发写入互相覆盖。基础状态的 timestamp
    只由主采集更新，发文采集使用独立时间，避免推迟策略复查。
    """
    try:
        db = PatentsDB(PATENTS_DB_FILE)
        if db.get_record(application_no) is None:
            print(f"    [!] {application_no} 不在 DB 中，写入 {FWXX_UNMATCHED_FILE}")
            _append_unmatched(application_no, fwxx_fields, reason='not_found_in_db')
            return False

        persisted_fields = {
            field: fwxx_fields[field]
            for field in (
                'fwxx_list',
                'bhsjtzs_xiazaisj',
                'bhsjtzs_data',
            )
            if field in fwxx_fields
        }
        persisted_fields['fwxx_collected_at'] = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
        db.update_fields(application_no, persisted_fields)
        return True
    except Exception as e:
        print(f"    [!] 发文字段更新失败: {e}")
        _append_unmatched(application_no, fwxx_fields, reason=f'update_failed: {e}')
        return False


def _append_unmatched(application_no: str, fwxx_fields: dict, reason: str = '') -> None:
    """将无法匹配到主库的发文字段追加到 unmatched 文件。"""
    try:
        if os.path.exists(FWXX_UNMATCHED_FILE):
            with open(FWXX_UNMATCHED_FILE, 'r', encoding='utf-8') as f:
                unmatched_payload = json.load(f)
        else:
            unmatched_payload = {'records': []}
        unmatched_record = {
            'application_no': application_no,
            'reason': reason,
        }
        for field in ('fwxx_list', 'bhsjtzs_xiazaisj', 'bhsjtzs_data'):
            if field in fwxx_fields:
                unmatched_record[field] = fwxx_fields[field]
        unmatched_payload['records'].append(unmatched_record)
        write_json_atomic(FWXX_UNMATCHED_FILE, unmatched_payload)
    except Exception as e:
        print(f"    [!] 写入 unmatched 失败: {e}")


def update_detection_log(application_no: str, fwxx_fields: dict) -> bool:
    """保留旧入口名，其写入范围已收窄为发文字段。"""
    return persist_fwxx_fields(application_no, fwxx_fields)


# ============================================================================
# Part 6: 主循环函数
# ============================================================================

def run_fwxx_collection(args) -> None:
    """独占共享桌面并执行完整发文采集。"""
    with reserve_detail_collection_desktop("发文信息采集"):
        _run_fwxx_collection(args)


def _run_fwxx_collection(args) -> None:
    """
    发文信息采集主循环

    Args:
        args: 命令行参数对象
    """
    if getattr(args, 'resume_batch', None):
        with CollectionBatch.resume('fwxx', FWXX_COLLECTION_CHECKPOINT_FILE, args.resume_batch) as checkpoint:
            _collect_fwxx_batch(args, checkpoint)
        return
    standalone_mode = bool(getattr(args, 'input', None) or getattr(args, 'app', None))

    print("\n" + "="*70)
    if standalone_mode:
        print("🚀 发文信息采集程序启动（独立模式）")
    else:
        print("🚀 发文信息采集程序启动")
    print("="*70)

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # 步骤 1：筛选目标申请号
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    if standalone_mode:
        # 独立模式：从文件或命令行参数加载
        targets = load_standalone_targets(
            input_file=getattr(args, 'input', None),
            app_nos=getattr(args, 'app', None),
            force=bool(getattr(args, 'force', False)),
        )
    else:
        # 自动模式：从 PatentsDB 筛选待采发文
        targets = load_target_applications()

    if not targets:
        if standalone_mode:
            print("✓ 无待采集的申请号（可能已全部采集完毕）")
        else:
            print("✓ 无需采集，所有驳回案件的发文信息都已采集！")
        return

    with CollectionBatch.create('fwxx', FWXX_COLLECTION_CHECKPOINT_FILE, targets) as checkpoint:
        _collect_fwxx_batch(args, checkpoint)


def _collect_fwxx_batch(args, checkpoint: CollectionBatch) -> None:
    targets = checkpoint.select_pending(args.test)

    # 测试模式
    if args.test:
        print(f"📋 测试模式：仅采集前 {len(targets)} 个\n")

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # 步骤 2：创建浏览器
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    driver = None
    try:
        # 步骤 3：创建浏览器，打开搜索页，等待用户登录
        print(f"\n[*] 打开搜索页: {args.url}")
        driver = BrowserService.launch_and_login(args.url, page_load_wait=FWXX_PAGE_LOAD_WAIT)

        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # 步骤 4：加载坐标配置
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

        # 搜索页坐标
        print("\n[*] 正在加载坐标配置...")
        input_x, input_y, button_x, button_y = CoordinateService.load_or_record_search_coordinates()

        print("\n[*] 现在需要记录发文信息页面的坐标...")
        print("[*] 请确保浏览器已登录并正常显示搜索页")

        # 发文信息坐标
        link_x, link_y, fwxx_menu_x, fwxx_menu_y = CoordinateService.load_or_record_fwxx_coordinates()

        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # 步骤 5：倒计时
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

        countdown(FWXX_STARTUP_COUNTDOWN, "坐标已记录，即将开始自动采集，倒计时")

        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # 步骤 6：主循环 - 逐个采集
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

        print("\n" + "="*70)
        print("采集进度")
        print("="*70)

        success_count = 0
        failed_count = 0
        failure_streak = CollectionFailureStreak('发文信息采集')

        for idx, application_no in enumerate(targets, 1):
            # 检测浏览器是否还活着
            if not is_browser_alive(driver):
                print("\n⚠️  浏览器已关闭，停止采集")
                print(f"\n已采集 {success_count} 条，失败 {failed_count} 条，还有 {len(targets) - idx + 1} 条未采集")
                raise DetailCollectionFatalError('浏览器进程意外退出，发文采集已中断')

            print(f"\n[{idx}/{len(targets)}] 申请号: {application_no}")

            # 采集单个申请号
            checkpoint.record_started(application_no)
            fwxx_fields = collect_one_fwxx(
                driver=driver,
                application_no=application_no,
                input_x=input_x,
                input_y=input_y,
                button_x=button_x,
                button_y=button_y,
                link_x=link_x,
                link_y=link_y,
                fwxx_menu_x=fwxx_menu_x,
                fwxx_menu_y=fwxx_menu_y,
            )

            # 仅更新发文字段
            if fwxx_fields:
                if persist_fwxx_fields(application_no, fwxx_fields):
                    print(f"  ✅ 已成功采集并更新日志")
                    success_count += 1
                    checkpoint.record_success(application_no)
                    failure_streak.record_success()
                else:
                    print(f"  ⚠️  主日志未更新，发文信息已备份到 {FWXX_UNMATCHED_FILE}")
                    failed_count += 1
                    checkpoint.record_failure(application_no, '发文数据未写入专利主库，已保存未匹配备份')
                    failure_streak.record_failure()
            else:
                print(f"  ❌ 未采集到数据")
                failed_count += 1
                checkpoint.record_failure(application_no, '未采集到有效发文数据')
                failure_streak.record_failure()

            # 申请号之间的随机延迟（防反爬）
            if idx % FWXX_ANTI_CRAWL_BATCH_SIZE == 0 and idx < len(targets):
                wait_time = random.uniform(FWXX_ANTI_CRAWL_WAIT_MIN, FWXX_ANTI_CRAWL_WAIT_MAX)
                print(f"\n  [*] 防爬虫等待 {wait_time:.1f} 秒...")
                time.sleep(wait_time)

        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # 步骤 7：导出 Excel
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

        print("\n" + "="*70)
        print(f"采集批次结束，成功: {success_count}, 失败: {failed_count}")
        print("="*70)

        print("\n[*] 导出 Excel...")
        logger = DetectionLogger()
        if logger.export_to_excel():
            print("[✓] Excel 导出成功!")

        # 刷新 JSONL 备份（含本次写入的 fwxx 数据），保证 sync push 时内容完整
        from settings import PATENTS_DB_FILE, DETECTION_LOG_JSONL_FILE
        from db_manager import PatentsDB
        exported = PatentsDB(PATENTS_DB_FILE).export_to_jsonl(DETECTION_LOG_JSONL_FILE)
        print(f"[✓] JSONL 备份已刷新：{exported} 条（含发文信息）")
        if failed_count:
            raise RuntimeError(
                f'发文采集失败 {failed_count} 条，未完成清单: {FWXX_COLLECTION_CHECKPOINT_FILE}'
            )

    except CollectionFailureStreakExceeded:
        # 熔断信息已由 CollectionFailureStreak 打点并写入报警，无需 traceback
        raise
    except Exception as e:
        print(f"\n[!] 采集过程出错: {e}")
        import traceback
        traceback.print_exc()
        raise

    finally:
        if checkpoint.remaining_count:
            print(f"\n[*] 未完成 {checkpoint.remaining_count} 条，清单已保存: {FWXX_COLLECTION_CHECKPOINT_FILE}")
            print(f'    续跑命令: python collect_fwxx.py --resume-batch {checkpoint.id}')
        # 清理
        if driver:
            try:
                driver.quit()
            except Exception:
                pass

        print("\n[✓] 程序结束")


# ============================================================================
# Part 7: 入口点
# ============================================================================

if __name__ == "__main__":
    raise_system_exit_on_sigterm()
    parser = argparse.ArgumentParser(
        description="发文信息采集模块"
    )
    parser.add_argument(
        '--test',
        type=int,
        help='测试模式，仅采集前 N 个申请号'
    )
    parser.add_argument(
        '--url',
        type=str,
        default=SEARCH_PAGE_URL,
        help=f'搜索页 URL（默认：{SEARCH_PAGE_URL}）'
    )
    target_source = parser.add_mutually_exclusive_group()
    target_source.add_argument(
        '--input',
        type=str,
        help='独立模式：从文件读取申请号列表（一行一个）'
    )
    target_source.add_argument(
        '--app',
        type=str,
        help='独立模式：直接指定申请号（多个用逗号分隔）'
    )
    target_source.add_argument('--resume-batch', metavar='ID', help='继续指定的未完成发文批次')
    parser.add_argument(
        '--force',
        action='store_true',
        help='独立模式：不按案件状态筛选，也不跳过已有发文记录'
    )

    args = parser.parse_args()

    # 检查环境变量
    if not USE_MITM_PROXY:
        print("\n[!] MITM 代理未启用，发文采集依赖代理拦截，无法继续")
        print("    请先启动代理后重试：")
        print("      python start_mitm_proxy.py")
        print("    或设置环境变量：USE_MITM_PROXY=true python collect_fwxx.py")
        sys.exit(1)

    try:
        run_fwxx_collection(args=args)
    except (DetailCollectionDesktopBusyError, CollectionBatchBusyError, ValueError) as error:
        print(f"\n[!] {error}")
        sys.exit(2)
    except CollectionFailureStreakExceeded as error:
        print(f"\n⛔ {error}")
        sys.exit(3)
