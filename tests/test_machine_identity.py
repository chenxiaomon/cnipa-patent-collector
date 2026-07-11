#!/usr/bin/env python3

import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import machine_identity


class TestMachineIdentity(unittest.TestCase):
    def test_replica_allows_database_rebuild(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            role_file = Path(tmpdir) / 'machine_role.txt'
            role_file.write_text('replica\n', encoding='utf-8')
            with patch.object(machine_identity, 'MACHINE_ROLE_FILE', role_file):
                self.assertEqual(
                    machine_identity.require_database_rebuild_authorization([]),
                    machine_identity.REPLICA_ROLE,
                )

    def test_master_rejects_database_rebuild_without_both_acknowledgements(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            role_file = Path(tmpdir) / 'machine_role.txt'
            role_file.write_text('master\n', encoding='utf-8')
            with patch.object(machine_identity, 'MACHINE_ROLE_FILE', role_file):
                with self.assertRaises(machine_identity.MachineRoleConfigurationError):
                    machine_identity.require_database_rebuild_authorization(['--force'])

    def test_master_allows_database_rebuild_with_both_acknowledgements(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            role_file = Path(tmpdir) / 'machine_role.txt'
            role_file.write_text('master\n', encoding='utf-8')
            with patch.object(machine_identity, 'MACHINE_ROLE_FILE', role_file):
                self.assertEqual(
                    machine_identity.require_database_rebuild_authorization(
                        ['--force', '--i-know-this-is-master']
                    ),
                    machine_identity.MASTER_ROLE,
                )

    def test_master_merge_refuses_noninteractive_execution(self):
        summary = {
            'records': 2,
            'new_applications': 1,
            'updated_applications': 1,
            'timestamp_from': '2026-01-01T00:00:00Z',
            'timestamp_to': '2026-01-02T00:00:00Z',
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            role_file = Path(tmpdir) / 'machine_role.txt'
            role_file.write_text('master\n', encoding='utf-8')
            with patch.object(machine_identity, 'MACHINE_ROLE_FILE', role_file), patch.object(
                machine_identity.sys, 'stdin', io.StringIO()
            ):
                with self.assertRaises(machine_identity.MachineRoleConfigurationError):
                    machine_identity.confirm_master_merge(summary)
