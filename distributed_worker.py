#!/usr/bin/env python3
"""Run a fixed remote assignment in an isolated local database and durable outbox."""

from __future__ import annotations

import argparse
import errno
import json
import os
import re
import secrets
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from atomic_write import write_json_atomic
from collection_checkpoint import CollectionBatch, list_collection_batches, read_collection_batch
from collection_distribution import (
    COLLECTION_FIELDS, PROTOCOL_VERSION, validate_collection_assignment,
    validate_collection_transfer,
)
from collection_watchdog import terminate_process_tree
from db_manager import PatentsDB
from desktop_collection_lock import reserve_detail_collection_desktop, reserve_supervised_collection
from settings import (
    BASE_DIR, COLLECTION_BATCHES_DIR, DATA_DIR, PATENTS_DB_FILE,
    WORKER_ASSIGNMENT_FILE, WORKER_CONNECTION_FILE, WORKER_EXECUTION_LOCK_FILE,
    WORKER_ID_FILE, WORKER_OUTBOX_FILE, WORKER_PROXY_LOG_FILE,
    WORKER_RECEIPTS_FILE, WORKER_REGISTRY_LOCK_FILE, WORKER_STATE_FILE,
    WORKER_TASKS_DIR,
)

_TASK_ID_PATTERN = re.compile(r'^[0-9a-f]{32}$')
_COLLECTOR_SCRIPTS = {'main': 'main_automation.py', 'fwxx': 'collect_fwxx.py', 'fees': 'collect_fees.py'}
_PROGRESS_INTERVAL_SECONDS = 10.0
_COORDINATOR_TIMEOUT_SECONDS = 3.0
_PROXY_START_TIMEOUT_SECONDS = 20.0
_stop_requested = False


class WorkerConnectionError(RuntimeError):
    """The coordinator did not acknowledge a bounded authenticated request."""


class WorkerAssignmentRejectedError(WorkerConnectionError):
    """The coordinator revoked the invitation or rejected its ownership."""


class WorkerTaskBusyError(RuntimeError):
    """Another worker owns the local assignment or registry."""


def _task_directory(task_id: str) -> Path:
    if not isinstance(task_id, str) or not _TASK_ID_PATTERN.fullmatch(task_id):
        raise ValueError('分单编号必须为 32 位小写十六进制字符')
    return WORKER_TASKS_DIR / task_id


def _coordinator_url(value: str) -> str:
    if not isinstance(value, str) or any(ord(character) < 33 for character in value):
        raise ValueError('主机地址必须为完整的 HTTP 或 HTTPS 地址')
    try:
        parsed = urllib.parse.urlsplit(value)
        port = parsed.port
    except ValueError as error:
        raise ValueError('主机地址格式不正确') from error
    if (
        parsed.scheme not in {'http', 'https'} or not parsed.hostname
        or parsed.username is not None or parsed.password is not None
        or parsed.query or parsed.fragment or '\\' in value
        or (port is not None and not 1 <= port <= 65535)
    ):
        raise ValueError('主机地址只允许 HTTP(S)，不能包含账号、查询参数或片段')
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc.lower(), parsed.path.rstrip('/'), '', ''))


@contextmanager
def _reserve_worker_file(lock_path: Path):
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open('a+b') as lock_stream:
        lock_stream.seek(0, os.SEEK_END)
        if not lock_stream.tell():
            lock_stream.write(b'\0')
            lock_stream.flush()
        lock_stream.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(lock_stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock_stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            if error.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK} or getattr(error, 'winerror', None) in {33, 36}:
                raise WorkerTaskBusyError('该工作机任务正在运行，请等待结束') from error
            raise
        yield


def _read_worker_document(document_path: Path) -> dict:
    try:
        document = json.loads(document_path.read_text(encoding='utf-8'))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f'无法读取工作机文件：{document_path.name}') from error
    if not isinstance(document, dict):
        raise ValueError(f'工作机文件格式不正确：{document_path.name}')
    return document


