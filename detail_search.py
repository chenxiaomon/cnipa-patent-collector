"""Confirm search targets from passive MITM receipts and isolate detail navigation."""

import time

from atomic_write import write_json_atomic
from cache_utils import is_supported_cn_application_no, normalize_app_no, read_json_cache
from cnipa_session import raise_if_cnipa_login_required
from detail_attempt import (
    DetailCollectionFatalError,
    clear_matching_detail_attempt,
    read_detail_attempt_marker,
)
from settings import (
    PATENT_DETAIL_SEARCH_CACHE_FILE,
    FWXX_SEARCH_READY_TIMEOUT,
    FWXX_DETAIL_OPEN_TIMEOUT,
    FWXX_PAGE_LOAD_WAIT,
    FWXX_DETAIL_CLOSE_WAIT,
    FWXX_TAB_SWITCH_WAIT,
)


DETAIL_SEARCH_API_PATH = '/api/search/undomestic/publicSearch'
_DETAIL_SEARCH_FAILURES = {
    'http_error': '搜索接口未返回 HTTP 200',
    'response_not_object_or_list': '搜索接口未返回有效 JSON 记录结构',
    'api_error': '搜索接口返回业务错误',
    'records_missing': '搜索响应缺少记录列表',
    'records_not_list': '搜索响应记录不是列表',
    'no_records': '搜索响应没有记录',
    'multiple_records': '搜索响应有多条记录，无法确认唯一详情入口',
    'total_invalid': '搜索响应总条数格式无效',
    'record_count_mismatch': '搜索响应总条数与记录数不一致',
    'record_not_object': '搜索记录不是对象',
    'application_no_missing': '搜索记录缺少申请号',
    'application_no_invalid': '搜索记录申请号格式无效',
    'application_no_mismatch': '搜索返回申请号与目标不符',
}


class DetailSearchRetryableError(RuntimeError):
    """A search target or detail window is unavailable; an open attempt needs recovery."""


def publish_detail_search_response(
    bound_attempt: dict | None, http_status: int, response_payload: object,
) -> None:
    """Publish only request-bound observations; never probe or modify the logged-in page."""
    if bound_attempt is None or read_detail_attempt_marker() != bound_attempt:
        return

    receipt = {
        'application_no': bound_attempt['application_no'],
        'http_status': http_status,
    }
    if http_status != 200:
        receipt['reason'] = 'http_error'
    elif not isinstance(response_payload, (dict, list)):
        receipt['reason'] = 'response_not_object_or_list'
    elif isinstance(response_payload, dict) and response_payload.get('code', 200) != 200:
        receipt['reason'] = 'api_error'
    else:
        if isinstance(response_payload, list):
            search_records = response_payload
        elif 'records' in response_payload:
            search_records = response_payload['records']
        elif isinstance(response_payload.get('data'), dict) and 'records' in response_payload['data']:
            search_records = response_payload['data']['records']
        elif isinstance(response_payload.get('data'), list):
            search_records = response_payload['data']
        else:
            search_records = None
            receipt['reason'] = 'records_missing'

        if isinstance(response_payload, dict):
            record_containers = [response_payload]
            if isinstance(response_payload.get('data'), dict):
                record_containers.append(response_payload['data'])
            for record_container in record_containers:
                if 'total' in record_container:
                    declared_total = record_container['total']
                    if type(declared_total) is not int or declared_total < 0:
                        receipt['reason'] = 'total_invalid'
                    elif declared_total > 1:
                        receipt['reason'] = 'multiple_records'
                    elif isinstance(search_records, list) and declared_total != len(search_records):
                        receipt['reason'] = 'record_count_mismatch'

        if 'reason' not in receipt:
            if not isinstance(search_records, list):
                receipt['reason'] = 'records_not_list'
            else:
                receipt['record_count'] = len(search_records)
                if not search_records:
                    receipt['reason'] = 'no_records'
                elif len(search_records) != 1:
                    receipt['reason'] = 'multiple_records'
                elif not isinstance(search_records[0], dict):
                    receipt['reason'] = 'record_not_object'
                else:
                    returned_application_no = search_records[0].get('zhuanlisqh')
                    if not returned_application_no:
                        receipt['reason'] = 'application_no_missing'
                    elif not isinstance(returned_application_no, str) or not is_supported_cn_application_no(returned_application_no):
                        receipt['reason'] = 'application_no_invalid'
                    else:
                        receipt['returned_application_no'] = normalize_app_no(returned_application_no)
                        receipt['reason'] = (
                            'target_confirmed'
                            if receipt['returned_application_no'] == bound_attempt['application_no']
                            else 'application_no_mismatch'
                        )

    write_json_atomic(PATENT_DETAIL_SEARCH_CACHE_FILE, {bound_attempt['attempt_id']: receipt})


