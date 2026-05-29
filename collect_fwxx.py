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
  # 正常运行（从 detection_log.json 筛选）
  USE_MITM_PROXY=true python collect_fwxx.py

  # 测试模式（仅采集 3 个）
  USE_MITM_PROXY=true python collect_fwxx.py --test 3

  # 独立模式：直接指定申请号列表文件
  USE_MITM_PROXY=true python collect_fwxx.py --input data/fwxx_list.txt

  # 独立模式：直接指定单个申请号
  USE_MITM_PROXY=true python collect_fwxx.py --app 2022108424726

  # 独立模式：指定多个申请号（逗号分隔）
  USE_MITM_PROXY=true python collect_fwxx.py --app 2022108424726,2023101501868

架构设计：
  1. 筛选目标：从 detection_log.json 中找出待采集的申请号
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
from datetime import datetime
from pathlib import Path

# 虚拟显示器必须在 pyautogui / Xlib 任何 import 之前启动
# XVFB_EXTERNAL=true 表示 Xvfb 已由外部（docker-entrypoint.sh）管理，跳过重复启动
if os.getenv('USE_VIRTUAL_DISPLAY', '').lower() in ('true', '1', 'yes') \
        and sys.platform.startswith('linux') \
        and os.getenv('XVFB_EXTERNAL', '').lower() not in ('true', '1', 'yes'):
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
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import undetected_chromedriver as uc

