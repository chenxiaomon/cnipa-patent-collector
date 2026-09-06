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


def _load_env_file_into_environment(env_file: Path) -> None:
    """把 .env 补进 os.environ，让本文件后续的 os.getenv 能读到。

    用 setdefault：进程显式传入的环境变量优先，保证 Dashboard 给子进程注入的
    USE_MITM_PROXY / CNIPA_LOGIN_WAIT_SECONDS 等不被 .env 覆盖。
    空值跳过，避免 'KEY=' 占位行把变量污染成空字符串。
    """
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, _, value = line.partition('=')
        key, value = key.strip(), value.strip()
        if key and value:
            os.environ.setdefault(key, value)


_load_env_file_into_environment(BASE_DIR / '.env')

# 本地版本标记文件（随代码 git 提交，供网络更新检查对比）
VERSION_FILE = BASE_DIR / 'VERSION'
RELEASE_REVISION_FILE = BASE_DIR / 'RELEASE_REVISION'

# 本机只读环境诊断使用的手工驱动目录；不触发驱动下载或浏览器启动。
MANUAL_CHROMEDRIVER_DIRS = tuple(
    BASE_DIR / directory_name
    for directory_name in (
        'chromedriver-win64', 'chromedriver-mac-arm64',
        'chromedriver-mac-x64', 'chromedriver-linux64',
    )
)

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
FWXX_MANUAL_LIST_DIR = DATA_DIR / 'manual_fwxx_lists'
RETRY_FAILED_FILE = DATA_DIR / 'retry_failed.txt'
MAIN_COLLECTION_CHECKPOINT_FILE = DATA_DIR / 'checkpoint_resume.txt'
FWXX_COLLECTION_CHECKPOINT_FILE = DATA_DIR / 'checkpoint_fwxx.txt'
FEE_COLLECTION_CHECKPOINT_FILE = DATA_DIR / 'checkpoint_fees.txt'
COLLECTION_BATCHES_DIR = DATA_DIR / 'collection_batches'

# ============================================================================
# 配置文件（鼠标坐标等）
# ============================================================================

CONFIG_FILE = DATA_DIR / 'config.json'
CONFIG_FWXX_FILE = DATA_DIR / 'config_fwxx.json'
FORCE_UPDATE_FLAG = DATA_DIR / 'force_update.flag'
MACHINE_ROLE_FILE = DATA_DIR / 'machine_role.txt'
MASTER_SYNC_CONFIG_FILE = DATA_DIR / 'master_sync.json'
MASTER_SYNC_STATE_FILE = DATA_DIR / 'master_sync_state.json'
MASTER_SYNC_LOCK_FILE = DATA_DIR / 'master_sync.lock'
COLLECTION_HEARTBEAT_FILE = DATA_DIR / 'collection_heartbeat.json'
ALERT_STATUS_FILE = DATA_DIR / 'alert_status.json'
WATCHDOG_EVENTS_FILE = DATA_DIR / 'watchdog_events.jsonl'
ALERT_FORWARD_STATE_FILE = DATA_DIR / 'alert_forward_state.json'
API_TOKEN_FILE = DATA_DIR / 'api_token.txt'

# ============================================================================
# 结果文件
# ============================================================================

# 检测日志（主要输出）
DETECTION_LOG_FILE = RESULTS_DIR / 'detection_log.json'
DETECTION_LOG_JSONL_FILE = RESULTS_DIR / 'detection_log.jsonl'
AGENCY_VERIFICATION_REPORT_JSON_FILE = RESULTS_DIR / 'agency_verification_report.json'
AGENCY_VERIFICATION_REPORT_CSV_FILE = RESULTS_DIR / 'agency_verification_report.csv'

# SQLite 数据库（主存储，替代 JSONL 查询）
PATENTS_DB_FILE = DATA_DIR / 'patents.db'

# 企业元数据（手动补录真实专利总数等）
COMPANY_META_FILE = DATA_DIR / 'company_meta.json'

# Excel 导出文件
PATENTS_EXCEL_FILE = RESULTS_DIR / 'patents_data.xlsx'

# ============================================================================
# 缓存文件
# ============================================================================

# 专利数据缓存（MITM 代理写入）
PATENT_CACHE_FILE = DATA_DIR / 'patent_cache.json'
PATENT_AGENCY_CACHE_FILE = DATA_DIR / 'patent_agency_cache.json'
PATENT_FWXX_CACHE_FILE = DATA_DIR / 'patent_fwxx_cache.json'
PATENT_FEE_CACHE_FILE = DATA_DIR / 'patent_fee_cache.json'
PATENT_DETAIL_IDENTITY_CACHE_FILE = DATA_DIR / 'patent_detail_identity_cache.json'