def wait_for_detail_search_target(attempt: dict) -> None:
    """Only a fresh, unique target response can authorize the existing coordinate click."""
    search_deadline = time.monotonic() + FWXX_SEARCH_READY_TIMEOUT
    while True:
        raise_if_cnipa_login_required()
        receipt_cache = read_json_cache(str(PATENT_DETAIL_SEARCH_CACHE_FILE))
        receipt = receipt_cache.get(attempt['attempt_id']) if isinstance(receipt_cache, dict) else None
        if isinstance(receipt, dict) and receipt.get('application_no') == attempt['application_no']:
            reason = receipt.get('reason')
            if reason == 'target_confirmed':
                if (
                    receipt.get('http_status') == 200
                    and receipt.get('record_count') == 1
                    and receipt.get('returned_application_no') == attempt['application_no']
                ):
                    return
                raise DetailSearchRetryableError('搜索确认回执格式异常，已跳过当前申请号')
            if isinstance(reason, str) and reason in _DETAIL_SEARCH_FAILURES:
                failure_description = _DETAIL_SEARCH_FAILURES[reason]
                if reason == 'http_error' and type(receipt.get('http_status')) is int:
                    failure_description += f"（实际 HTTP {receipt['http_status']}）"
                raise DetailSearchRetryableError(
                    f"{failure_description}，目标 {attempt['application_no']}"
                )
        remaining_wait = search_deadline - time.monotonic()
        if remaining_wait <= 0:
            raise DetailSearchRetryableError(
                f"搜索结果等待 {FWXX_SEARCH_READY_TIMEOUT:g} 秒仍未收到当前尝试的主代理回执，"
                f"目标 {attempt['application_no']}；请检查并重启主 MITM 代理后补采"
            )
        time.sleep(min(0.2, remaining_wait))


def wait_for_unique_detail_window(driver, search_handle: str) -> str:
    """Wait without a second click; ambiguous windows or a lost search page are fatal."""
    detail_open_deadline = time.monotonic() + FWXX_DETAIL_OPEN_TIMEOUT
    while True:
        raise_if_cnipa_login_required()
        current_handles = list(driver.window_handles)
        if search_handle not in current_handles:
            raise DetailCollectionFatalError("搜索页已丢失，已停止批次")
        new_handles = [handle for handle in current_handles if handle != search_handle]
        if len(new_handles) > 1:
            raise DetailCollectionFatalError(
                f"详情页打开了 {len(new_handles)} 个新标签页，无法确认唯一详情页，已停止批次"
            )
        if new_handles:
            return new_handles[0]
        remaining_wait = detail_open_deadline - time.monotonic()
        if remaining_wait <= 0:
            raise DetailSearchRetryableError(
                f"详情页额外等待 {FWXX_DETAIL_OPEN_TIMEOUT:g} 秒仍未打开新标签页（新增 0 个）"
            )
        time.sleep(min(0.2, remaining_wait))


def restore_detail_search_page(driver, search_handle: str, attempt_id: str) -> None:
    """Revoke the attempt and cancel late navigation before permitting another search."""
    try:
        clear_matching_detail_attempt(attempt_id)
        driver.switch_to.window(search_handle)
        # Clearing the nonce cannot stop old callbacks from firing after a new attempt begins.
        driver.refresh()
        time.sleep(FWXX_PAGE_LOAD_WAIT)
        for stale_handle in list(driver.window_handles):
            if stale_handle != search_handle:
                driver.switch_to.window(stale_handle)
                driver.close()
                time.sleep(FWXX_DETAIL_CLOSE_WAIT)
        driver.switch_to.window(search_handle)
        if list(driver.window_handles) != [search_handle]:
            raise DetailCollectionFatalError("搜索页恢复后仍有额外标签页，已停止批次")
        time.sleep(FWXX_TAB_SWITCH_WAIT)
    except DetailCollectionFatalError:
        raise
    except Exception as error:
        raise DetailCollectionFatalError("无法恢复搜索页，已停止批次") from error
