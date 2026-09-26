"""Worker durability and isolation without browser or real patent data access."""

import contextlib
import io
import json
import os
import signal
import subprocess
import sys
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import Mock

import pytest

import collection_checkpoint
import desktop_collection_lock
import distributed_worker as worker
from atomic_write import write_json_atomic
from collection_checkpoint import CollectionBatch
from db_manager import PatentsDB


TASK_ID = 'a' * 32
APPLICATIONS = ['2018108715138', '202310411762X']
ACCESS_TOKEN = 'private-assignment-token-do-not-display'


@pytest.fixture
def isolated_task(tmp_path, monkeypatch):
    local_directory = tmp_path / 'local'
    task_directory = local_directory / 'worker_tasks' / TASK_ID
    task_directory.mkdir(parents=True)
    monkeypatch.setattr(worker, 'WORKER_TASKS_DIR', local_directory / 'worker_tasks')
    monkeypatch.setattr(worker, 'WORKER_REGISTRY_LOCK_FILE', local_directory / 'worker_registry.lock')
    monkeypatch.setattr(worker, 'WORKER_ID_FILE', local_directory / 'worker_id.json')
    monkeypatch.setattr(worker, 'DATA_DIR', task_directory)
    for constant_name in (
        'WORKER_CONNECTION_FILE', 'WORKER_ASSIGNMENT_FILE', 'WORKER_STATE_FILE',
        'WORKER_OUTBOX_FILE', 'WORKER_RECEIPTS_FILE', 'WORKER_EXECUTION_LOCK_FILE',
        'WORKER_PROXY_LOG_FILE', 'PATENTS_DB_FILE', 'COLLECTION_BATCHES_DIR',
    ):
        monkeypatch.setattr(worker, constant_name, task_directory / getattr(worker, constant_name).name)
    monkeypatch.setattr(collection_checkpoint, 'COLLECTION_BATCHES_DIR', worker.COLLECTION_BATCHES_DIR)
    monkeypatch.setattr(desktop_collection_lock, 'SUPERVISED_COLLECTION_LOCK_FILE', local_directory / 'supervised.lock')
    monkeypatch.setattr(desktop_collection_lock, 'DETAIL_COLLECTION_LOCK_FILE', local_directory / 'desktop.lock')
    monkeypatch.setattr(worker, '_stop_requested', False)
    return task_directory


def assignment(collector='fees'):
    return {
        'protocol_version': 1, 'task_id': TASK_ID, 'collector': collector,
        'device_name': '工作机 B', 'application_nos': APPLICATIONS, 'state': 'running',
    }


def fee_fields():
    return {
        'payable_fee_records': [], 'late_fee_schedule_records': None,
        'paid_fee_records': [{'fee': '年费'}], 'fee_receipt_dispatch_records': [],
        'fee_snapshot_at': '2026-09-26T10:00:00.123456Z',
    }


def successful_fields(collector):
    if collector == 'fees':
        return fee_fields()
    if collector == 'fwxx':
        return {
            'fwxx_list': [{'name': '通知书'}], 'bhsjtzs_xiazaisj': None,
            'bhsjtzs_data': None, 'fwxx_collected_at': '2026-09-26T10:00:00Z',
        }
    return {
        'status_code': 200, 'zhuanlimc': '测试专利', 'shenqingrxm': '测试申请人',
        'timestamp': '2026-09-26T10:00:00Z',
    }


def finish_batch(task_directory, batch_id, collector):
    with CollectionBatch.resume(collector, task_directory / 'checkpoint.txt', batch_id) as checkpoint:
        checkpoint.select_pending(None)
        checkpoint.record_started(APPLICATIONS[0])
        PatentsDB(worker.PATENTS_DB_FILE).upsert({
            'application_no': APPLICATIONS[0], **successful_fields(collector),
            'daili_jg': '不得回传的额外字段',
        })
        checkpoint.record_success(APPLICATIONS[0])
        checkpoint.record_started(APPLICATIONS[1])
        checkpoint.record_failure(APPLICATIONS[1], '本次未收到响应')


class CoordinatorStub:
    def __init__(self, collector='fees'):
        self.task_id = TASK_ID
        self.collector = collector
        self.submissions = []
        self.progress_updates = []
        self.offline = False

    def claim(self):
        return assignment(self.collector)

    def deliver_item(self, transfer):
        if self.offline:
            raise worker.WorkerConnectionError('网络断开')
        self.submissions.append(transfer)
        return {'protocol_version': 1, 'task_id': TASK_ID, 'application_no': transfer['application_no'], 'status': transfer['status'], 'duplicate': False}

    def publish_progress(self, progress):
        self.progress_updates.append(progress)


