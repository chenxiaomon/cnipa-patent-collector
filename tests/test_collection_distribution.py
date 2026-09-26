"""Distributed results must preserve domain ownership and transactional receipts."""

import hashlib
import importlib.util
import os
import sqlite3
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from collection_distribution import (
    DistributionAuthorizationError,
    DistributionConflictError,
    DistributionValidationError,
)
from db_manager import PatentsDB

FIRST = '2018108715138'
SECOND = '202310411762X'
THIRD = '2026102909420'
WORKER = 'a' * 32
OTHER_WORKER = 'b' * 32
STAMP = '2099-01-01T00:00:00Z'


@pytest.fixture
def database():
    with tempfile.TemporaryDirectory(prefix='cnipa-distribution-db-') as directory:
        yield PatentsDB(Path(directory) / 'patents.db')


def seed(database, application_no=FIRST):
    database.upsert({
        'application_no': application_no, 'status_code': 200,
        'zhuanlimc': 'old title', 'shenqingrxm': 'old applicant',
        'anjianywzt': 'old status', 'timestamp': '2026-01-01T00:00:00Z',
        'fwxx_list': [{'name': 'old notice'}], 'fwxx_collected_at': '2026-01-01T00:00:00Z',
        'payable_fee_records': [{'amount': 'old payable'}],
        'paid_fee_records': [], 'fee_receipt_dispatch_records': [],
        'late_fee_schedule_records': [{'amount': 'old late'}],
        'fee_snapshot_at': '2026-01-01T00:00:00Z',
        'daili_jg': 'original agency',
    })


def claim(database, collector='fees', application_nos=None):
    assignment = database.create_collection_assignments(collector, application_nos or [FIRST], ['B'])[0]
    database.claim_collection_assignment(assignment['task_id'], assignment['access_token'], WORKER)
    return assignment


def transfer(collector='fees', application_no=FIRST, status='success', fields=None):
    if fields is None:
        fields = {
            'main': {'status_code': 200, 'zhuanlimc': 'new title', 'shenqingrxm': 'new applicant', 'anjianywzt': 'new status'},
            'fwxx': {'fwxx_list': [], 'fwxx_collected_at': STAMP},
            'fees': {'payable_fee_records': [], 'paid_fee_records': [], 'fee_receipt_dispatch_records': [], 'fee_snapshot_at': STAMP},
        }[collector] if status == 'success' else {}
    return {'protocol_version': 1, 'application_no': application_no, 'status': status, 'fields': fields, 'reason': ''}


def accept(database, assignment, payload):
    return database.accept_collection_transfer(assignment['task_id'], assignment['access_token'], WORKER, payload)


def test_balanced_assignments_normalize_and_hide_credentials(database):
    assignments = database.create_collection_assignments('main', [FIRST, 'CN202310411762.X', THIRD, FIRST], ['A', 'B'])
    assert [assignment['application_nos'] for assignment in assignments] == [[FIRST, THIRD], [SECOND]]
    assert all('access_token' not in summary for summary in database.list_collection_assignments())
    invitation = database.get_collection_assignment_invitation(assignments[0]['task_id'])
    assert invitation['access_token'] == assignments[0]['access_token']
    assert invitation['counts']['total'] == 2


def test_concurrent_assignments_have_one_reservation(database):
    def reserve():
        contender = PatentsDB(database._db_path)
        try:
            return contender.create_collection_assignments('main', [FIRST], ['B'])
        except DistributionConflictError:
            return None
    with ThreadPoolExecutor(max_workers=2) as executor:
        attempts = list(executor.map(lambda _: reserve(), range(2)))
    assert sum(attempt is not None for attempt in attempts) == 1
    assert len(database.list_collection_assignments()) == 1


@pytest.mark.parametrize('collector', ['fwxx', 'fees'])
def test_details_need_existing_patent(database, collector):
    with pytest.raises(DistributionValidationError):
        database.create_collection_assignments(collector, [FIRST], ['B'])
    assert database.list_collection_assignments() == []


@pytest.mark.parametrize('collector', ['main', 'fwxx', 'fees'])
def test_success_writes_only_its_domain_and_uses_host_time(database, collector):
    seed(database)
    before = database.get_record(FIRST)
    assignment = claim(database, collector)
    receipt = accept(database, assignment, transfer(collector))
    stored = database.get_record(FIRST)
    assert receipt['status'] == 'success'
    assert stored['daili_jg'] == before['daili_jg']
    if collector != 'main':
        assert stored['zhuanlimc'] == before['zhuanlimc']
        assert stored['timestamp'] == before['timestamp']
    if collector != 'fwxx':
        assert stored['fwxx_list'] == before['fwxx_list']
    if collector != 'fees':
        assert stored['payable_fee_records'] == before['payable_fee_records']
    version_field = {'main': 'timestamp', 'fwxx': 'fwxx_collected_at', 'fees': 'fee_snapshot_at'}[collector]
    assert stored[version_field] == receipt['received_at']
    assert stored[version_field] != STAMP
    assert database.list_collection_assignments()[0]['state'] == 'completed'