def register_worker_assignment(coordinator_url: str, task_id: str, access_token: str) -> str:
    """Persist an invitation locally without contacting its coordinator."""
    task_directory = _task_directory(task_id)
    coordinator_url = _coordinator_url(coordinator_url)
    if (
        not isinstance(access_token, str) or not 16 <= len(access_token) <= 256
        or not access_token.isascii() or any(ord(character) < 33 for character in access_token)
    ):
        raise ValueError('分单凭据格式不正确')
    connection = {'coordinator_url': coordinator_url, 'task_id': task_id, 'access_token': access_token}
    with _reserve_worker_file(WORKER_REGISTRY_LOCK_FILE):
        task_directory.mkdir(parents=True, exist_ok=True)
        connection_path = task_directory / WORKER_CONNECTION_FILE.name
        if connection_path.exists():
            existing_connection = _read_worker_document(connection_path)
            if (
                existing_connection.get('coordinator_url') != coordinator_url
                or existing_connection.get('task_id') != task_id
                or not secrets.compare_digest(str(existing_connection.get('access_token', '')), access_token)
            ):
                raise ValueError('该分单已绑定其他主机或凭据，不能覆盖本机未交付成果')
            return task_id
        # NamedTemporaryFile in write_json_atomic creates a private mode-0600 file.
        write_json_atomic(connection_path, connection)
        connection_path.chmod(0o600)
    return task_id


def _worker_identity() -> str:
    with _reserve_worker_file(WORKER_REGISTRY_LOCK_FILE):
        if WORKER_ID_FILE.exists():
            worker_id = _read_worker_document(WORKER_ID_FILE).get('worker_id')
            if not isinstance(worker_id, str) or not _TASK_ID_PATTERN.fullmatch(worker_id):
                raise ValueError('本机工作机编号损坏，请保留任务目录并检查 worker_id.json')
            return worker_id
        worker_id = uuid.uuid4().hex
        write_json_atomic(WORKER_ID_FILE, {'worker_id': worker_id})
        return worker_id


class _RejectCoordinatorRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise WorkerConnectionError('主机地址发生重定向，请使用最终地址重新分单')


