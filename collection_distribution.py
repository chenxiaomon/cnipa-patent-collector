"""Untrusted multi-device collection protocol and domain ownership contracts."""

import hashlib
import json
import math
import re

from cache_utils import is_supported_cn_application_no, normalize_app_no, parse_timestamp

PROTOCOL_VERSION = 1
MAX_COLLECTION_TRANSFER_BYTES = 8 * 1024 * 1024
COLLECTION_FIELDS = {
    'main': (
        'status_code', 'response_time_ms', 'detected', 'response_summary',
        'timestamp', 'error_message', 'famingzlsqgbg', 'shouquanggh',
        'zhuanlimc', 'shenqingrxm', 'zhuanlilx', 'shenqingr', 'gongkaiggh',
        'falvzt', 'gongkaiggr', 'shouquanggr', 'zhufenlh', 'anjianbh', 'anjianywzt',
    ),
    'fwxx': ('fwxx_list', 'bhsjtzs_xiazaisj', 'bhsjtzs_data', 'fwxx_collected_at'),
    'fees': (
        'payable_fee_records', 'late_fee_schedule_records', 'paid_fee_records',
        'fee_receipt_dispatch_records', 'fee_snapshot_at',
    ),
}
_REQUIRED_FEE_LISTS = ('payable_fee_records', 'paid_fee_records', 'fee_receipt_dispatch_records')
_LIST_FIELDS = {'fwxx_list', *_REQUIRED_FEE_LISTS, 'late_fee_schedule_records'}
_ID_PATTERN = re.compile(r'^[0-9a-f]{32}$')


class DistributionValidationError(ValueError):
    """The caller supplied an unsupported or incomplete protocol value."""


class DistributionConflictError(RuntimeError):
    """The assignment is no longer eligible for the requested operation."""


class DistributionAuthorizationError(PermissionError):
    """The task credential or device identity did not match."""


def _application_number(value: object) -> str:
    if not isinstance(value, str) or not is_supported_cn_application_no(value):
        raise DistributionValidationError('申请号格式不正确')
    return normalize_app_no(value)


def validate_collection_assignment(collector, application_nos, device_names) -> tuple:
    if not isinstance(collector, str) or collector not in COLLECTION_FIELDS:
        raise DistributionValidationError('采集类型必须是 main、fwxx 或 fees')
    if not isinstance(application_nos, list) or not 1 <= len(application_nos) <= 500:
        raise DistributionValidationError('每次分单需要 1 至 500 个申请号')
    normalized_numbers = list(dict.fromkeys(_application_number(value) for value in application_nos))
    if not isinstance(device_names, list) or not 1 <= len(device_names) <= 8:
        raise DistributionValidationError('需要 1 至 8 个设备名')
    normalized_devices = []
    for device_name in device_names:
        if (
            not isinstance(device_name, str)
            or not 1 <= len(device_name.strip()) <= 80
            or any(ord(character) < 32 for character in device_name)
        ):
            raise DistributionValidationError('设备名需要 1 至 80 个可显示字符')
        normalized_devices.append(device_name.strip())
    if len(set(normalized_devices)) != len(normalized_devices):
        raise DistributionValidationError('设备名不能重复')
    if len(normalized_devices) > len(normalized_numbers):
        raise DistributionValidationError('设备数量不能超过申请号数量')
    return collector, normalized_numbers, normalized_devices


def validate_collection_task_id(task_id) -> str:
    if not isinstance(task_id, str) or not _ID_PATTERN.fullmatch(task_id):
        raise DistributionValidationError('分单编号格式不正确')
    return task_id


def validate_collection_credentials(task_id, token, worker_id) -> tuple:
    validate_collection_task_id(task_id)
    if not isinstance(token, str) or not 16 <= len(token) <= 256 or not token.isascii():
        raise DistributionAuthorizationError('分单凭据不正确')
    if not isinstance(worker_id, str) or not _ID_PATTERN.fullmatch(worker_id):
        raise DistributionValidationError('工作机编号格式不正确')
    return task_id, token, worker_id


def validate_collection_progress(progress) -> dict:
    allowed_fields = {'state', 'current_application_no', 'message', 'completed', 'succeeded', 'failed'}
    if not isinstance(progress, dict) or set(progress) - allowed_fields:
        raise DistributionValidationError('工作机进度字段不正确')
    normalized_progress = dict(progress)
    for name, maximum_length in (('state', 80), ('message', 500)):
        if name in progress and (not isinstance(progress[name], str) or len(progress[name]) > maximum_length):
            raise DistributionValidationError(f'进度 {name} 格式不正确')
    for name in ('completed', 'succeeded', 'failed'):
        if name in progress and (type(progress[name]) is not int or not 0 <= progress[name] <= 500):
            raise DistributionValidationError('进度完成数量不正确')
    if progress.get('succeeded', 0) + progress.get('failed', 0) > progress.get('completed', 0):
        raise DistributionValidationError('进度成功与失败总数不能超过完成数')
    if progress.get('current_application_no'):
        normalized_progress['current_application_no'] = _application_number(progress['current_application_no'])
    elif 'current_application_no' in progress and progress['current_application_no'] not in ('', None):
        raise DistributionValidationError('当前申请号格式不正确')
    return normalized_progress