def test_registration_is_private_idempotent_and_rejects_rebinding(isolated_task):
    assert worker.register_worker_assignment('http://127.0.0.1:8765/', TASK_ID, ACCESS_TOKEN) == TASK_ID
    before = worker.WORKER_CONNECTION_FILE.read_bytes()
    assert worker.register_worker_assignment('http://127.0.0.1:8765', TASK_ID, ACCESS_TOKEN) == TASK_ID
    assert worker.WORKER_CONNECTION_FILE.read_bytes() == before
    if os.name != 'nt':
        assert worker.WORKER_CONNECTION_FILE.stat().st_mode & 0o777 == 0o600
    for origin, credential in [('https://elsewhere.example', ACCESS_TOKEN), ('http://127.0.0.1:8765', 'another-secret-token')]:
        with pytest.raises(ValueError, match='不能覆盖'):
            worker.register_worker_assignment(origin, TASK_ID, credential)
    assert ACCESS_TOKEN not in json.dumps(worker.list_worker_assignments())


@pytest.mark.parametrize('coordinator_url', [
    'file:///tmp/tasks', 'http://user:password@host', 'http://host/?token=x',
    'http://host/#secret', 'http://host:70000', 'http://host\nX-Foo:x',
    'http://host\\evil', 'not-an-url', '',
])
def test_registration_rejects_unsafe_urls_without_network(isolated_task, monkeypatch, coordinator_url):
    network = Mock(side_effect=AssertionError('registration must not contact network'))
    monkeypatch.setattr(urllib.request, 'urlopen', network)
    with pytest.raises(ValueError):
        worker.register_worker_assignment(coordinator_url, TASK_ID, ACCESS_TOKEN)
    network.assert_not_called()


@pytest.mark.parametrize('task_id', ['../escape', 'a' * 31, 'A' * 32, 'a/b', ''])
def test_registration_rejects_noncanonical_task_ids(isolated_task, task_id):
    with pytest.raises(ValueError, match='32 位'):
        worker.register_worker_assignment('http://localhost', task_id, ACCESS_TOKEN)


def test_worker_identity_is_stable_and_never_invents_replacement_for_corruption(isolated_task):
    first_identity = worker._worker_identity()
    assert len(first_identity) == 32
    assert worker._worker_identity() == first_identity
    write_json_atomic(worker.WORKER_ID_FILE, {'worker_id': 'bad'})
    with pytest.raises(ValueError, match='编号损坏'):
        worker._worker_identity()


@pytest.mark.parametrize('collector', ['main', 'fwxx', 'fees'])
def test_run_uses_empty_task_domains_and_exports_only_successful_owned_fields(isolated_task, monkeypatch, collector):
    coordinator = CoordinatorStub(collector)
    monkeypatch.setattr(worker, 'CoordinatorConnection', lambda task_id: coordinator)
    entered = []

    def collect_assigned(assigned, batch_id, connection):
        entered.append(assigned)
        task_db = PatentsDB(worker.PATENTS_DB_FILE)
        if collector == 'main':
            assert task_db.count() == 0
        else:
            assert task_db.count() == 2
            assert all(task_db.get_record(number).get('fwxx_list') is None for number in APPLICATIONS)
            assert all(task_db.get_record(number).get('paid_fee_records') is None for number in APPLICATIONS)
        finish_batch(isolated_task, batch_id, collector)

    monkeypatch.setattr(worker, '_collect_assignment', collect_assigned)
    assert worker.run_worker_assignment(TASK_ID) == 0
    assert len(entered) == 1
    assert len(coordinator.submissions) == 2
    assert coordinator.submissions[0]['status'] == 'success'
    assert set(coordinator.submissions[0]['fields']) == set(worker.COLLECTION_FIELDS[collector])
    assert 'daili_jg' not in coordinator.submissions[0]['fields']
    assert coordinator.submissions[1]['status'] == 'failed'
    assert coordinator.submissions[1]['fields'] == {}
    assert worker.run_worker_assignment(TASK_ID) == 0
    assert len(entered) == 1
    assert len(coordinator.submissions) == 2