def test_empty_fee_lists_are_valid_and_replace_optional_section(database):
    seed(database)
    assignment = claim(database)
    assert accept(database, assignment, transfer())['status'] == 'success'
    stored = database.get_record(FIRST)
    assert stored['payable_fee_records'] == []
    assert stored['late_fee_schedule_records'] is None


@pytest.mark.parametrize('field', ['payable_fee_records', 'paid_fee_records', 'fee_receipt_dispatch_records', 'fee_snapshot_at'])
@pytest.mark.parametrize('mutation', ['missing', 'null'])
def test_incomplete_fees_cannot_be_acknowledged(database, field, mutation):
    seed(database)
    assignment = claim(database)
    payload = transfer()
    if mutation == 'missing':
        payload['fields'].pop(field)
    else:
        payload['fields'][field] = None
    with pytest.raises(DistributionValidationError):
        accept(database, assignment, payload)
    assert database.list_collection_assignments()[0]['counts']['pending'] == 1
    assert database.get_record(FIRST)['payable_fee_records'] == [{'amount': 'old payable'}]


def test_cas_rejects_same_domain_changes_and_preserves_local_write(database):
    seed(database)
    assignment = claim(database)
    database.update_fields(FIRST, {'paid_fee_records': [{'new': 'local'}]})
    receipt = accept(database, assignment, transfer())
    assert receipt['status'] == 'conflict'
    assert database.get_record(FIRST)['paid_fee_records'] == [{'new': 'local'}]
    assert accept(database, assignment, transfer())['duplicate'] is True
    assert len(database.create_collection_assignments('fees', [FIRST], ['B'])) == 1


def test_other_domain_changes_do_not_conflict(database):
    seed(database)
    assignment = claim(database, 'fees')
    database.update_fields(FIRST, {'fwxx_list': [{'local': 'notice'}]})
    assert accept(database, assignment, transfer())['status'] == 'success'
    assert database.get_record(FIRST)['fwxx_list'] == [{'local': 'notice'}]


def test_duplicate_receipt_survives_reopen_and_does_not_rewrite(database):
    seed(database)
    assignment = claim(database)
    first_receipt = accept(database, assignment, transfer())
    reopened = PatentsDB(database._db_path)
    duplicate = accept(reopened, assignment, transfer())
    assert duplicate == {**first_receipt, 'duplicate': True}
    assert reopened.get_record(FIRST)['fee_snapshot_at'] == first_receipt['received_at']
    changed = transfer()
    changed['fields']['paid_fee_records'] = [{'different': True}]
    with pytest.raises(DistributionConflictError):
        accept(reopened, assignment, changed)


def test_finished_item_keeps_reservation_until_whole_assignment_finishes(database):
    assignment = claim(database, 'main', [FIRST, SECOND])
    accept(database, assignment, transfer('main'))
    with pytest.raises(DistributionConflictError):
        database.create_collection_assignments('main', [FIRST], ['C'])
    accept(database, assignment, transfer('main', SECOND, status='failed'))
    assert database.create_collection_assignments('main', [FIRST], ['C'])


def test_receipt_failure_rolls_back_business_write(database):
    seed(database)
    assignment = claim(database)
    with database._connect() as connection:
        connection.execute('''CREATE TRIGGER reject_receipt BEFORE UPDATE OF receipt_json
                              ON collection_assignment_items BEGIN SELECT RAISE(ABORT, 'receipt failed'); END''')
        connection.commit()
    with pytest.raises(sqlite3.IntegrityError, match='receipt failed'):
        accept(database, assignment, transfer())
    assert database.get_record(FIRST)['payable_fee_records'] == [{'amount': 'old payable'}]
    assert database.list_collection_assignments()[0]['counts']['pending'] == 1


def test_wrong_token_worker_and_unassigned_patent_are_rejected(database):
    seed(database)
    assignment = claim(database)
    with pytest.raises(DistributionAuthorizationError):
        database.claim_collection_assignment(assignment['task_id'], 'z' * 32, WORKER)
    with pytest.raises(DistributionAuthorizationError):
        database.claim_collection_assignment(assignment['task_id'], assignment['access_token'], OTHER_WORKER)
    with pytest.raises(DistributionAuthorizationError):
        accept(database, assignment, transfer(application_no=SECOND))
    assert database.get_record(FIRST)['payable_fee_records'] == [{'amount': 'old payable'}]