class CoordinatorConnection:
    """Own request authentication, bounded transport and response validation."""

    def __init__(self, task_id: str):
        connection_path = _task_directory(task_id) / WORKER_CONNECTION_FILE.name
        connection = _read_worker_document(connection_path)
        self._base_url = _coordinator_url(connection.get('coordinator_url'))
        self.task_id = task_id
        token = connection.get('access_token')
        if connection.get('task_id') != task_id or not isinstance(token, str) or not 16 <= len(token) <= 256 or not token.isascii() or any(ord(character) < 33 for character in token):
            raise ValueError('本机分单连接配置不正确')
        self._headers = {
            'Accept': 'application/json', 'Content-Type': 'application/json',
            'X-CNIPA-Task-Token': token, 'X-CNIPA-Worker-ID': _worker_identity(),
        }
        self._opener = urllib.request.build_opener(_RejectCoordinatorRedirect())

    def _exchange(self, request: urllib.request.Request) -> dict:
        for name, value in self._headers.items():
            request.add_header(name, value)
        try:
            with self._opener.open(request, timeout=_COORDINATOR_TIMEOUT_SECONDS) as response:
                response_bytes = response.read(2 * 1024 * 1024 + 1)
            if len(response_bytes) > 2 * 1024 * 1024:
                raise WorkerConnectionError('主机响应超过允许大小')
            response_document = json.loads(response_bytes.decode('utf-8'))
        except urllib.error.HTTPError as error:
            if error.code in {401, 403, 404, 409}:
                raise WorkerAssignmentRejectedError(
                    f'主机拒绝该分单（HTTP {error.code}），已停止采集并保留本机成果'
                ) from error
            raise WorkerConnectionError(f'主机拒绝请求（HTTP {error.code}），本机成果已保留') from error
        except (urllib.error.URLError, OSError, UnicodeError, json.JSONDecodeError) as error:
            raise WorkerConnectionError('主机连接失败或响应无效，本机成果已保留，可稍后重传') from error
        if not isinstance(response_document, dict):
            raise WorkerConnectionError('主机响应格式不正确')
        return response_document

    def claim(self) -> dict:
        request = urllib.request.Request(f'{self._base_url}/api/worker/assignments/{self.task_id}')
        assignment = self._exchange(request).get('assignment')
        if not isinstance(assignment, dict) or type(assignment.get('protocol_version')) is not int or assignment['protocol_version'] != PROTOCOL_VERSION or assignment.get('task_id') != self.task_id:
            raise WorkerConnectionError('主机返回的分单编号或协议版本不匹配')
        collector, applications, devices = validate_collection_assignment(
            assignment.get('collector'), assignment.get('application_nos'), [assignment.get('device_name')],
        )
        if assignment.get('state') not in {'pending', 'running', 'completed', 'cancelled'}:
            raise WorkerConnectionError('主机返回的分单状态不正确')
        return {
            'protocol_version': PROTOCOL_VERSION, 'task_id': self.task_id,
            'collector': collector, 'application_nos': applications,
            'device_name': devices[0], 'state': assignment['state'],
        }

    def publish_progress(self, progress: dict) -> None:
        request = urllib.request.Request(
            f'{self._base_url}/api/worker/assignments/{self.task_id}/progress',
            data=json.dumps(progress, ensure_ascii=False).encode('utf-8'), method='POST',
        )
        self._exchange(request)

    def deliver_item(self, transfer: dict) -> dict:
        request = urllib.request.Request(
            f'{self._base_url}/api/worker/assignments/{self.task_id}/results',
            data=json.dumps(transfer, ensure_ascii=False, allow_nan=False).encode('utf-8'), method='POST',
        )
        receipt = self._exchange(request).get('receipt')
        if (
            not isinstance(receipt, dict) or receipt.get('application_no') != transfer['application_no']
            or receipt.get('task_id') != self.task_id
            or type(receipt.get('protocol_version')) is not int or receipt['protocol_version'] != PROTOCOL_VERSION
            or receipt.get('status') not in {transfer['status'], 'conflict'}
            or type(receipt.get('duplicate')) is not bool
        ):
            raise WorkerConnectionError('主机回执不匹配，本项仍保留为待回传')
        return {key: receipt[key] for key in ('protocol_version', 'task_id', 'application_no', 'status', 'duplicate')}


def _write_worker_state(state: str, batch_id: str, reason: str = '') -> None:
    write_json_atomic(WORKER_STATE_FILE, {
        'state': state, 'batch_id': batch_id, 'reason': reason,
        'updated_at': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
    })


