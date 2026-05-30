#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
主自动化程序
- 读取申请号列表
- 逐个执行自动化查询
- 实时记录检测数据
- 支持断点续传
"""

import os
import sys

# 虚拟显示器必须在 pyautogui / Xlib 任何 import 之前启动，否则 PyAutoGUI 会缓存物理屏幕连接
if os.getenv('USE_VIRTUAL_DISPLAY', '').lower() in ('true', '1', 'yes') \
        and sys.platform.startswith('linux'):
    try:
        from pyvirtualdisplay import Display as _VD
        _vd_w = int(os.getenv('VIRTUAL_DISPLAY_WIDTH', '1920'))
        _vd_h = int(os.getenv('VIRTUAL_DISPLAY_HEIGHT', '1080'))
        _vd_inst = _VD(visible=False, size=(_vd_w, _vd_h), color_depth=24)
        _vd_inst.start()
        print(f"✓ 虚拟显示器已启动 ({_vd_w}x{_vd_h})，物理桌面已释放")
    except ImportError:
        print("⚠️  pyvirtualdisplay 未安装，使用物理桌面")

import time
import random
import warnings
import json
from datetime import datetime

# 禁用 undetected_chromedriver 的垃圾回收警告（已知 bug）
warnings.filterwarnings("ignore", category=ResourceWarning)

try:
    import undetected_chromedriver as uc
    import pyautogui
except ImportError as e:
    print(f"❌ 缺失依赖: {e}")
    print("请运行: pip install undetected-chromedriver selenium pyautogui")
    sys.exit(1)

from detection_logger import DetectionLogger, DetectionRecord
from browser_utils import (
    fill_vue_input, is_browser_alive, create_driver_with_retry,
)
from cache_utils import normalize_app_no, poll_cache_for_key
from coordinate_service import CoordinateService
from browser_service import BrowserService, stop_virtual_display
from input_service import InputService
from settings import (
    CNIPA_URL, SEARCH_LIST_FILE, CONFIG_FILE, FORCE_UPDATE_FLAG,
    PYAUTOGUI_PAUSE, PYAUTOGUI_FAILSAFE, MITM_TIMEOUT, MITM_POLL_INTERVAL,
    PATENT_CACHE_FILE, USE_MITM_PROXY, PATENTS_DB_FILE, DETECTION_LOG_JSONL_FILE,
    DATA_DIR,
    AUTOMATION_CONFIG_LOAD_WAIT, AUTOMATION_STARTUP_COUNTDOWN,
    AUTOMATION_ANTI_CRAWL_BATCH_SIZE, AUTOMATION_STATS_PRINT_INTERVAL,
    AUTOMATION_ANTI_CRAWL_WAIT_MIN, AUTOMATION_ANTI_CRAWL_WAIT_MAX,
)
from db_manager import PatentsDB

# PyAutoGUI 配置
pyautogui.PAUSE = PYAUTOGUI_PAUSE
pyautogui.FAILSAFE = PYAUTOGUI_FAILSAFE


def load_search_list() -> list:
    """加载申请号列表"""
    if not os.path.exists(SEARCH_LIST_FILE):
        print(f"❌ 找不到搜索列表: {SEARCH_LIST_FILE}")
        sys.exit(1)

    with open(SEARCH_LIST_FILE, 'r', encoding='utf-8') as f:
        applications = [line.strip() for line in f if line.strip()]

    print(f"✓ 已加载 {len(applications)} 个申请号")
    return applications


def _is_patent_data_complete(data: dict) -> bool:
    """
    验证专利数据是否有效（宽松模式：只检查关键字段）

    原则：宁可不采集，也不要采集错误的数据
    - 有关键信息（申请号、名称、申请人）= 有效数据 ✅
    - 缺少关键信息 = 无效数据 ❌
    - 其他字段为 null = 正常，允许 ✓

    Args:
        data: 从 MITM 缓存读取的专利数据字典

    Returns:
        True: 数据有效，包含关键字段（申请号、专利名称、申请人）
        False: 数据无效，缺少关键字段
    """
    # 只要求这些关键字段有值
    critical_fields = [
        'zhuanlisqh',    # 申请号 - 唯一标识
        'zhuanlimc',     # 专利名称 - 核心信息
        'shenqingrxm'    # 申请人 - 重要信息
    ]

    for field in critical_fields:
        # 关键字段不能为空、null 字符串或空白
        value = data.get(field)
        if not value or value == 'null' or (isinstance(value, str) and value.strip() == ''):
            return False

    # 其他字段可以 null，不影响数据有效性
    return True


def search_application(
    driver,
    application_no: str,
    input_x: int,
    input_y: int,
    button_x: int,
    button_y: int,
    logger: DetectionLogger,
    force_update: bool = False,
) -> None:
    """
    搜索单个申请号

    Args:
        driver: Selenium 驱动
        application_no: 申请号
        input_x, input_y: 输入框坐标
        button_x, button_y: 查询按钮坐标
        logger: 日志记录器
    """
    start_time = time.time()

    try:
        # 检测浏览器是否还活着
        if not is_browser_alive(driver):
            print(f"    [!] 浏览器已关闭，无法采集")
            return None

        print(f"\n[→] 查询: {application_no}")

        # 输入申请号并点击查询
        InputService.type_in_search(input_x, input_y, button_x, button_y, application_no)

        # 等待并轮询缓存，最多 MITM_TIMEOUT 秒
        # ⭐ 核心原则：宁可不采集，也不要采集错误的数据
        normalized_app_no = normalize_app_no(application_no)
        patent_data = poll_cache_for_key(
            str(PATENT_CACHE_FILE),
            normalized_app_no,
            max_wait=MITM_TIMEOUT,
            interval=MITM_POLL_INTERVAL,
            validate=_is_patent_data_complete,
        )

        if patent_data:
            # ✅ MITM 代理运行，成功拦截了 API 数据
            record = DetectionRecord(
                application_no=normalized_app_no,
                status_code=200,
                response_summary='Patent data from MITM proxy',
                detected=False,
                # 14 个专利字段
                famingzlsqgbg=patent_data.get('famingzlsqgbg'),
                shouquanggh=patent_data.get('shouquanggh'),
                zhuanlimc=patent_data.get('zhuanlimc'),
                shenqingrxm=patent_data.get('shenqingrxm'),
                zhuanlilx=patent_data.get('zhuanlilx'),
                shenqingr=patent_data.get('shenqingr'),
                gongkaiggh=patent_data.get('gongkaiggh'),
                falvzt=patent_data.get('falvzt'),
                gongkaiggr=patent_data.get('gongkaiggr'),
                shouquanggr=patent_data.get('shouquanggr'),
                zhufenlh=patent_data.get('zhufenlh'),
                anjianbh=patent_data.get('anjianbh'),
                anjianywzt=patent_data.get('anjianywzt'),
            )
            print(f"  ✓ 获得专利数据: {patent_data.get('zhuanlimc', 'N/A')}")
        else:
            # ❌ 采集失败：未能在 8 秒内获得完整的专利字段
            record = DetectionRecord(
                application_no=normalized_app_no,
                status_code=0,
                response_summary='Failed to collect patent data (MITM timeout or incomplete data)',
                detected=False,
                # 不填充专利字段，保持空白
            )
            print(f"  ✗ 未采集数据（网络超时或 MITM 未启动）")

        record.response_time_ms = round((time.time() - start_time) * 1000, 2)
        print(f"  ✓ 状态: {record.status_code}, 耗时: {record.response_time_ms}ms")

    except Exception as e:
        record = DetectionRecord(
            application_no=normalize_app_no(application_no),
            error_message=str(e),
            response_summary=f'Error: {str(e)[:50]}'
        )
        record.response_time_ms = round((time.time() - start_time) * 1000, 2)
        print(f"  ✗ 错误: {str(e)[:50]}")

    # 记录结果：强制更新模式下用 upsert，正常模式追加
    if force_update:
        logger.upsert_record(record)
    else:
        logger.add_record(record)


def run_automation(test_count: int = None, update_list: str = None) -> None:
    """
    运行自动化程序

    Args:
        test_count:   仅处理前 N 个申请号（用于测试），None 则处理全部
        update_list:  强制更新模式：从指定文件读取申请号并重新检索（忽略已处理标记）
    """
    print("\n" + "="*60)
    if update_list:
        print("🔄 强制更新模式")
    else:
        print("🔍 检测系统自动化执行")
    print("="*60)

    # 关键检查：验证 MITM 代理是否启用
    if not USE_MITM_PROXY:
        print("\n⚠️  警告：MITM 代理未启用")
        print("如果要采集完整的 14 个专利字段，请：")
        print("  1. 在另一个终端运行：python start_mitm_proxy.py")
        print("  2. 然后运行本程序：USE_MITM_PROXY=true python main_automation.py")
        print("\n如果不启用 MITM 代理，将运行降级模式（仅记录申请号）")
        print("="*60)

    # 初始化日志记录器
    logger = DetectionLogger()

    if update_list:
        # 强制更新模式：从文件读取待更新申请号，不过滤已处理
        if not os.path.exists(update_list):
            print(f"❌ 找不到更新列表: {update_list}")
            return
        with open(update_list, encoding='utf-8') as f:
            pending = [line.strip() for line in f if line.strip()]
        print(f"⏳ 强制更新: {len(pending)} 个申请号")
    else:
        # 正常模式：跳过已处理
        all_applications = load_search_list()
        pending = logger.get_pending_applications(all_applications)
        print(f"✓ 已处理: {len(all_applications) - len(pending)} 个")
        print(f"⏳ 待处理: {len(pending)} 个")

    if test_count:
        pending = pending[:test_count]
        print(f"📌 测试模式: 仅处理前 {test_count} 个")

    if not pending:
        print("✓ 所有申请号都已处理！")
        logger.print_summary()
        return

    driver = None
    try:
        if update_list:
            with open(FORCE_UPDATE_FLAG, 'w'):
                pass
            print(f"[*] 已写入强制更新信号: {FORCE_UPDATE_FLAG}")

        # 创建浏览器并登录
        driver = BrowserService.launch_and_login(CNIPA_URL)

        # 加载或记录鼠标位置
        print("\n⏳ 正在加载鼠标位置配置...")
        time.sleep(AUTOMATION_CONFIG_LOAD_WAIT)
        input_x, input_y, button_x, button_y = CoordinateService.load_or_record_search_coordinates()

        # 倒计时
        print(f"\n⏳ {AUTOMATION_STARTUP_COUNTDOWN}秒后开始自动操作，请不要动鼠标！")
        for i in range(AUTOMATION_STARTUP_COUNTDOWN, 0, -1):
            print(f"  {i}...", end="\r")
            time.sleep(1)
        print()

        # 执行自动化
        print("\n" + "="*60)
        print("🤖 开始自动化搜索...")
        print("="*60)

        for i, app_no in enumerate(pending, 1):
            # 检测浏览器是否还活着
            if not is_browser_alive(driver):
                remaining = pending[i - 1:]
                print("\n⚠️  浏览器已关闭，停止采集")
                print(f"已采集 {i-1} 条，还有 {len(remaining)} 条未采集")
                if remaining:
                    checkpoint = DATA_DIR / 'checkpoint_resume.txt'
                    checkpoint.write_text('\n'.join(remaining) + '\n', encoding='utf-8')
                    print(f"[*] 未完成列表已写入: {checkpoint}")
                    print(f"    续跑命令: python main_automation.py --update-list {checkpoint}")
                break

            print(f"\n[{i}/{len(pending)}]")
            search_application(
                driver,
                app_no,
                input_x,
                input_y,
                button_x,
                button_y,
                logger,
                force_update=bool(update_list),
            )

            # 每处理 N 个后随机等待（优化：每条省 ~0.25s）
            if i % AUTOMATION_ANTI_CRAWL_BATCH_SIZE == 0:
                wait_time = random.uniform(AUTOMATION_ANTI_CRAWL_WAIT_MIN, AUTOMATION_ANTI_CRAWL_WAIT_MAX)
                print(f"  防爬虫等待 {wait_time:.1f}秒...")
                time.sleep(wait_time)

            # 每处理 N 个打印一次统计
            if i % AUTOMATION_STATS_PRINT_INTERVAL == 0:
                stats = logger.get_stats()
                print(f"\n📊 进度: 已处理 {stats['total']} 个，成功 {stats['success']} 个")

    except KeyboardInterrupt:
        print("\n\n⚠️  用户中断")
    finally:
        if update_list:
            try:
                os.remove(FORCE_UPDATE_FLAG)
                print("[*] 已清除强制更新信号")
            except FileNotFoundError:
                pass

        # 安全关闭浏览器（处理 undetected_chromedriver 的清理问题）
        if driver:
            try:
                driver.quit()
            except Exception:
                # 忽略 undetected_chromedriver 的清理错误（已知 bug）
                pass

        # 强制置为 None，帮助垃圾回收器
        driver = None

        stop_virtual_display()

        logger.print_summary()
        print(f"\n✓ 日志文件: {logger.log_file}")

        # 导出 Excel（如果有专利数据）
        excel_file = os.path.join(
            os.path.dirname(logger.log_file),
            'patents_data.xlsx'
        )
        if logger.get_stats()['total'] > 0:
            logger.export_to_excel(excel_file)
            print(f"✓ Excel 文件: {excel_file}")
            PatentsDB(PATENTS_DB_FILE).export_to_jsonl(DETECTION_LOG_JSONL_FILE)
            print(f"✓ JSONL 备份已刷新: {DETECTION_LOG_JSONL_FILE}")


if __name__ == '__main__':
    # 默认模式：处理全部（跳过已处理）
    #   python main_automation.py
    # 测试模式：
    #   python main_automation.py --test 10
    # 强制更新模式（重新检索指定申请号）：
    #   python main_automation.py --update-list data/update_list.txt
    #   python main_automation.py --update-list data/update_list.txt --test 5

    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--test', type=int, default=None, metavar='N',
                        help='仅处理前 N 个申请号')
    parser.add_argument('--update-list', type=str, default=None, metavar='FILE',
                        help='强制更新模式：从文件读取申请号并重新检索')
    args = parser.parse_args()

    run_automation(test_count=args.test, update_list=args.update_list)