def test_network_failure_keeps_immutable_outbox_and_resume_only_delivers(isolated_task, monkeypatch):
    coordinator = CoordinatorStub()
    coordinator.offline = True
    monkeypatch.setattr(worker, 'CoordinatorConnection', lambda task_id: coordinator)
    collect = Mock(side_effect=lambda assigned, batch_id, connection: finish_batch(isolated_task, batch_id, 'fees'))
    monkeypatch.setattr(worker, '_collect_assignment', collect)
    with pytest.raises(worker.WorkerConnectionError):
        worker.run_worker_assignment(TASK_ID)
    sealed_bytes = worker.WORKER_OUTBOX_FILE.read_bytes()
    assert not worker.WORKER_RECEIPTS_FILE.exists()
    coordinator.offline = False
    assert worker.deliver_worker_assignment(TASK_ID) == 0
    assert worker.WORKER_OUTBOX_FILE.read_bytes() == sealed_bytes
    collect.assert_called_once()
    assert len(coordinator.submissions) == 2


def test_lost_ack_retries_same_content_and_accepts_duplicate(isolated_task, monkeypatch):
    coordinator = CoordinatorStub()
    monkeypatch.setattr(worker, 'CoordinatorConnection', lambda task_id: coordinator)
    monkeypatch.setattr(worker, '_collect_assignment', lambda assigned, batch_id, connection: finish_batch(isolated_task, batch_id, 'fees'))
    original_deliver = coordinator.deliver_item
    calls = []

    def deliver_with_lost_ack(transfer):
        calls.append(json.dumps(transfer, sort_keys=True))
        if len(calls) == 1:
            original_deliver(transfer)
            raise worker.WorkerConnectionError('回执丢失')
        receipt = original_deliver(transfer)
        receipt['duplicate'] = transfer['application_no'] == APPLICATIONS[0]
        return receipt

    coordinator.deliver_item = deliver_with_lost_ack
    with pytest.raises(worker.WorkerConnectionError):
        worker.run_worker_assignment(TASK_ID)
    assert worker.deliver_worker_assignment(TASK_ID) == 0
    assert calls[0] == calls[1]
    receipts = json.loads(worker.WORKER_RECEIPTS_FILE.read_text())
    assert receipts[APPLICATIONS[0]]['duplicate'] is True


def test_prior_unsealed_batch_is_recovered_without_recollection(isolated_task, monkeypatch):
    coordinator = CoordinatorStub()
    monkeypatch.setattr(worker, 'CoordinatorConnection', lambda task_id: coordinator)
    collect = Mock(side_effect=AssertionError('recovery must not collect'))
    monkeypatch.setattr(worker, '_collect_assignment', collect)
    write_json_atomic(worker.WORKER_ASSIGNMENT_FILE, assignment())
    batch_id = CollectionBatch.prepare('fees', APPLICATIONS)
    finish_batch(isolated_task, batch_id, 'fees')
    # A hard kill may happen before the separate state file is saved.
    assert not worker.WORKER_STATE_FILE.exists()
    assert worker.run_worker_assignment(TASK_ID) == 0
    collect.assert_not_called()
    assert [item['status'] for item in coordinator.submissions] == ['success', 'failed']


def test_interrupted_and_unattempted_rows_never_export_old_fields(isolated_task):
    write_json_atomic(worker.WORKER_ASSIGNMENT_FILE, assignment())
    batch_id = CollectionBatch.prepare('fees', APPLICATIONS)
    PatentsDB(worker.PATENTS_DB_FILE).upsert({'application_no': APPLICATIONS[0], **fee_fields()})
    with pytest.raises(RuntimeError):
        with CollectionBatch.resume('fees', isolated_task / 'checkpoint.txt', batch_id) as checkpoint:
            checkpoint.select_pending(None)
            checkpoint.record_started(APPLICATIONS[0])
            raise RuntimeError('browser closed')
    worker._seal_outbox(assignment(), batch_id)
    outbox = json.loads(worker.WORKER_OUTBOX_FILE.read_text())
    assert [item['status'] for item in outbox['items']] == ['interrupted', 'interrupted']
    assert all(item['fields'] == {} for item in outbox['items'])


def test_active_child_batch_cannot_be_sealed(isolated_task):
    batch_id = CollectionBatch.prepare('fees', APPLICATIONS)
    with CollectionBatch.resume('fees', isolated_task / 'checkpoint.txt', batch_id):
        with pytest.raises(worker.WorkerTaskBusyError, match='尚未退出'):
            worker._seal_outbox(assignment(), batch_id)
    assert not worker.WORKER_OUTBOX_FILE.exists()