def list_worker_assignments() -> list[dict]:
    """Return local progress without exposing task credentials or patent fields."""
    summaries = []
    for task_directory in sorted(WORKER_TASKS_DIR.glob('*')):
        if not task_directory.is_dir() or not _TASK_ID_PATTERN.fullmatch(task_directory.name):
            continue
        summary = {
            'task_id': task_directory.name, 'coordinator_url': '', 'collector': '',
            'device_name': '', 'state': 'registered', 'total': 0, 'completed': 0,
            'succeeded': 0, 'failed': 0, 'interrupted': 0, 'conflict': 0,
            'acknowledged': 0, 'delivery_pending': 0, 'reason': '', 'updated_at': '',
            'acknowledged_counts': {'success': 0, 'failed': 0, 'interrupted': 0, 'conflict': 0},
        }
        try:
            connection = _read_worker_document(task_directory / WORKER_CONNECTION_FILE.name)
            summary['coordinator_url'] = _coordinator_url(connection.get('coordinator_url'))
            documents = {}
            for document_path in (WORKER_ASSIGNMENT_FILE, WORKER_STATE_FILE, WORKER_OUTBOX_FILE, WORKER_RECEIPTS_FILE):
                local_path = task_directory / document_path.name
                documents[document_path.name] = _read_worker_document(local_path) if local_path.exists() else {}
            assignment = documents[WORKER_ASSIGNMENT_FILE.name]
            local_state = documents[WORKER_STATE_FILE.name]
            summary.update({key: assignment.get(key, '') for key in ('collector', 'device_name')})
            summary['total'] = len(assignment.get('application_nos', []))
            summary.update({key: local_state[key] for key in ('state', 'reason', 'updated_at') if key in local_state})
            outbox_items = documents[WORKER_OUTBOX_FILE.name].get('items', [])
            receipts = documents[WORKER_RECEIPTS_FILE.name]
            if outbox_items:
                summary['completed'] = len(outbox_items)
                summary['succeeded'] = sum(item['status'] == 'success' for item in outbox_items)
                summary['failed'] = sum(item['status'] == 'failed' for item in outbox_items)
                summary['interrupted'] = sum(item['status'] == 'interrupted' for item in outbox_items)
                summary['acknowledged'] = sum(item['application_no'] in receipts for item in outbox_items)
                summary['conflict'] = sum(receipt['status'] == 'conflict' for receipt in receipts.values())
                summary['acknowledged_counts'] = {
                    status: sum(receipt['status'] == status for receipt in receipts.values())
                    for status in ('success', 'failed', 'interrupted', 'conflict')
                }
                summary['delivery_pending'] = len(outbox_items) - summary['acknowledged']
                if summary['delivery_pending']:
                    summary['state'] = 'rejected' if local_state.get('state') == 'rejected' else 'delivery_pending'
                else:
                    summary['state'] = 'delivered'
            elif local_state.get('batch_id'):
                batch_path = task_directory / COLLECTION_BATCHES_DIR.name / f"{local_state['batch_id']}.json"
                batch = _read_worker_document(batch_path)
                summary['succeeded'] = sum(item['status'] == 'success' for item in batch['items'])
                summary['failed'] = sum(item['status'] == 'failed' for item in batch['items'])
                summary['completed'] = summary['succeeded'] + summary['failed']
        except (ValueError, OSError, KeyError, TypeError):
            summary.update(state='unreadable', reason='本机任务文件不完整或损坏，请保留任务目录后检查')
        summaries.append(summary)
    return summaries


def _existing_batch(assignment: dict) -> str | None:
    batches = list_collection_batches()
    if not batches:
        return None
    if len(batches) != 1 or batches[0]['status'] == 'unreadable':
        raise ValueError('隔离任务目录包含多个或损坏的采集批次，拒绝自动重采')
    batch = read_collection_batch(batches[0]['id'])
    if batch['collector'] != assignment['collector'] or [item['application_no'] for item in batch['items']] != assignment['application_nos']:
        raise ValueError('本机批次与主机分单不一致，拒绝自动重采')
    if batch['status'] == 'running':
        raise WorkerTaskBusyError('本机采集子进程仍在运行，暂不能封存或重传')
    return batch['id']