# 导入现有模块
sys.path.insert(0, os.path.dirname(__file__))
from detection_logger import DetectionLogger
from browser_utils import (
    is_browser_alive, create_driver_with_retry,
)
from coordinate_service import CoordinateService
from browser_service import BrowserService
from input_service import InputService
from cache_utils import read_json_cache, write_json_cache, poll_cache_for_key, clear_cache_key
from settings import (
    CNIPA_URL, DETECTION_LOG_JSONL_FILE, CONFIG_FILE, CONFIG_FWXX_FILE,
    PATENT_CACHE_FILE, PATENT_FWXX_CACHE_FILE, MARKER_FILE,
    FWXX_UNMATCHED_FILE, PYAUTOGUI_PAUSE, PYAUTOGUI_FAILSAFE,
    MITM_TIMEOUT, MITM_POLL_INTERVAL, USE_MITM_PROXY,
    FWXX_TRIGGER_ANJIANYWZT, PATENTS_DB_FILE,
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
MARKER_FILE = str(MARKER_FILE)
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
    2. fwxx_list IS NULL（支持断点续传）
    """
    db = PatentsDB(PATENTS_DB_FILE)
    summary = db.get_summary()
    total_bhsj = summary['rejection']
    already_collected = summary['fwxx_collected']
    targets = db.fwxx_uncollected_app_nos()

    print("\n" + "="*60)
    print("📊 发文信息采集统计")
    print("="*60)
    print(f"✓ 驳回等复审请求: 共 {total_bhsj} 条")
    print(f"✓ 已采集发文信息: {already_collected} 条")
    print(f"⏳ 待采集: {len(targets)} 条")
    print("="*60 + "\n")

    if targets:
        print("待采集申请号列表:")
        for i, app_no in enumerate(targets[:10], 1):
            print(f"  {i}. {app_no}")
        if len(targets) > 10:
            print(f"  ... 及其他 {len(targets)-10} 个")
        print()

    return targets


def load_standalone_targets(input_file: str = None, app_nos: str = None) -> list:
    """
    独立模式：直接从文件或命令行参数加载申请号列表

    不依赖 detection_log.json，不做状态筛选

    Args:
        input_file: 申请号列表文件路径（一行一个）
        app_nos: 逗号分隔的申请号字符串

    Returns:
        申请号列表
    """
    targets = []

    if app_nos:
        # 从命令行参数解析
        targets = [no.strip() for no in app_nos.split(',') if no.strip()]
        print(f"\n[*] 从命令行参数读取 {len(targets)} 个申请号")

    elif input_file:
        # 从文件读取
        if not os.path.exists(input_file):
            print(f"[!] 文件不存在: {input_file}")
            return []

        with open(input_file, 'r', encoding='utf-8') as f:
            targets = [line.strip() for line in f if line.strip()]
        print(f"\n[*] 从文件读取 {len(targets)} 个申请号: {input_file}")

    if targets:
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
    """返回 detection_log 中已有 fwxx_list 的申请号（断点续传用）"""
    try:
        logger = DetectionLogger()
        return {r['application_no'] for r in logger._load_records() if r.get('fwxx_list') is not None}
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
    采集单个申请号的发文信息

    步骤：
    1. 搜索申请号（PyAutoGUI 输入）
    2. 点击申请号链接进入详情页（新标签）
    3. 点击"发文信息"菜单
    4. 标记当前申请号
    5. MITM 拦截 API
    6. 从缓存读取数据
    7. 关闭详情页标签，回到搜索页

    Args:
        driver: Selenium WebDriver 实例
        application_no: 申请号
        input_x, input_y: 搜索输入框坐标
        button_x, button_y: 查询按钮坐标
        link_x, link_y: 申请号链接坐标
        fwxx_menu_x, fwxx_menu_y: 发文信息菜单坐标

    Returns:
        发文信息字典，或 None 失败
    """
    try:
        # 检测浏览器是否还活着
        if not is_browser_alive(driver):
            print(f"    [!] 浏览器已关闭，无法采集")
            return None

        print(f"\n  [{application_no}] 开始采集发文信息...")

        # 清理缓存中的旧数据（防止脏数据干扰）
        try:
            clear_cache_key(PATENT_FWXX_CACHE_FILE, application_no)
        except Exception:
            pass

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

        # 记录点击前的标签数量（用于检测是否成功打开新标签）
        tabs_before = len(driver.window_handles)

        InputService.move_and_click(link_x, link_y, post_click_wait=FWXX_DETAIL_CLICK_WAIT)

        # 检查是否打开了新标签页
        tabs_after = len(driver.window_handles)
        if tabs_after <= tabs_before:
            # 没有打开新标签 → 搜索失败、无结果或点击无效
            print(f"    [!] 搜索结果无效或点击失败（标签数未增加）")
            print(f"    [*] 跳过此申请号，继续下一个...")
            return None

        # Selenium 切换到新标签页（⚠️ 关键）
        driver.switch_to.window(driver.window_handles[-1])
        time.sleep(FWXX_TAB_SWITCH_WAIT)
        print(f"    [✓] 已切换到详情页标签")

        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # 步骤 3：写入申请号标记文件（解决 MITM 关联问题）
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

        # ⚠️ 在点击"发文信息"之前标记申请号
        with open(MARKER_FILE, 'w', encoding='utf-8') as f:
            json.dump({'application_no': application_no}, f)
        print(f"    [*] 标记申请号: {application_no}")

        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # 步骤 4：点击"发文信息"菜单
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

        print(f"    [*] 点击'发文信息'菜单...")
        InputService.move_and_click(fwxx_menu_x, fwxx_menu_y, post_click_wait=FWXX_MENU_CLICK_WAIT)

        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # 步骤 5：从缓存读取发文信息（轮询等待）
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

        print(f"    [*] 从 MITM 缓存读取发文信息...")
        fwxx_data = poll_cache_for_key(PATENT_FWXX_CACHE_FILE, application_no, max_wait=FWXX_CACHE_POLL_TIMEOUT)

        if not fwxx_data:
            print(f"    [!] 未从缓存中获得发文信息")
            # 降级处理：关闭标签但继续
        else:
            print(f"    [✓] 成功读取发文信息")

        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # 步骤 6：关闭详情页标签，回到搜索页
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

        # 只有在有多个标签时才关闭
        if len(driver.window_handles) > 1:
            print(f"    [*] 关闭详情页标签...")
            pyautogui.hotkey('ctrl', 'w')
            time.sleep(FWXX_DETAIL_CLOSE_WAIT)

            # Selenium 切回搜索页标签
            driver.switch_to.window(driver.window_handles[0])
            time.sleep(FWXX_TAB_SWITCH_WAIT)
            print(f"    [✓] 已回到搜索页")
        else:
            print(f"    [⚠️ ] 没有多余标签可关闭")

        return fwxx_data

    except Exception as e:
        print(f"    [!] 采集失败: {str(e)[:100]}")
        # 清理所有多余的标签页，回到搜索页
        try:
            while len(driver.window_handles) > 1:
                driver.switch_to.window(driver.window_handles[-1])
                pyautogui.hotkey('ctrl', 'w')
                time.sleep(FWXX_TAB_SWITCH_WAIT)
            driver.switch_to.window(driver.window_handles[0])
        except Exception:
            pass
        return None


# ============================================================================
# Part 5: 日志更新函数
# ============================================================================

def update_detection_log(application_no: str, fwxx_data: dict) -> bool:
    """
    将采集到的发文信息写回 PatentsDB。

    Returns:
        成功返回 True；申请号不在 DB 中返回 False。
    """
    try:
        db = PatentsDB(PATENTS_DB_FILE)
        record = db.get_record(application_no)
        if record is None:
            print(f"    [!] {application_no} 不在 DB 中，写入 {FWXX_UNMATCHED_FILE}")
            _append_unmatched(application_no, fwxx_data)
            return False

        record['fwxx_list'] = fwxx_data.get('fwxx_list')
        record['bhsjtzs_xiazaisj'] = fwxx_data.get('bhsjtzs_xiazaisj')
        record['bhsjtzs_data'] = fwxx_data.get('bhsjtzs_data')
        db.upsert(record)
        return True
    except Exception as e:
        print(f"    [!] 日志更新失败: {e}")
        return False


def _append_unmatched(application_no: str, fwxx_data: dict) -> None:
    """将无法匹配到 detection_log 的游离 fwxx 数据追加到 unmatched 文件"""
    try:
        if os.path.exists(FWXX_UNMATCHED_FILE):
            with open(FWXX_UNMATCHED_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
        else:
            data = {'records': []}
        data['records'].append({
            'application_no': application_no,
            'fwxx_list': fwxx_data.get('fwxx_list'),
            'bhsjtzs_xiazaisj': fwxx_data.get('bhsjtzs_xiazaisj'),
            'bhsjtzs_data': fwxx_data.get('bhsjtzs_data'),
        })
        tmp = FWXX_UNMATCHED_FILE + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, FWXX_UNMATCHED_FILE)
    except Exception as e:
        print(f"    [!] 写入 unmatched 失败: {e}")


# ============================================================================
# Part 6: 主循环函数
# ============================================================================

def run_fwxx_collection(args) -> None:
    """
    发文信息采集主循环

    Args:
        args: 命令行参数对象
    """
    test_count = args.test
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
        )
    else:
        # 原有模式：从 detection_log.json 筛选
        targets = load_target_applications()

    if not targets:
        if standalone_mode:
            print("✓ 无待采集的申请号（可能已全部采集完毕）")
        else:
            print("✓ 无需采集，所有驳回案件的发文信息都已采集！")
        return

    # 测试模式
    if test_count:
        targets = targets[:test_count]
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

        for idx, application_no in enumerate(targets, 1):
            # 检测浏览器是否还活着
            if not is_browser_alive(driver):
                print("\n⚠️  浏览器已关闭，停止采集")
                print(f"\n已采集 {success_count} 条，失败 {failed_count} 条，还有 {len(targets) - idx + 1} 条未采集")
                break

            print(f"\n[{idx}/{len(targets)}] 申请号: {application_no}")

            # 采集单个申请号
            fwxx_data = collect_one_fwxx(
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

            # 更新日志（统一写入 detection_log）
            if fwxx_data:
                if update_detection_log(application_no, fwxx_data):
                    print(f"  ✅ 已成功采集并更新日志")
                    success_count += 1
                else:
                    print(f"  ⚠️  申请号不在 detection_log 中，已写入 {FWXX_UNMATCHED_FILE}")
                    failed_count += 1
            else:
                print(f"  ❌ 未采集到数据")
                failed_count += 1

            # 申请号之间的随机延迟（防反爬）
            if idx % FWXX_ANTI_CRAWL_BATCH_SIZE == 0 and idx < len(targets):
                wait_time = random.uniform(FWXX_ANTI_CRAWL_WAIT_MIN, FWXX_ANTI_CRAWL_WAIT_MAX)
                print(f"\n  [*] 防爬虫等待 {wait_time:.1f} 秒...")
                time.sleep(wait_time)

        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # 步骤 7：导出 Excel
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

        print("\n" + "="*70)
        print(f"✓ 采集完成！成功: {success_count}, 失败: {failed_count}")
        print("="*70)

        print("\n[*] 导出 Excel...")
        logger = DetectionLogger()
        if logger.export_to_excel():
            print("[✓] Excel 导出成功!")

    except Exception as e:
        print(f"\n[!] 采集过程出错: {e}")
        import traceback
        traceback.print_exc()

    finally:
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
    parser.add_argument(
        '--input',
        type=str,
        help='独立模式：从文件读取申请号列表（一行一个）'
    )
    parser.add_argument(
        '--app',
        type=str,
        help='独立模式：直接指定申请号（多个用逗号分隔）'
    )

    args = parser.parse_args()

    # 检查环境变量
    if not os.getenv('USE_MITM_PROXY', '').lower() in ('true', '1', 'yes'):
        print("\n警告: MITM 代理未启用")
        print("提示: 如果采集失败，可以启动代理后重试")
        print("  1. 启动代理: python start_mitm_proxy.py")
        print("  2. 重新运行: USE_MITM_PROXY=true python collect_fwxx.py\n")
        response = input("是否继续不使用代理？(y/N): ").strip().lower()
        if response != 'y':
            sys.exit(1)

    run_fwxx_collection(args=args)