def test_sigterm_keeps_outbox_and_defers_delivery(isolated_task, monkeypatch):
    coordinator = CoordinatorStub()
    monkeypatch.setattr(worker, 'CoordinatorConnection', lambda task_id: coordinator)

    def interrupted_collection(assigned, batch_id, connection):
        finish_batch(isolated_task, batch_id, 'fees')
        worker._request_worker_stop(signal.SIGTERM, None)

    monkeypatch.setattr(worker, '_collect_assignment', interrupted_collection)
    assert worker.run_worker_assignment(TASK_ID) == 130
    assert worker.WORKER_OUTBOX_FILE.exists()
    assert coordinator.submissions == []


def test_corrupted_receipt_does_not_hide_unacknowledged_item(isolated_task, monkeypatch):
    coordinator = CoordinatorStub()
    monkeypatch.setattr(worker, 'CoordinatorConnection', lambda task_id: coordinator)
    write_json_atomic(worker.WORKER_ASSIGNMENT_FILE, assignment())
    batch_id = CollectionBatch.prepare('fees', APPLICATIONS)
    finish_batch(isolated_task, batch_id, 'fees')
    worker._seal_outbox(assignment(), batch_id)
    write_json_atomic(worker.WORKER_RECEIPTS_FILE, {APPLICATIONS[0]: {'status': 'success'}})
    with pytest.raises(ValueError, match='确认记录损坏'):
        worker.deliver_worker_assignment(TASK_ID)
    assert coordinator.submissions == []


def test_outbox_scope_cannot_be_expanded(isolated_task, monkeypatch):
    coordinator = CoordinatorStub()
    monkeypatch.setattr(worker, 'CoordinatorConnection', lambda task_id: coordinator)
    write_json_atomic(worker.WORKER_ASSIGNMENT_FILE, assignment())
    batch_id = CollectionBatch.prepare('fees', APPLICATIONS)
    finish_batch(isolated_task, batch_id, 'fees')
    worker._seal_outbox(assignment(), batch_id)
    outbox = json.loads(worker.WORKER_OUTBOX_FILE.read_text())
    outbox['items'].append(dict(outbox['items'][0]))
    write_json_atomic(worker.WORKER_OUTBOX_FILE, outbox)
    with pytest.raises(ValueError, match='范围与原分单不一致'):
        worker.deliver_worker_assignment(TASK_ID)


def test_local_listing_shows_delivery_progress_without_secrets(isolated_task):
    worker.register_worker_assignment('http://localhost:8765', TASK_ID, ACCESS_TOKEN)
    write_json_atomic(worker.WORKER_ASSIGNMENT_FILE, assignment())
    batch_id = CollectionBatch.prepare('fees', APPLICATIONS)
    finish_batch(isolated_task, batch_id, 'fees')
    worker._seal_outbox(assignment(), batch_id)
    write_json_atomic(worker.WORKER_RECEIPTS_FILE, {
        APPLICATIONS[0]: {'protocol_version': 1, 'task_id': TASK_ID, 'application_no': APPLICATIONS[0], 'status': 'conflict', 'duplicate': False},
    })
    summary = worker.list_worker_assignments()[0]
    assert summary['state'] == 'delivery_pending'
    assert (summary['completed'], summary['succeeded'], summary['failed']) == (2, 1, 1)
    assert (summary['acknowledged'], summary['delivery_pending'], summary['conflict']) == (1, 1, 1)
    assert summary['acknowledged_counts'] == {'success': 0, 'failed': 0, 'interrupted': 0, 'conflict': 1}
    serialized = json.dumps(summary, ensure_ascii=False)
    assert ACCESS_TOKEN not in serialized
    assert '不得回传的额外字段' not in serialized


def test_refuses_default_data_directory(isolated_task, monkeypatch):
    monkeypatch.setattr(worker, 'DATA_DIR', isolated_task.parent)
    with pytest.raises(ValueError, match='拒绝使用日常专利库'):
        worker.run_worker_assignment(TASK_ID)
    assert not worker.PATENTS_DB_FILE.exists()