# 断点续传和状态标记
MARKER_FILE = DATA_DIR / 'current_fwxx_target.json'
AGENCY_ATTEMPT_MARKER_FILE = DATA_DIR / 'current_agency_attempt.json'
FWXX_UNMATCHED_FILE = DATA_DIR / 'fwxx_unmatched.json'
FEE_UNMATCHED_FILE = DATA_DIR / 'fee_unmatched.json'
AGENCY_UNMATCHED_FILE = DATA_DIR / 'agency_unmatched.json'
DETAIL_COLLECTION_LOCK_FILE = DATA_DIR / 'detail_collection.lock'
SUPERVISED_COLLECTION_LOCK_FILE = DATA_DIR / 'supervised_collection.lock'
PHASE0_BROWSER_LOCK_FILE = DATA_DIR / 'phase0_browser.lock'
PUBLIC_BROWSER_LOCK_FILE = DATA_DIR / 'public_browser.lock'
PUBLIC_PAGINATION_LOCK_FILE = DATA_DIR / 'public_pagination.lock'
LOGIN_READY_FLAG_FILE = DATA_DIR / 'login_ready.flag'

# 补采独立模式的结果
FWXX_STANDALONE_RESULTS_FILE = RESULTS_DIR / 'fwxx_standalone_results.json'

# ============================================================================
# MITM 代理配置
# ============================================================================

MITM_HOST = os.getenv('MITM_HOST', '127.0.0.1')
MITM_PORT = int(os.getenv('MITM_PORT', '8083'))
# 公开查询专用 MITM 代理端口（start_mitm_public_search.py 监听此端口）
PUBLIC_MITM_PORT = int(os.getenv('PUBLIC_MITM_PORT', '8082'))

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
CNIPA_PUBLIC_SEARCH_URL = 'https://cpquery.cponline.cnipa.gov.cn/chinesepatent/index'

# ============================================================================
# 代码更新源（check_update.py / fetch_update.py 共用）
# ============================================================================

# GitHub 仓库标识
GITHUB_REPO = os.getenv('GITHUB_REPO', 'chenxiaomon/cnipa-patent-collector')
GITHUB_BRANCH = os.getenv('GITHUB_BRANCH', 'main')

# 拉取单个文件的源站模板，按顺序尝试：GitHub 原站优先，失败回退国内镜像。
# {repo}/{branch}/{path} 三个占位符在使用处用 .format() 填充。
# 自定义镜像：设置环境变量 RAW_FILE_MIRRORS（逗号分隔的模板），会插到列表最前面。
_DEFAULT_RAW_MIRRORS = [
    'https://raw.githubusercontent.com/{repo}/{branch}/{path}',  # GitHub 原站
    'https://cdn.jsdelivr.net/gh/{repo}@{branch}/{path}',        # jsDelivr CDN（国内通常可达）
    'https://ghproxy.net/https://raw.githubusercontent.com/{repo}/{branch}/{path}',  # ghproxy 反代
    'https://raw.gitmirror.com/{repo}/{branch}/{path}',          # gitmirror 镜像
]


def raw_file_urls(path: str) -> list[str]:
    """返回拉取仓库内某文件的候选 URL 列表（按尝试顺序）。

    path 为仓库内相对路径，如 'VERSION' 或 'settings.py'。
    调用方应依次尝试，任一成功即可，全部失败再报错。
    """
    custom = os.getenv('RAW_FILE_MIRRORS', '').strip()
    templates = [t.strip() for t in custom.split(',') if t.strip()] + _DEFAULT_RAW_MIRRORS
    return [t.format(repo=GITHUB_REPO, branch=GITHUB_BRANCH, path=path) for t in templates]

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

# 首屏加载上限；不设时 chromedriver 默认 300 秒，代理异常会静默卡到超时
BROWSER_PAGE_LOAD_TIMEOUT         = float(os.getenv('BROWSER_PAGE_LOAD_TIMEOUT', '60'))

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

# 非交互模式登录等待时间（秒）：Dashboard/Docker 场景下等待用户完成验证码的上限。
# 默认 75 秒与 web_dashboard.py 传子进程的值对齐；命令行交互模式不受此限制（stdin.isatty）。
CNIPA_LOGIN_WAIT_SECONDS = float(os.getenv('CNIPA_LOGIN_WAIT_SECONDS', '75'))

# 无人值守采集看门狗
WATCHDOG_HEARTBEAT_TIMEOUT_SECONDS = int(os.getenv('WATCHDOG_HEARTBEAT_TIMEOUT_SECONDS', '600'))
WATCHDOG_MAX_RESTARTS = int(os.getenv('WATCHDOG_MAX_RESTARTS', '3'))
WATCHDOG_FAILURE_THRESHOLD = int(os.getenv('WATCHDOG_FAILURE_THRESHOLD', '20'))
WATCHDOG_MIN_FREE_GB = float(os.getenv('WATCHDOG_MIN_FREE_GB', '5'))
ALERT_POLL_SECONDS = int(os.getenv('ALERT_POLL_SECONDS', '60'))

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
