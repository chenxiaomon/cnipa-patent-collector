#!/usr/bin/env python
# -*- coding: utf-8 -*-
import sys
import io
# 解决 Windows 控制台编码问题
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

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
import sys
import time
import random
import argparse
from datetime import datetime
from pathlib import Path

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
    is_browser_alive, real_type, create_driver_with_retry,
    auto_fill_login, load_credentials, clear_input_field,
)
from cache_utils import read_json_cache, write_json_cache, poll_cache_for_key
from settings import (
    CNIPA_URL, DETECTION_LOG_FILE, CONFIG_FILE, CONFIG_FWXX_FILE,
    PATENT_CACHE_FILE, PATENT_FWXX_CACHE_FILE, MARKER_FILE,
    FWXX_UNMATCHED_FILE, PYAUTOGUI_PAUSE, PYAUTOGUI_FAILSAFE,
    MITM_TIMEOUT, MITM_POLL_INTERVAL, USE_MITM_PROXY,
    FWXX_TRIGGER_ANJIANYWZT
)

# PyAutoGUI 配置
pyautogui.PAUSE = PYAUTOGUI_PAUSE
pyautogui.FAILSAFE = PYAUTOGUI_FAILSAFE

# ============================================================================
# 常量和配置
# ============================================================================

SEARCH_PAGE_URL = CNIPA_URL