def test_busy_desktop_rejects_before_claim_or_creating_batch(isolated_task, monkeypatch):
    coordinator = CoordinatorStub()
    coordinator.claim = Mock(return_value=assignment())
    monkeypatch.setattr(worker, 'CoordinatorConnection', lambda task_id: coordinator)
    with desktop_collection_lock.reserve_detail_collection_desktop('普通采集'):
        with pytest.raises(desktop_collection_lock.DetailCollectionDesktopBusyError):
            worker.run_worker_assignment(TASK_ID)
    coordinator.claim.assert_not_called()
    assert not worker.WORKER_ASSIGNMENT_FILE.exists()
    assert not worker.COLLECTION_BATCHES_DIR.exists()


def test_owned_proxy_and_collector_share_isolated_environment_and_are_cleaned(isolated_task, monkeypatch):
    coordinator = CoordinatorStub()
    batch_id = CollectionBatch.prepare('fees', APPLICATIONS)
    proxy_child = Mock()
    proxy_child.poll.return_value = None
    collector_child = Mock()
    collector_child.poll.side_effect = [None, 0]
    launch_calls = []

    def launch(command, environment, **streams):
        launch_calls.append((command, environment, streams))
        return proxy_child if len(launch_calls) == 1 else collector_child

    monkeypatch.setattr(worker, '_unused_proxy_port', lambda: 43210)
    monkeypatch.setattr(worker, '_launch_owned_child', launch)
    readiness = Mock()
    monkeypatch.setattr(worker, '_await_proxy', readiness)
    stopped_children = []
    monkeypatch.setattr(worker, 'terminate_process_tree', stopped_children.append)
    monkeypatch.setattr(worker.time, 'sleep', lambda duration: None)
    worker._collect_assignment(assignment(), batch_id, coordinator)
    readiness.assert_called_once_with(proxy_child, 43210)
    assert launch_calls[0][1] == launch_calls[1][1]
    assert launch_calls[1][1]['CNIPA_DATA_DIR'] == str(isolated_task)
    assert launch_calls[1][1]['MITM_PORT'] == '43210'
    assert launch_calls[1][1]['MITM_HOST'] == '127.0.0.1'
    assert launch_calls[1][0][-2:] == ['--resume-batch', batch_id]
    assert stopped_children == [collector_child, proxy_child]
    assert coordinator.progress_updates == [{'state': 'collecting', 'completed': 0, 'succeeded': 0, 'failed': 0}]


def test_proxy_start_failure_never_launches_collector(isolated_task, monkeypatch):
    proxy_child = Mock()
    launch = Mock(return_value=proxy_child)
    stopped = Mock()
    monkeypatch.setattr(worker, '_unused_proxy_port', lambda: 43210)
    monkeypatch.setattr(worker, '_launch_owned_child', launch)
    monkeypatch.setattr(worker, '_await_proxy', Mock(side_effect=RuntimeError('proxy failed')))
    monkeypatch.setattr(worker, 'terminate_process_tree', stopped)
    with pytest.raises(RuntimeError, match='proxy failed'):
        worker._collect_assignment(assignment(), 'b' * 32, CoordinatorStub())
    launch.assert_called_once()
    stopped.assert_called_once_with(proxy_child)


@pytest.mark.parametrize('network_exception', [worker.WorkerConnectionError('offline'), worker.WorkerAssignmentRejectedError('cancelled')])
def test_progress_network_failure_continues_but_revocation_stops(isolated_task, monkeypatch, network_exception):
    coordinator = CoordinatorStub()
    coordinator.publish_progress = Mock(side_effect=network_exception)
    batch_id = CollectionBatch.prepare('fees', APPLICATIONS)
    proxy_child = Mock()
    proxy_child.poll.return_value = None
    collector_child = Mock()
    collector_child.poll.side_effect = [None, 0]
    monkeypatch.setattr(worker, '_unused_proxy_port', lambda: 43210)
    monkeypatch.setattr(worker, '_launch_owned_child', Mock(side_effect=[proxy_child, collector_child]))
    monkeypatch.setattr(worker, '_await_proxy', lambda child, port: None)
    stopped_children = []
    monkeypatch.setattr(worker, 'terminate_process_tree', stopped_children.append)
    monkeypatch.setattr(worker.time, 'sleep', lambda duration: None)
    if isinstance(network_exception, worker.WorkerAssignmentRejectedError):
        with pytest.raises(worker.WorkerAssignmentRejectedError):
            worker._collect_assignment(assignment(), batch_id, coordinator)
    else:
        worker._collect_assignment(assignment(), batch_id, coordinator)
    assert stopped_children == [collector_child, proxy_child]


