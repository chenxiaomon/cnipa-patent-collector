#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
集中化配置管理模块

管理所有项目配置：路径、MITM 参数、超时时间、环境变量。
支持通过环境变量覆盖默认值。

使用方法：
    from settings import DATA_DIR, CONFIG_FILE, MITM_TIMEOUT
"""

import os
import sys
from pathlib import Path

# Windows 控制台强制 UTF-8，避免 emoji/中文在 GBK 环境下崩溃
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# ============================================================================
# 基础路径
# ============================================================================

# 项目根目录（settings.py 所在的目录）
BASE_DIR = Path(__file__).parent.absolute()

# 数据目录
DATA_DIR = BASE_DIR / 'data'
RESULTS_DIR = DATA_DIR / 'results'
RAW_RESPONSES_DIR = DATA_DIR / 'raw_responses'
RAW_SEARCHES_DIR = DATA_DIR / 'raw_searches'

# 确保目录存在
for directory in [DATA_DIR, RESULTS_DIR, RAW_RESPONSES_DIR, RAW_SEARCHES_DIR]:
    directory.mkdir(parents=True, exist_ok=True)

# ============================================================================
# 输入数据文件
# ============================================================================

SEARCH_LIST_FILE = DATA_DIR / 'search_list.txt'
FWXX_LIST_FILE = DATA_DIR / 'fwxx_list.txt'

# ============================================================================
# 配置文件（鼠标坐标等）
# ============================================================================

CONFIG_FILE = DATA_DIR / 'config.json'
CONFIG_FWXX_FILE = DATA_DIR / 'config_fwxx.json'
FORCE_UPDATE_FLAG = DATA_DIR / 'force_update.flag'

# ============================================================================
# 结果文件
# ============================================================================

# 检测日志（主要输出）
DETECTION_LOG_FILE = RESULTS_DIR / 'detection_log.json'
DETECTION_LOG_JSONL_FILE = RESULTS_DIR / 'detection_log.jsonl'

# SQLite 数据库（主存储，替代 JSONL 查询）
PATENTS_DB_FILE = DATA_DIR / 'patents.db'

# Excel 导出文件
PATENTS_EXCEL_FILE = RESULTS_DIR / 'patents_data.xlsx'

# ============================================================================
# 缓存文件
# ============================================================================

# 专利数据缓存（MITM 代理写入）
PATENT_CACHE_FILE = DATA_DIR / 'patent_cache.json'
PATENT_FWXX_CACHE_FILE = DATA_DIR / 'patent_fwxx_cache.json'

# 断点续传和状态标记
MARKER_FILE = DATA_DIR / 'current_fwxx_target.json'
FWXX_UNMATCHED_FILE = DATA_DIR / 'fwxx_unmatched.json'

# 补采独立模式的结果
FWXX_STANDALONE_RESULTS_FILE = RESULTS_DIR / 'fwxx_standalone_results.json'

# ============================================================================
# MITM 代理配置
# ============================================================================

MITM_HOST = os.getenv('MITM_HOST', '127.0.0.1')
MITM_PORT = int(os.getenv('MITM_PORT', '8083'))

# MITM 轮询超时（秒）：等待 MITM 代理返回数据的最长时间
MITM_TIMEOUT = float(os.getenv('MITM_TIMEOUT', '8'))

# MITM 轮询间隔（秒）：两次检查缓存之间的等待时间
MITM_POLL_INTERVAL = float(os.getenv('MITM_POLL_INTERVAL', '0.5'))

# ============================================================================
# PyAutoGUI 配置
# ============================================================================

PYAUTOGUI_PAUSE = float(os.getenv('PYAUTOGUI_PAUSE', '0.03'))
PYAUTOGUI_FAILSAFE = os.getenv('PYAUTOGUI_FAILSAFE', 'false').lower() in ('true', '1', 'yes')

# ============================================================================
# 功能开关
# ============================================================================

# 是否启用 MITM 代理（生产采集必须启用）
USE_MITM_PROXY = os.getenv('USE_MITM_PROXY', '').lower() in ('true', '1', 'yes')

# 是否在 Xvfb 虚拟显示器中运行（释放物理桌面，需先安装 xvfb + pyvirtualdisplay）
USE_VIRTUAL_DISPLAY = os.getenv('USE_VIRTUAL_DISPLAY', '').lower() in ('true', '1', 'yes')
VIRTUAL_DISPLAY_WIDTH = int(os.getenv('VIRTUAL_DISPLAY_WIDTH', '1920'))
VIRTUAL_DISPLAY_HEIGHT = int(os.getenv('VIRTUAL_DISPLAY_HEIGHT', '1080'))

# ============================================================================
# URL 和端点
# ============================================================================

CNIPA_URL = 'https://cpquery.cponline.cnipa.gov.cn/'
CNIPA_QUERY_API = 'https://cpquery.cponline.cnipa.gov.cn/txtSearch'

# ============================================================================
# 业务规则
# ============================================================================

# 采集发文的触发条件（案件业务状态）
FWXX_TRIGGER_ANJIANYWZT = '驳回等复审请求'

# ============================================================================
# 自动化行为参数
# ============================================================================

# 主采集流程（main_automation.py）
AUTOMATION_CONFIG_LOAD_WAIT       = float(os.getenv('AUTOMATION_CONFIG_LOAD_WAIT', '1'))
AUTOMATION_STARTUP_COUNTDOWN      = int(os.getenv('AUTOMATION_STARTUP_COUNTDOWN', '5'))
AUTOMATION_ANTI_CRAWL_BATCH_SIZE  = int(os.getenv('AUTOMATION_ANTI_CRAWL_BATCH_SIZE', '10'))
AUTOMATION_STATS_PRINT_INTERVAL   = int(os.getenv('AUTOMATION_STATS_PRINT_INTERVAL', '20'))
AUTOMATION_ANTI_CRAWL_WAIT_MIN    = float(os.getenv('AUTOMATION_ANTI_CRAWL_WAIT_MIN', '2'))
AUTOMATION_ANTI_CRAWL_WAIT_MAX    = float(os.getenv('AUTOMATION_ANTI_CRAWL_WAIT_MAX', '5'))

# 发文信息采集流程（collect_fwxx.py）
FWXX_PAGE_LOAD_WAIT        = float(os.getenv('FWXX_PAGE_LOAD_WAIT', '3'))
FWXX_STARTUP_COUNTDOWN     = int(os.getenv('FWXX_STARTUP_COUNTDOWN', '8'))
FWXX_INPUT_DELAY_MIN       = float(os.getenv('FWXX_INPUT_DELAY_MIN', '0.05'))
FWXX_INPUT_DELAY_MAX       = float(os.getenv('FWXX_INPUT_DELAY_MAX', '0.18'))
FWXX_INPUT_PAUSE_PROB      = float(os.getenv('FWXX_INPUT_PAUSE_PROB', '0.15'))
FWXX_POST_SEARCH_WAIT      = float(os.getenv('FWXX_POST_SEARCH_WAIT', '3'))
FWXX_DETAIL_CLICK_WAIT     = float(os.getenv('FWXX_DETAIL_CLICK_WAIT', '4'))
FWXX_TAB_SWITCH_WAIT       = float(os.getenv('FWXX_TAB_SWITCH_WAIT', '0.5'))
FWXX_MENU_CLICK_WAIT       = float(os.getenv('FWXX_MENU_CLICK_WAIT', '3'))
FWXX_CACHE_POLL_TIMEOUT    = float(os.getenv('FWXX_CACHE_POLL_TIMEOUT', '10'))
FWXX_DETAIL_CLOSE_WAIT     = float(os.getenv('FWXX_DETAIL_CLOSE_WAIT', '1'))
FWXX_ANTI_CRAWL_BATCH_SIZE = int(os.getenv('FWXX_ANTI_CRAWL_BATCH_SIZE', '3'))
FWXX_ANTI_CRAWL_WAIT_MIN   = float(os.getenv('FWXX_ANTI_CRAWL_WAIT_MIN', '2'))
FWXX_ANTI_CRAWL_WAIT_MAX   = float(os.getenv('FWXX_ANTI_CRAWL_WAIT_MAX', '5'))

# ============================================================================
# 验证工具函数
# ============================================================================

def verify_paths() -> dict:
    """验证所有关键路径是否存在或可创建"""
    paths_status = {
        'DATA_DIR': DATA_DIR.exists(),
        'RESULTS_DIR': RESULTS_DIR.exists(),
        'SEARCH_LIST_FILE': SEARCH_LIST_FILE.exists(),
        'CONFIG_FILE': CONFIG_FILE.exists(),
    }
    return paths_status


def get_config_summary() -> str:
    """获取配置摘要（用于调试）"""
    return f"""
╔════════════════════════════════════════════════════════════════╗
║                     项目配置摘要                               ║
╠════════════════════════════════════════════════════════════════╣
║ 基础路径                                                       ║
║   BASE_DIR: {BASE_DIR}
║   DATA_DIR: {DATA_DIR}
║   RESULTS_DIR: {RESULTS_DIR}
║                                                                ║
║ MITM 代理                                                      ║
║   地址: {MITM_HOST}:{MITM_PORT}
║   超时: {MITM_TIMEOUT}s
║   轮询间隔: {MITM_POLL_INTERVAL}s
║   启用: {USE_MITM_PROXY}
║                                                                ║
║ 关键文件                                                       ║
║   搜索列表: {SEARCH_LIST_FILE}
║   检测日志: {DETECTION_LOG_FILE}
║   Excel 报表: {PATENTS_EXCEL_FILE}
║   专利缓存: {PATENT_CACHE_FILE}
╚════════════════════════════════════════════════════════════════╝
"""


if __name__ == '__main__':
    print(get_config_summary())
    print("路径验证:")
    for path, exists in verify_paths().items():
        status = "✓" if exists else "✗"
        print(f"  {status} {path}")