def canonical_collection_json(payload) -> str:
    """Stable, finite JSON is the identity used for retries and optimistic writes."""
    try:
        serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    except (TypeError, ValueError, RecursionError) as error:
        raise DistributionValidationError('提交内容必须为有限 JSON 值') from error
    return serialized


def collection_domain_fingerprint(collector: str, patent_record: dict | None) -> str:
    snapshot = None if patent_record is None else {
        field: patent_record.get(field) for field in COLLECTION_FIELDS[collector]
    }
    return hashlib.sha256(canonical_collection_json(snapshot).encode('utf-8')).hexdigest()


def validate_collection_transfer(collector: str, transfer) -> dict:
    allowed_fields = {'protocol_version', 'application_no', 'status', 'fields', 'reason'}
    if not isinstance(transfer, dict) or set(transfer) - allowed_fields:
        raise DistributionValidationError('回传协议字段不正确')
    if type(transfer.get('protocol_version')) is not int or transfer['protocol_version'] != PROTOCOL_VERSION:
        raise DistributionValidationError('不支持的回传协议版本')
    application_no = _application_number(transfer.get('application_no'))
    status = transfer.get('status')
    if status not in ('success', 'failed', 'interrupted'):
        raise DistributionValidationError('回传状态不正确')
    fields = transfer.get('fields')
    reason = transfer.get('reason', '')
    if not isinstance(fields, dict) or set(fields) - set(COLLECTION_FIELDS[collector]):
        raise DistributionValidationError('回传字段超出本分单的采集范围')
    if not isinstance(reason, str) or len(reason) > 1000:
        raise DistributionValidationError('失败原因不能超过 1000 个字符')
    if status != 'success' and fields:
        raise DistributionValidationError('失败或中断的回传不能包含业务字段')
    normalized_transfer = {
        'protocol_version': PROTOCOL_VERSION,
        'application_no': application_no,
        'status': status,
        'fields': dict(fields),
        'reason': reason,
    }
    if len(canonical_collection_json(normalized_transfer).encode('utf-8')) > MAX_COLLECTION_TRANSFER_BYTES:
        raise DistributionValidationError('单项提交不能超过 8 MB')
    if status != 'success':
        return normalized_transfer
    for name, value in fields.items():
        if name in _LIST_FIELDS:
            if name == 'late_fee_schedule_records' and value is None:
                continue
            if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
                raise DistributionValidationError(f'{name} 必须是对象列表，空列表有效')
        elif name == 'bhsjtzs_data':
            if value is not None and not isinstance(value, (dict, list)):
                raise DistributionValidationError('驳回通知书内容必须是对象或列表')
        elif name == 'status_code':
            if type(value) is not int or value != 200:
                raise DistributionValidationError('主采集成功需要 status_code=200')
        elif name == 'response_time_ms':
            if value is not None and (type(value) not in (int, float) or not math.isfinite(value) or value < 0):
                raise DistributionValidationError('响应耗时格式不正确')
        elif name == 'detected':
            if value is not None and (type(value) not in (bool, int) or value not in (0, 1)):
                raise DistributionValidationError('检测状态格式不正确')
        elif value is not None and (not isinstance(value, str) or len(value) > 100000):
            raise DistributionValidationError(f'{name} 必须是字符串或空值')
    if collector == 'main':
        if fields.get('status_code') != 200 or any(
            not isinstance(fields.get(field), str) or not fields[field].strip()
            for field in ('zhuanlimc', 'shenqingrxm')
        ):
            raise DistributionValidationError('主采集成功需要状态 200、专利名称和申请人')
    elif collector == 'fwxx':
        if 'fwxx_list' not in fields or parse_timestamp(fields.get('fwxx_collected_at')) is None:
            raise DistributionValidationError('发文成功需要发文列表和有效的采集时间')
    else:
        if any(field not in fields for field in _REQUIRED_FEE_LISTS) or parse_timestamp(fields.get('fee_snapshot_at')) is None:
            raise DistributionValidationError('费用成功需要应缴、已缴、收据列表及有效的快照时间')
    return normalized_transfer