def test_delivery_summary_distinguishes_failed_acknowledgements(isolated_task, monkeypatch, capsys):
    coordinator = CoordinatorStub()
    monkeypatch.setattr(worker, 'CoordinatorConnection', lambda task_id: coordinator)
    monkeypatch.setattr(worker, '_collect_assignment', lambda assigned, batch_id, connection: finish_batch(isolated_task, batch_id, 'fees'))
    assert worker.run_worker_assignment(TASK_ID) == 0
    terminal_text = capsys.readouterr().out
    assert '已交付 2 项；成功 1、失败 1、中断 0、冲突 0' in terminal_text
    assert '未成功项目请在主机检查后重新分单' in terminal_text


def test_revoked_assignment_preserves_rejection_reason_with_outbox(isolated_task, monkeypatch):
    worker.register_worker_assignment('http://localhost:8765', TASK_ID, ACCESS_TOKEN)
    coordinator = CoordinatorStub()
    rejection = worker.WorkerAssignmentRejectedError('主机已取消该分单')
    coordinator.deliver_item = Mock(side_effect=rejection)
    monkeypatch.setattr(worker, 'CoordinatorConnection', lambda task_id: coordinator)

    def revoked_collection(assigned, batch_id, connection):
        finish_batch(isolated_task, batch_id, 'fees')
        raise rejection

    monkeypatch.setattr(worker, '_collect_assignment', revoked_collection)
    with pytest.raises(worker.WorkerAssignmentRejectedError):
        worker.run_worker_assignment(TASK_ID)
    summary = worker.list_worker_assignments()[0]
    assert summary['state'] == 'rejected'
    assert summary['reason'] == '主机已取消该分单'
    assert summary['delivery_pending'] == 2
    assert worker.WORKER_OUTBOX_FILE.exists()


def test_rejected_claim_remains_visible_before_batch_exists(isolated_task, monkeypatch):
    worker.register_worker_assignment('http://localhost:8765', TASK_ID, ACCESS_TOKEN)
    coordinator = CoordinatorStub()
    coordinator.claim = Mock(side_effect=worker.WorkerAssignmentRejectedError('分单已撤销'))
    monkeypatch.setattr(worker, 'CoordinatorConnection', lambda task_id: coordinator)
    with pytest.raises(worker.WorkerAssignmentRejectedError):
        worker.run_worker_assignment(TASK_ID)
    summary = worker.list_worker_assignments()[0]
    assert summary['state'] == 'rejected'
    assert summary['reason'] == '分单已撤销'
    assert summary['total'] == 0
    assert not worker.PATENTS_DB_FILE.exists()


@contextlib.contextmanager
def coordinator_server():
    requests = []

    class CoordinatorHTTP(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def do_GET(self):
            requests.append((self.path, dict(self.headers), None))
            serialized = json.dumps({'assignment': assignment()}).encode()
            self.send_response(200)
            self.end_headers()
            self.wfile.write(serialized)

        def do_POST(self):
            transfer = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            requests.append((self.path, dict(self.headers), transfer))
            receipt = {'protocol_version': 1, 'task_id': TASK_ID, 'application_no': transfer['application_no'], 'status': transfer['status'], 'duplicate': False}
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps({'receipt': receipt}).encode())

    server = ThreadingHTTPServer(('127.0.0.1', 0), CoordinatorHTTP)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}', requests
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=5)


def test_http_protocol_carries_scoped_credentials_and_checks_receipt(isolated_task):
    with coordinator_server() as (origin, requests):
        worker.register_worker_assignment(origin, TASK_ID, ACCESS_TOKEN)
        connection = worker.CoordinatorConnection(TASK_ID)
        assert connection.claim() == assignment()
        transfer = {'protocol_version': 1, 'application_no': APPLICATIONS[0], 'status': 'failed', 'fields': {}, 'reason': 'timeout'}
        assert connection.deliver_item(transfer)['status'] == 'failed'
    assert requests[0][1]['X-Cnipa-Task-Token'] == ACCESS_TOKEN
    assert requests[0][1]['X-Cnipa-Worker-Id'] == worker._worker_identity()
    assert requests[1][2] == transfer
    assert requests[1][0].endswith('/results')