def test_cancel_releases_pending_targets_and_rejects_late_upload(database):
    assignment = claim(database, 'main')
    cancelled = database.cancel_collection_assignment(assignment['task_id'])
    assert cancelled['counts']['cancelled'] == 1
    with pytest.raises(DistributionConflictError):
        accept(database, assignment, transfer('main'))
    assert database.get_record(FIRST) is None
    assert database.create_collection_assignments('main', [FIRST], ['C'])


@pytest.mark.parametrize('status', ['failed', 'interrupted'])
def test_unsuccessful_items_do_not_touch_business_fields(database, status):
    seed(database)
    before = database.get_record(FIRST)
    assignment = claim(database)
    assert accept(database, assignment, transfer(status=status))['status'] == status
    assert database.get_record(FIRST) == before


def test_transfer_rejects_cross_domain_fields_and_protocol_changes(database):
    seed(database)
    assignment = claim(database)
    payload = transfer()
    payload['fields']['fwxx_list'] = []
    with pytest.raises(DistributionValidationError):
        accept(database, assignment, payload)
    payload = transfer()
    payload['protocol_version'] = True
    with pytest.raises(DistributionValidationError):
        accept(database, assignment, payload)


def test_progress_cannot_fabricate_completed_receipts(database):
    assignment = claim(database, 'main')
    summary = database.record_collection_worker_progress(
        assignment['task_id'], assignment['access_token'], WORKER,
        {'state': 'delivering', 'completed': 1, 'succeeded': 1, 'failed': 0},
    )
    assert summary['progress']['succeeded'] == 1
    assert summary['counts']['pending'] == 1
    assert summary['state'] == 'running'
    assert summary['last_seen'] is not None
    with pytest.raises(DistributionValidationError):
        database.record_collection_worker_progress(
            assignment['task_id'], assignment['access_token'], WORKER,
            {'state': 'delivering', 'completed': 2, 'succeeded': 2, 'failed': 0},
        )


def test_main_can_create_without_detail_or_agency_fields(database):
    assignment = claim(database, 'main')
    assert accept(database, assignment, transfer('main'))['status'] == 'success'
    stored = database.get_record(FIRST)
    assert stored['zhuanlimc'] == 'new title'
    assert stored['fwxx_list'] is None
    assert stored['paid_fee_records'] is None
    assert stored['daili_jg'] is None


@pytest.mark.parametrize('collector,fields', [
    ('main', {'status_code': 200, 'zhuanlimc': 'title'}),
    ('main', {'status_code': 400, 'zhuanlimc': 'title', 'shenqingrxm': 'applicant'}),
    ('fwxx', {'fwxx_list': [], 'fwxx_collected_at': None}),
    ('fwxx', {'fwxx_list': None, 'fwxx_collected_at': STAMP}),
])
def test_success_requires_complete_domain_payload(database, collector, fields):
    seed(database)
    assignment = claim(database, collector)
    with pytest.raises(DistributionValidationError):
        accept(database, assignment, transfer(collector, fields=fields))
    assert database.list_collection_assignments()[0]['counts']['pending'] == 1


def test_json_key_order_does_not_change_domain_fingerprint_or_retry(database):
    seed(database)
    database.update_fields(FIRST, {'paid_fee_records': [{'a': 1, 'b': 2}]})
    assignment = claim(database)
    database.update_fields(FIRST, {'paid_fee_records': [{'b': 2, 'a': 1}]})
    payload = transfer()
    payload['fields']['paid_fee_records'] = [{'a': 1, 'b': 2}]
    assert accept(database, assignment, payload)['status'] == 'success'
    payload['fields']['paid_fee_records'] = [{'b': 2, 'a': 1}]
    assert accept(database, assignment, payload)['duplicate'] is True


def test_incomplete_detail_assignment_rolls_back_all_devices(database):
    seed(database)
    with pytest.raises(DistributionValidationError):
        database.create_collection_assignments('fees', [FIRST, SECOND], ['A', 'B'])
    assert database.list_collection_assignments() == []


def test_competing_workers_cannot_share_task(database):
    assignment = database.create_collection_assignments('main', [FIRST], ['B'])[0]
    def claim_with(worker):
        contender = PatentsDB(database._db_path)
        try:
            return contender.claim_collection_assignment(assignment['task_id'], assignment['access_token'], worker)
        except DistributionAuthorizationError:
            return None
    with ThreadPoolExecutor(max_workers=2) as executor:
        claims = list(executor.map(claim_with, [WORKER, OTHER_WORKER]))
    assert sum(claimed is not None for claimed in claims) == 1


def test_legacy_reader_can_read_patents_after_new_tables(database):
    seed(database)
    assignment = claim(database)
    accept(database, assignment, transfer())
    with sqlite3.connect(database._db_path) as connection:
        assert connection.execute('SELECT application_no,paid_fee_records FROM patents').fetchone() == (FIRST, '[]')