def _seal_outbox(assignment: dict, batch_id: str) -> None:
    """Freeze only committed successes; recovery never turns old rows into success."""
    if WORKER_OUTBOX_FILE.exists():
        return
    batch = read_collection_batch(batch_id)
    if batch['status'] == 'running':
        raise WorkerTaskBusyError('采集子进程尚未退出，不能封存结果')
    task_db = PatentsDB(PATENTS_DB_FILE)
    transfers = []
    for item in batch['items']:
        status = item['status'] if item['status'] in {'success', 'failed'} else 'interrupted'
        fields = {}
        reason = item['reason'][:1000]
        if status == 'success':
            patent_record = task_db.get_record(item['application_no'])
            if patent_record is None:
                status, reason = 'interrupted', '本机批次成功但缺少已保存的采集记录，未回传业务字段'
            else:
                fields = {name: patent_record.get(name) for name in COLLECTION_FIELDS[assignment['collector']]}
        elif status == 'interrupted' and not reason:
            reason = '采集已停止，本项没有完整的成功记录；请由主机重新分单'
        transfer = {
            'protocol_version': PROTOCOL_VERSION, 'application_no': item['application_no'],
            'status': status, 'fields': fields, 'reason': reason,
        }
        try:
            transfer = validate_collection_transfer(assignment['collector'], transfer)
        except ValueError:
            transfer.update(status='interrupted', fields={}, reason='本机采集记录不满足回传完整性要求，原始记录已保留')
        transfers.append(transfer)
    write_json_atomic(WORKER_OUTBOX_FILE, {
        'protocol_version': PROTOCOL_VERSION, 'task_id': assignment['task_id'],
        'collector': assignment['collector'], 'items': transfers,
    })
    previous_state = _read_worker_document(WORKER_STATE_FILE) if WORKER_STATE_FILE.exists() else {}
    state = 'rejected' if previous_state.get('state') == 'rejected' else 'delivery_pending'
    _write_worker_state(state, batch_id, previous_state.get('reason', ''))


def _deliver_outbox(connection: CoordinatorConnection) -> int:
    outbox = _read_worker_document(WORKER_OUTBOX_FILE)
    if outbox.get('task_id') != connection.task_id or outbox.get('collector') not in COLLECTION_FIELDS or outbox.get('protocol_version') != PROTOCOL_VERSION or not isinstance(outbox.get('items'), list):
        raise ValueError('本机待回传文件与分单不匹配')
    transfers = [validate_collection_transfer(outbox['collector'], item) for item in outbox['items']]
    assignment = _read_worker_document(WORKER_ASSIGNMENT_FILE)
    application_nos = [item['application_no'] for item in transfers]
    if (
        assignment.get('task_id') != connection.task_id
        or assignment.get('collector') != outbox['collector']
        or application_nos != assignment.get('application_nos')
    ):
        raise ValueError('本机待回传范围与原分单不一致')
    receipts = _read_worker_document(WORKER_RECEIPTS_FILE) if WORKER_RECEIPTS_FILE.exists() else {}
    submissions = {item['application_no']: item for item in transfers}
    for application_no, receipt in receipts.items():
        if (
            application_no not in submissions or not isinstance(receipt, dict)
            or receipt.get('application_no') != application_no
            or receipt.get('task_id') != connection.task_id
            or type(receipt.get('protocol_version')) is not int or receipt['protocol_version'] != PROTOCOL_VERSION
            or receipt.get('status') not in {submissions[application_no]['status'], 'conflict'}
            or type(receipt.get('duplicate')) is not bool
        ):
            raise ValueError('本机确认记录损坏，拒绝把未确认成果标为已交付')
    for transfer in transfers:
        if _stop_requested:
            print('[工作机] 待回传成果已保留，稍后点击“仅重传结果”。')
            return 130
        application_no = transfer['application_no']
        if application_no in receipts:
            continue
        try:
            receipt = connection.deliver_item(transfer)
        except WorkerAssignmentRejectedError as error:
            local_state = _read_worker_document(WORKER_STATE_FILE)
            _write_worker_state('rejected', local_state['batch_id'], str(error))
            raise
        receipts[application_no] = receipt
        write_json_atomic(WORKER_RECEIPTS_FILE, receipts)
        print(f"[工作机] 主机已确认 {application_no}：{receipt['status']}")
    local_state = _read_worker_document(WORKER_STATE_FILE)
    _write_worker_state('delivered', local_state['batch_id'])
    counts = {status: sum(receipt['status'] == status for receipt in receipts.values()) for status in ('success', 'failed', 'interrupted', 'conflict')}
    print(
        f"[工作机] 已交付 {len(transfers)} 项；成功 {counts['success']}、失败 {counts['failed']}、"
        f"中断 {counts['interrupted']}、冲突 {counts['conflict']}。"
    )
    if counts['failed'] + counts['interrupted'] + counts['conflict']:
        print('[工作机] 未成功项目请在主机检查后重新分单。')
    return 0