def test_redirect_is_never_followed_with_task_token(isolated_task):
    redirect = worker._RejectCoordinatorRedirect()
    with pytest.raises(worker.WorkerConnectionError, match='重定向'):
        redirect.redirect_request(None, None, 302, '', {}, 'http://other.example/')


def test_invalid_receipt_leaves_delivery_pending(isolated_task, monkeypatch):
    worker.register_worker_assignment('http://localhost:8765', TASK_ID, ACCESS_TOKEN)
    connection = worker.CoordinatorConnection(TASK_ID)
    monkeypatch.setattr(connection, '_exchange', lambda request: {
        'receipt': {'application_no': APPLICATIONS[1], 'status': 'success', 'duplicate': False},
    })
    with pytest.raises(worker.WorkerConnectionError, match='回执不匹配'):
        connection.deliver_item({'application_no': APPLICATIONS[0], 'status': 'success'})


@pytest.mark.parametrize('changed_field, wrong_value', [('task_id', 'b' * 32), ('protocol_version', 2), ('protocol_version', True)])
def test_wrong_task_or_protocol_receipt_is_rejected(isolated_task, monkeypatch, changed_field, wrong_value):
    worker.register_worker_assignment('http://localhost:8765', TASK_ID, ACCESS_TOKEN)
    connection = worker.CoordinatorConnection(TASK_ID)
    receipt = {
        'protocol_version': 1, 'task_id': TASK_ID,
        'application_no': APPLICATIONS[0], 'status': 'success', 'duplicate': False,
        changed_field: wrong_value,
    }
    monkeypatch.setattr(connection, '_exchange', lambda request: {'receipt': receipt})
    with pytest.raises(worker.WorkerConnectionError, match='回执不匹配'):
        connection.deliver_item({'application_no': APPLICATIONS[0], 'status': 'success'})


def test_settings_isolate_business_paths_but_share_desktop_resources(tmp_path):
    # This subprocess runs in the test suite's source-only copy, never production.
    source_directory = Path(__file__).resolve().parents[1]
    task_directory = tmp_path / 'task'
    script = (
        'import json, settings; print(json.dumps({name:str(getattr(settings,name)) for name in '
        '["LOCAL_DATA_DIR","DATA_DIR","PATENTS_DB_FILE","PATENT_DETAIL_SEARCH_CACHE_FILE",'
        '"CONFIG_FILE","CONFIG_FWXX_FILE","DETAIL_COLLECTION_LOCK_FILE","LOGIN_READY_FLAG_FILE"]}))'
    )
    completed = subprocess.run(
        [sys.executable, '-c', script], cwd=source_directory,
        env={**os.environ, 'CNIPA_DATA_DIR': str(task_directory)}, capture_output=True, text=True, check=True,
    )
    paths = json.loads(completed.stdout)
    assert Path(paths['PATENTS_DB_FILE']).parent == task_directory
    assert Path(paths['PATENT_DETAIL_SEARCH_CACHE_FILE']).parent == task_directory
    assert paths['DATA_DIR'] != paths['LOCAL_DATA_DIR']
    for name in ('CONFIG_FILE', 'CONFIG_FWXX_FILE', 'DETAIL_COLLECTION_LOCK_FILE', 'LOGIN_READY_FLAG_FILE'):
        assert Path(paths[name]).parent == source_directory / 'data'
    rejected = subprocess.run(
        [sys.executable, '-c', 'import settings'], cwd=source_directory,
        env={**os.environ, 'CNIPA_DATA_DIR': 'relative/task'}, capture_output=True, text=True,
    )
    assert rejected.returncode != 0
    assert '必须为绝对路径' in rejected.stderr


def test_cli_reexecs_into_task_data_without_exposing_token(isolated_task, monkeypatch):
    monkeypatch.setattr(worker, 'DATA_DIR', isolated_task.parent)
    reexec = Mock(side_effect=RuntimeError('test replacement'))
    monkeypatch.setattr(worker.os, 'execve', reexec)
    captured = io.StringIO()
    with contextlib.redirect_stdout(captured):
        assert worker.main(['deliver', '--task-id', TASK_ID]) == 1
    executable, command, environment = reexec.call_args.args
    assert executable == sys.executable
    assert command[-3:] == ['deliver', '--task-id', TASK_ID]
    assert environment['CNIPA_DATA_DIR'] == str(isolated_task)
    assert ACCESS_TOKEN not in captured.getvalue()
