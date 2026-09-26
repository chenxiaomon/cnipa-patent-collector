"""Two independent workstations deliver through the real HTTP and DB boundaries."""

import json
import tempfile
import threading
import unittest
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import distributed_worker
import web_dashboard
from db_manager import PatentsDB


class DistributionEndToEndTests(unittest.TestCase):
    def setUp(self):
        self.resources = ExitStack()
        self.addCleanup(self.resources.close)
        self.workspace = Path(self.resources.enter_context(tempfile.TemporaryDirectory()))
        self.master = PatentsDB(self.workspace / 'master.db')
        self.resources.enter_context(patch.object(web_dashboard, '_patents_db', self.master))
        self.resources.enter_context(patch.object(web_dashboard, 'read_machine_role', return_value='master'))
        self.resources.enter_context(patch.object(
            web_dashboard, 'api_token_matches', side_effect=lambda token: token == 'local-test-admin',
        ))
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), web_dashboard.DashboardHandler)
        self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.server_thread.start()
        self.addCleanup(self.stop_server)
        self.coordinator_url = f'http://127.0.0.1:{self.server.server_port}'
        self.numbers = ['2026100000010', '2026100000029', '2026100000038', '2026100000047']
        for application_no in self.numbers:
            self.master.upsert({
                'application_no': application_no, 'status_code': 200,
                'zhuanlimc': 'Synthetic patent', 'shenqingrxm': 'Test applicant',
                'anjianywzt': 'original status', 'timestamp': '2026-01-01T00:00:00Z',
                'fwxx_list': [{'name': 'existing notice'}],
            })

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.server_thread.join(timeout=5)

    def create_assignments(self):
        request = urllib.request.Request(
            self.coordinator_url + '/api/distribution/assignments',
            data=json.dumps({
                'collector': 'fees', 'application_nos': self.numbers,
                'device_names': ['First workstation', 'Second workstation'],
            }).encode(),
            headers={'Content-Type': 'application/json', 'X-CNIPA-Token': 'local-test-admin'},
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            self.assertEqual(response.status, 201)
            return json.load(response)['assignments']

    def connect_workstation(self, invitation, directory_name):
        workstation = self.workspace / directory_name
        with patch.multiple(
            distributed_worker,
            WORKER_TASKS_DIR=workstation / 'worker_tasks',
            WORKER_ID_FILE=workstation / 'worker_id.json',
            WORKER_REGISTRY_LOCK_FILE=workstation / 'worker_registry.lock',
        ):
            distributed_worker.register_worker_assignment(
                self.coordinator_url, invitation['task_id'], invitation['access_token'],
            )
            connection = distributed_worker.CoordinatorConnection(invitation['task_id'])
        claimed_assignment = connection.claim()
        self.assertNotIn('access_token', claimed_assignment)
        self.assertEqual(claimed_assignment['application_nos'], invitation['application_nos'])
        return connection

    @staticmethod
    def fee_transfer(application_no):
        return {
            'protocol_version': 1, 'application_no': application_no, 'status': 'success',
            'reason': '', 'fields': {
                'payable_fee_records': [], 'late_fee_schedule_records': [],
                'paid_fee_records': [{'amount': '100'}], 'fee_receipt_dispatch_records': [],
                'fee_snapshot_at': '2026-09-26T10:00:00Z',
            },
        }

    def test_two_workstations_complete_disjoint_tasks_and_retry_lost_ack(self):
        invitations = self.create_assignments()
        self.assertFalse(set(invitations[0]['application_nos']) & set(invitations[1]['application_nos']))
        connections = [
            self.connect_workstation(invitation, f'worker-{index}')
            for index, invitation in enumerate(invitations)
        ]
        uploads = [
            (connection, self.fee_transfer(application_no))
            for connection, invitation in zip(connections, invitations)
            for application_no in invitation['application_nos']
        ]
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(connection.deliver_item, transfer) for connection, transfer in uploads]
            for future in futures:
                receipt = future.result(timeout=10)
                self.assertEqual(receipt['status'], 'success')
                self.assertFalse(receipt['duplicate'])

        first_connection, first_transfer = uploads[0]
        stored_before_retry = self.master.get_record(first_transfer['application_no'])
        repeated_receipt = first_connection.deliver_item(first_transfer)
        self.assertTrue(repeated_receipt['duplicate'])
        self.assertEqual(self.master.get_record(first_transfer['application_no']), stored_before_retry)

        for application_no in self.numbers:
            saved_patent = self.master.get_record(application_no)
            self.assertEqual(saved_patent['anjianywzt'], 'original status')
            self.assertEqual(saved_patent['timestamp'], '2026-01-01T00:00:00Z')
            self.assertEqual(saved_patent['fwxx_list'], [{'name': 'existing notice'}])
            self.assertEqual(saved_patent['paid_fee_records'], [{'amount': '100'}])
        request = urllib.request.Request(
            self.coordinator_url + '/api/distribution', headers={'X-CNIPA-Token': 'local-test-admin'},
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            summaries = json.load(response)['assignments']
        self.assertEqual(sum(assignment['counts']['success'] for assignment in summaries), 4)
        self.assertTrue(all(assignment['state'] == 'completed' for assignment in summaries))
        self.assertTrue(all('access_token' not in assignment for assignment in summaries))

    def test_master_fee_change_conflicts_but_status_change_does_not(self):
        invitations = self.create_assignments()
        connection = self.connect_workstation(invitations[0], 'worker-first')
        same_domain, other_domain = invitations[0]['application_nos']
        self.master.update_fee_snapshot(same_domain, {
            'payable_fee_records': [{'amount': '250'}], 'paid_fee_records': [],
            'fee_receipt_dispatch_records': [], 'fee_snapshot_at': '2026-09-26T11:00:00Z',
        })
        self.master.update_fields(other_domain, {'anjianywzt': 'new status'})

        conflict_receipt = connection.deliver_item(self.fee_transfer(same_domain))
        accepted_receipt = connection.deliver_item(self.fee_transfer(other_domain))

        self.assertEqual(conflict_receipt['status'], 'conflict')
        self.assertEqual(accepted_receipt['status'], 'success')
        self.assertEqual(self.master.get_record(same_domain)['payable_fee_records'], [{'amount': '250'}])
        self.assertEqual(self.master.get_record(other_domain)['anjianywzt'], 'new status')
        self.assertEqual(self.master.get_record(other_domain)['paid_fee_records'], [{'amount': '100'}])


if __name__ == '__main__':
    unittest.main()