def _unused_proxy_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as reservation:
        reservation.bind(('127.0.0.1', 0))
        return reservation.getsockname()[1]


def _launch_owned_child(command: list[str], environment: dict, **streams) -> subprocess.Popen:
    launch_arguments = {'cwd': str(BASE_DIR), 'env': environment, 'stdin': subprocess.PIPE, **streams}
    if os.name == 'nt':
        launch_arguments['creationflags'] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        launch_arguments['start_new_session'] = True
    child = subprocess.Popen(command, **launch_arguments)
    child.stdin.close()
    return child


def _await_proxy(proxy_child: subprocess.Popen, proxy_port: int) -> None:
    deadline = time.monotonic() + _PROXY_START_TIMEOUT_SECONDS
    while not _stop_requested and time.monotonic() < deadline:
        if proxy_child.poll() is not None:
            raise RuntimeError('任务代理启动失败，请检查本机任务目录的 worker_proxy.log')
        try:
            with socket.create_connection(('127.0.0.1', proxy_port), timeout=0.2):
                return
        except OSError:
            time.sleep(0.1)
    raise RuntimeError('任务代理未能就绪或启动已被中断，本次不打开浏览器')


def _collect_assignment(assignment: dict, batch_id: str, connection: CoordinatorConnection) -> None:
    proxy_port = _unused_proxy_port()
    environment = {
        **os.environ, 'CNIPA_DATA_DIR': str(DATA_DIR), 'USE_MITM_PROXY': 'true',
        'MITM_HOST': '127.0.0.1', 'MITM_PORT': str(proxy_port),
    }
    proxy_child = None
    collector_child = None
    with WORKER_PROXY_LOG_FILE.open('a', encoding='utf-8') as proxy_log:
        try:
            proxy_child = _launch_owned_child(
                [sys.executable, '-u', str(BASE_DIR / 'start_mitm_proxy.py')], environment,
                stdout=proxy_log, stderr=subprocess.STDOUT,
            )
            _await_proxy(proxy_child, proxy_port)
            if _stop_requested:
                return
            collector_child = _launch_owned_child(
                [sys.executable, '-u', str(BASE_DIR / _COLLECTOR_SCRIPTS[assignment['collector']]), '--resume-batch', batch_id],
                environment,
            )
            _write_worker_state('collecting', batch_id)
            next_progress_at = 0.0
            while not _stop_requested and collector_child.poll() is None:
                if proxy_child.poll() is not None:
                    raise RuntimeError('任务代理意外退出，已停止采集并保留已完成成果')
                if time.monotonic() >= next_progress_at:
                    batch = read_collection_batch(batch_id)
                    try:
                        connection.publish_progress({
                            'state': 'collecting', 'completed': batch['succeeded'] + batch['failed'],
                            'succeeded': batch['succeeded'], 'failed': batch['failed'],
                        })
                    except WorkerAssignmentRejectedError:
                        raise
                    except WorkerConnectionError:
                        print('[工作机] 暂时无法上报进度；采集继续，成果保存在本机。')
                    next_progress_at = time.monotonic() + _PROGRESS_INTERVAL_SECONDS
                time.sleep(0.2)
        finally:
            try:
                if collector_child is not None:
                    terminate_process_tree(collector_child)
            finally:
                if proxy_child is not None:
                    terminate_process_tree(proxy_child)


def _require_isolated_task(task_id: str) -> None:
    if DATA_DIR.resolve() != _task_directory(task_id).resolve():
        raise ValueError('工作机必须在专用任务数据目录启动，拒绝使用日常专利库')