# 将 Path 对象转换为字符串（用于文件操作）
DETECTION_LOG_FILE = str(DETECTION_LOG_FILE)
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
    从 detection_log.json 中筛选待采集的目标申请号

    筛选条件：
    1. anjianywzt == '驳回等复审请求'（案件业务状态）
    2. fwxx_list is None（支持断点续传）

    Returns:
        待采集的申请号列表
    """
    if not os.path.exists(DETECTION_LOG_FILE):
        print(f"[!] 采集日志文件不存在: {DETECTION_LOG_FILE}")
        return []

    with open(DETECTION_LOG_FILE, 'r', encoding='utf-8') as f:
        log_data = json.load(f)

    records = log_data.get('records', [])

    # 统计各类型案件
    total_bhsj = 0  # "驳回等复审请求" 总数
    already_collected = 0  # 已采集发文信息
    targets = []  # 待采集列表

    for record in records:
        anjianywzt = record.get('anjianywzt')  # 案件业务状态
        fwxx_list = record.get('fwxx_list')
        app_no = record.get('application_no')

        # 筛选"驳回等复审请求"状态（使用 anjianywzt 而不是 falvzt）
        if anjianywzt == '驳回等复审请求':
            total_bhsj += 1

            # 检查是否已采集
            if fwxx_list is not None:
                already_collected += 1
            else:
                targets.append(app_no)

    # 打印统计信息
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
    if not os.path.exists(DETECTION_LOG_FILE):
        return set()
    try:
        with open(DETECTION_LOG_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return {r['application_no'] for r in data.get('records', []) if r.get('fwxx_list') is not None}
    except:
        return set()




# ============================================================================
# Part 3: 坐标配置函数
# ============================================================================

def load_or_record_fwxx_positions() -> dict:
    """
    加载或手动记录发文信息采集所需的坐标

    需要记录的两个坐标：
    1. link_x, link_y: 搜索结果中申请号链接的位置
    2. fwxx_menu_x, fwxx_menu_y: 详情页左侧"发文信息"菜单的位置

    Returns:
        包含坐标的字典
    """
    # 尝试从配置文件加载
    if os.path.exists(CONFIG_FWXX_FILE):
        try:
            with open(CONFIG_FWXX_FILE, 'r', encoding='utf-8') as f:
                config = json.load(f)
                print("\n✓ 从配置文件加载发文信息坐标")
                print(f"  申请号链接: ({config['link_x']}, {config['link_y']})")
                print(f"  发文菜单: ({config['fwxx_menu_x']}, {config['fwxx_menu_y']})")
                print()
                return config
        except Exception as e:
            print(f"⚠️  配置文件读取失败: {e}\n")

    # 自动记录坐标（使用 PyAutoGUI，与 main_automation.py 一致）
    print("\n" + "="*70)
    print("⚠️  需要自动记录 2 个坐标")
    print("="*70)

    print("\n【记录坐标 A】申请号链接位置")
    print("  1. 在浏览器中搜索一个申请号，使搜索结果显示")
    print("  2. 将鼠标移动到搜索结果中的申请号链接上")
    print("  倒计时开始（请保持鼠标位置）...")
    countdown(8, "准备读取申请号链接坐标，倒计时")

    # 获取坐标 A（自动读取）
    link_x, link_y = pyautogui.position()
    print(f"\n✓ 申请号链接坐标已记录: ({link_x}, {link_y})")

    print("\n【记录坐标 B】发文信息菜单位置")
    print("  1. 在搜索结果中点击申请号，进入详情页")
    print("  2. 等待详情页加载完成")
    print("  3. 在左侧菜单栏中找到'发文信息'文字")
    print("  4. 将鼠标移动到'发文信息'上")
    print("  倒计时开始（请保持鼠标位置）...")
    countdown(8, "准备读取发文菜单坐标，倒计时")

    # 获取坐标 B（自动读取）
    fwxx_menu_x, fwxx_menu_y = pyautogui.position()
    print(f"\n✓ 发文菜单坐标已记录: ({fwxx_menu_x}, {fwxx_menu_y})")

    # 保存配置
    config = {
        'link_x': link_x,
        'link_y': link_y,
        'fwxx_menu_x': fwxx_menu_x,
        'fwxx_menu_y': fwxx_menu_y,
        'last_updated': datetime.now().isoformat()
    }

    try:
        with open(CONFIG_FWXX_FILE, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
        print(f"\n✓ 坐标已保存到 {CONFIG_FWXX_FILE}")
    except Exception as e:
        print(f"\n[!] 保存配置失败: {e}")

    return config


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
            cache = read_json_cache(PATENT_FWXX_CACHE_FILE)
            if application_no in cache:
                del cache[application_no]
                write_json_cache(PATENT_FWXX_CACHE_FILE, cache)
        except Exception:
            pass

        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # 步骤 1：搜索申请号（复用 PyAutoGUI 防爬虫逻辑）
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

        print(f"    [*] 输入申请号...")

        # 点击输入框
        pyautogui.moveTo(input_x, input_y, duration=random.uniform(0.3, 0.5))
        time.sleep(random.uniform(0.1, 0.2))
        pyautogui.click()
        time.sleep(0.5)

        # 清空输入框
        clear_input_field()

        # 输入申请号（保持原始防爬虫延迟）
        real_type(application_no, delay_range=(0.05, 0.18), pause_prob=0.15)
        time.sleep(random.uniform(0.5, 1))

        # 自动点击查询按钮
        print(f"    [*] 点击查询按钮...")
        pyautogui.moveTo(button_x, button_y, duration=random.uniform(0.3, 0.5))
        time.sleep(random.uniform(0.1, 0.3))
        pyautogui.click()
        time.sleep(3)  # 等待搜索结果

        # 验证搜索结果是否正常（检查页面是否有异常提示）
        try:
            # 检查页面上是否存在常见的"无结果"提示
            page_text = driver.page_source.lower()
            if any(keyword in page_text for keyword in ['无查询结果', '无搜索结果', '请输入查询', '没有找到']):
                print(f"    [!] 搜索无结果或出现异常提示")
                print(f"    [*] 跳过此申请号，继续下一个...")
                return None
        except:
            pass

        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # 步骤 2：点击申请号链接进入详情页（新标签）
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

        # 自动点击申请号链接
        print(f"    [*] 点击申请号链接进入详情页...")

        # 记录点击前的标签数量（用于检测是否成功打开新标签）
        tabs_before = len(driver.window_handles)

        pyautogui.moveTo(link_x, link_y, duration=random.uniform(0.3, 0.5))
        time.sleep(random.uniform(0.1, 0.3))
        pyautogui.click()
        time.sleep(4)  # 详情页加载较慢

        # 检查是否打开了新标签页
        tabs_after = len(driver.window_handles)
        if tabs_after <= tabs_before:
            # 没有打开新标签 → 搜索失败、无结果或点击无效
            print(f"    [!] 搜索结果无效或点击失败（标签数未增加）")
            print(f"    [*] 跳过此申请号，继续下一个...")
            return None

        # Selenium 切换到新标签页（⚠️ 关键）
        driver.switch_to.window(driver.window_handles[-1])
        time.sleep(0.5)
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
        pyautogui.moveTo(fwxx_menu_x, fwxx_menu_y, duration=random.uniform(0.3, 0.5))
        time.sleep(random.uniform(0.1, 0.3))
        pyautogui.click()
        time.sleep(3)  # 等待 API 响应

        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # 步骤 5：从缓存读取发文信息（轮询等待）
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

        print(f"    [*] 从 MITM 缓存读取发文信息...")
        fwxx_data = poll_cache_for_key(PATENT_FWXX_CACHE_FILE, application_no, max_wait=10)

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
            time.sleep(1)

            # Selenium 切回搜索页标签
            driver.switch_to.window(driver.window_handles[0])
            time.sleep(0.5)
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
                time.sleep(0.5)
            driver.switch_to.window(driver.window_handles[0])
        except:
            pass
        return None


# ============================================================================
# Part 5: 日志更新函数
# ============================================================================

def update_detection_log(application_no: str, fwxx_data: dict) -> bool:
    """
    更新 detection_log.json，填充发文信息字段

    Args:
        application_no: 申请号
        fwxx_data: 发文信息字典

    Returns:
        成功返回 True；申请号不在 detection_log 中返回 False。
    """
    try:
        with open(DETECTION_LOG_FILE, 'r', encoding='utf-8') as f:
            log_data = json.load(f)

        found = False
        for record in log_data['records']:
            if record['application_no'] == application_no:
                record['fwxx_list'] = fwxx_data.get('fwxx_list')
                record['bhsjtzs_xiazaisj'] = fwxx_data.get('bhsjtzs_xiazaisj')
                record['bhsjtzs_data'] = fwxx_data.get('bhsjtzs_data')
                found = True
                break

        if not found:
            print(f"    [!] {application_no} 不在 detection_log 中，写入 {FWXX_UNMATCHED_FILE}")
            _append_unmatched(application_no, fwxx_data)
            return False

        tmp_file = DETECTION_LOG_FILE + '.tmp'
        with open(tmp_file, 'w', encoding='utf-8') as f:
            json.dump(log_data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_file, DETECTION_LOG_FILE)

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
        driver = create_driver_with_retry()

        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # 步骤 3：打开搜索页，等待用户登录
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

        print(f"\n[*] 打开搜索页: {args.url}")
        driver.get(args.url)
        time.sleep(3)

        # 自动填写账密
        username, password = load_credentials()
        if username and password:
            filled = auto_fill_login(driver, username, password)
            if filled:
                print("\n" + "="*60)
                print("请在浏览器中完成验证码，然后点击【登录】按钮")
                print("登录成功后，回到这里按 Enter 继续...")
                print("="*60)
            else:
                print("[!] 自动填写失败，请手动登录后按 Enter 继续...")
        else:
            print("[!] 未找到登录凭证，请手动登录后按 Enter 继续...")
            print("    提示：在 .env 中填写 CNIPA_USERNAME / CNIPA_PASSWORD 可自动填写")

        input()

        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # 步骤 4：加载坐标配置
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

        # 搜索页坐标（复用现有配置或自动记录）
        print("\n[*] 正在加载坐标配置...")
        if not os.path.exists(CONFIG_FILE):
            print(f"[!] 配置文件不存在: {CONFIG_FILE}")
            print("[*] 需要手动记录坐标信息（仅需一次）...")
            print("\n" + "="*60)
            print("📍 鼠标位置记录（自动模式）")
            print("="*60)
            print("⚠️  紧急停止: 把鼠标甩到屏幕左上角\n")

            print("▶ 请把鼠标移到 [申请号输入框] 的中间")
            countdown(8, "秒后自动读取坐标")
            input_x, input_y = pyautogui.position()
            print(f"  ✓ 输入框坐标: ({input_x}, {input_y})")

            print("\n▶ 请把鼠标移到 [查询按钮] 的中间")
            countdown(8, "秒后自动读取坐标")
            button_x, button_y = pyautogui.position()
            print(f"  ✓ 按钮坐标: ({button_x}, {button_y})")

            # 保存到配置文件
            search_config = {
                'input_x': input_x,
                'input_y': input_y,
                'button_x': button_x,
                'button_y': button_y,
                'last_updated': datetime.now().isoformat()
            }
            try:
                with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
                    json.dump(search_config, f, indent=2, ensure_ascii=False)
                print(f"\n✓ 坐标已保存到配置文件: {CONFIG_FILE}")
            except Exception as e:
                print(f"\n[!] 保存配置失败: {e}")
                driver.quit()
                return
        else:
            try:
                with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                    search_config = json.load(f)
                input_x = search_config['input_x']
                input_y = search_config['input_y']
                button_x = search_config['button_x']
                button_y = search_config['button_y']
                print(f"[✓] 坐标配置已加载")
            except (KeyError, json.JSONDecodeError) as e:
                print(f"[!] 配置文件格式错误: {e}")
                driver.quit()
                return

        print("\n[*] 现在需要记录发文信息页面的坐标...")
        print("[*] 请确保浏览器已登录并正常显示搜索页")

        # 发文信息坐标（新增）
        fwxx_config = load_or_record_fwxx_positions()
        link_x = fwxx_config['link_x']
        link_y = fwxx_config['link_y']
        fwxx_menu_x = fwxx_config['fwxx_menu_x']
        fwxx_menu_y = fwxx_config['fwxx_menu_y']

        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # 步骤 5：倒计时
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

        countdown(8, "坐标已记录，即将开始自动采集，倒计时")

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

            # 申请号之间的随机延迟（防反爬，每 3 个随机等待 2~5 秒）
            if idx % 3 == 0 and idx < len(targets):
                wait_time = random.uniform(2, 5)
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
            except:
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