def test_transfer_must_be_claimed_before_receipt(database):
    assignment = database.create_collection_assignments('main', [FIRST], ['B'])[0]
    with pytest.raises(DistributionConflictError):
        accept(database, assignment, transfer('main'))
    assert database.get_record(FIRST) is None


@pytest.mark.parametrize('status', ['failed', 'interrupted'])
def test_fee_failure_enters_existing_retry_list_once_and_success_clears(database, status):
    seed(database)
    assignment = claim(database)
    payload = transfer(status=status)
    payload['reason'] = 'worker could not load detail'
    accept(database, assignment, payload)
    accept(database, assignment, payload)
    with database._connect() as connection:
        failure = connection.execute(
            "SELECT * FROM collection_failures WHERE collection_kind='fees' AND application_no=?", (FIRST,),
        ).fetchone()
    assert failure['attempt_count'] == 1
    assert failure['reason'] == payload['reason']
    retry_assignment = claim(database)
    accept(database, retry_assignment, transfer())
    with database._connect() as connection:
        assert connection.execute('SELECT COUNT(*) FROM collection_failures').fetchone()[0] == 0


@pytest.mark.parametrize('disposition', ['conflict', 'cancelled'])
def test_conflict_and_cancel_do_not_enter_ordinary_fee_failures(database, disposition):
    seed(database)
    assignment = claim(database)
    if disposition == 'conflict':
        database.update_fields(FIRST, {'paid_fee_records': [{'source': 'local'}]})
        assert accept(database, assignment, transfer())['status'] == 'conflict'
    else:
        database.cancel_collection_assignment(assignment['task_id'])
    with database._connect() as connection:
        assert connection.execute('SELECT COUNT(*) FROM collection_failures').fetchone()[0] == 0



def test_main_transfer_preserves_prepared_history_and_all_other_domains(database):
    seed(database)
    database.update_fields(FIRST, {
        'previous_status': 'the original prepared baseline',
        'bhsjtzs_xiazaisj': '2026-01-01',
        'bhsjtzs_data': {'notice': 'rejection'},
        'daili_r': 'original agent',
    })
    original_record = database.get_record(FIRST)
    preserved_fields = (
        'previous_status', 'fwxx_list', 'bhsjtzs_xiazaisj', 'bhsjtzs_data',
        'fwxx_collected_at', 'payable_fee_records', 'late_fee_schedule_records',
        'paid_fee_records', 'fee_receipt_dispatch_records', 'fee_snapshot_at',
        'daili_jg', 'daili_r',
    )
    for next_status in ('first collected status', 'second collected status'):
        assignment = claim(database, 'main')
        payload = transfer('main')
        payload['fields']['anjianywzt'] = next_status
        assert accept(database, assignment, payload)['status'] == 'success'
        stored = database.get_record(FIRST)
        assert stored['anjianywzt'] == next_status
        assert {field: stored[field] for field in preserved_fields} == {
            field: original_record[field] for field in preserved_fields
        }


def test_stable_f67e1f2_patentsdb_reads_database_after_distributed_writes(database):
    # Export with git show f67e1f2:db_manager.py and provide this path when auditing rollback.
    baseline_module_path = os.environ.get('CNIPA_COMPAT_DB_SOURCE')
    if not baseline_module_path:
        pytest.skip('rollback audit requires the exact stable db_manager.py via CNIPA_COMPAT_DB_SOURCE')
    baseline_source = Path(baseline_module_path).read_bytes()
    assert hashlib.sha256(baseline_source).hexdigest() == '66bb643c0f30b46b3fa5f83ca0206ed2f9012aa54c22540489aea9c557587456'
    module_spec = importlib.util.spec_from_file_location('cnipa_stable_f67e1f2_db', baseline_module_path)
    baseline_module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(baseline_module)
    seed(database)
    database.update_fields(FIRST, {'previous_status': 'prepared baseline'})
    for collector in ('main', 'fwxx', 'fees'):
        assignment = claim(database, collector)
        assert accept(database, assignment, transfer(collector))['status'] == 'success'
    assigned_unknown = claim(database, 'main', [SECOND])
    assert accept(database, assigned_unknown, transfer('main', SECOND))['status'] == 'success'
    current_records = [database.get_record(number) for number in (FIRST, SECOND)]
    stable_database = baseline_module.PatentsDB(database._db_path)
    assert [stable_database.get_record(number) for number in (FIRST, SECOND)] == current_records
    assert stable_database.count() == 2
    assert stable_database.get_record(FIRST)['previous_status'] == 'prepared baseline'
    assert stable_database.get_record(SECOND)['previous_status'] is None
    reopened = PatentsDB(database._db_path)
    assert len(reopened.list_collection_assignments()) == 4
    assert all(assignment['state'] == 'completed' for assignment in reopened.list_collection_assignments())