def run_worker_assignment(task_id: str) -> int:
    _require_isolated_task(task_id)
    with _reserve_worker_file(WORKER_EXECUTION_LOCK_FILE), reserve_supervised_collection('多机分单采集'):
        connection = CoordinatorConnection(task_id)
        if WORKER_OUTBOX_FILE.exists():
            return _deliver_outbox(connection)
        if WORKER_ASSIGNMENT_FILE.exists():
            assignment = _read_worker_document(WORKER_ASSIGNMENT_FILE)
            batch_id = _existing_batch(assignment)
            if batch_id:
                print('[工作机] 发现已有采集批次，只封存并回传现有成果，不重复采集。')
                _seal_outbox(assignment, batch_id)
                return _deliver_outbox(connection)
        with reserve_detail_collection_desktop('多机分单准备'):
            try:
                assignment = connection.claim()
            except WorkerAssignmentRejectedError as error:
                _write_worker_state('rejected', '', str(error))
                raise
            if assignment['state'] in {'completed', 'cancelled'}:
                raise ValueError('主机分单已结束或取消，请重新分单；本机不会重复采集')
            write_json_atomic(WORKER_ASSIGNMENT_FILE, assignment)
            if assignment['collector'] != 'main':
                PatentsDB(PATENTS_DB_FILE).upsert_batch([
                    {'application_no': application_no} for application_no in assignment['application_nos']
                ])
            batch_id = CollectionBatch.prepare(assignment['collector'], assignment['application_nos'])
            _write_worker_state('prepared', batch_id)
        try:
            _collect_assignment(assignment, batch_id, connection)
        except WorkerAssignmentRejectedError as error:
            _write_worker_state('rejected', batch_id, str(error)[:1000])
            print(f'[工作机] 采集已停止：{error}')
        except Exception as error:
            _write_worker_state('interrupted', batch_id, str(error)[:1000])
            print(f'[工作机] 采集中断：{error}')
        finally:
            _seal_outbox(assignment, batch_id)
        return _deliver_outbox(connection)


def deliver_worker_assignment(task_id: str) -> int:
    """Retry delivery or seal an interrupted batch; never launch a collector."""
    _require_isolated_task(task_id)
    with _reserve_worker_file(WORKER_EXECUTION_LOCK_FILE):
        connection = CoordinatorConnection(task_id)
        if not WORKER_OUTBOX_FILE.exists():
            assignment = _read_worker_document(WORKER_ASSIGNMENT_FILE)
            batch_id = _existing_batch(assignment)
            if batch_id is None:
                raise ValueError('本机尚无采集批次，不能重传；请先启动分单采集')
            _seal_outbox(assignment, batch_id)
        return _deliver_outbox(connection)


def _request_worker_stop(signum, frame) -> None:
    del signum, frame
    global _stop_requested
    _stop_requested = True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='运行固定分单，或仅重传已封存的采集成果')
    commands = parser.add_subparsers(dest='command', required=True)
    for command in ('run', 'deliver'):
        commands.add_parser(command).add_argument('--task-id', required=True)
    arguments = parser.parse_args(argv)
    try:
        task_directory = _task_directory(arguments.task_id).resolve()
        if DATA_DIR.resolve() != task_directory:
            os.execve(sys.executable, [
                sys.executable, '-u', str(BASE_DIR / 'distributed_worker.py'),
                arguments.command, '--task-id', arguments.task_id,
            ], {**os.environ, 'CNIPA_DATA_DIR': str(task_directory)})
        signal.signal(signal.SIGTERM, _request_worker_stop)
        signal.signal(signal.SIGINT, _request_worker_stop)
        operation = run_worker_assignment if arguments.command == 'run' else deliver_worker_assignment
        return operation(arguments.task_id)
    except (ValueError, OSError, RuntimeError) as error:
        print(f'[工作机] {error}')
        return 1


if __name__ == '__main__':
    sys.exit(main())
