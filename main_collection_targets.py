"""Own the initial main-collection list and its database eligibility rules."""

from cache_utils import normalize_app_no, parse_app_no_list
from db_manager import PatentsDB
from settings import PATENTS_DB_FILE, SEARCH_LIST_FILE


def load_search_list() -> list[str]:
    if not SEARCH_LIST_FILE.is_file():
        raise ValueError(f'找不到搜索列表: {SEARCH_LIST_FILE}')
    applications = parse_app_no_list(SEARCH_LIST_FILE.read_text(encoding='utf-8'))
    print(f"✓ 已加载 {len(applications)} 个申请号")
    return applications


def select_main_collection_targets() -> list[str]:
    """Filter only at batch creation; retries belong to the frozen batch."""
    applications = load_search_list()
    processed = PatentsDB(PATENTS_DB_FILE).get_processed_app_nos()
    pending = [application_no for application_no in applications if normalize_app_no(application_no) not in processed]
    print(f"✓ 已处理: {len(applications) - len(pending)} 个")
    print(f"⏳ 待处理: {len(pending)} 个")
    return pending
