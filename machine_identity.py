#!/usr/bin/env python3
"""Machine identity and destructive-operation guardrails."""

from __future__ import annotations

import sys
from collections.abc import Sequence

from settings import MACHINE_ROLE_FILE

MASTER_ROLE = 'master'
REPLICA_ROLE = 'replica'
UNCONFIGURED_ROLE = 'unconfigured'
_VALID_ROLES = {MASTER_ROLE, REPLICA_ROLE}
_MASTER_REBUILD_ACKNOWLEDGEMENT = {'--force', '--i-know-this-is-master'}


class MachineRoleConfigurationError(RuntimeError):
    """Raised when a machine role is missing, invalid, or unsafe for an operation."""


def read_machine_role() -> str:
    """Read and validate this machine's locally configured role."""
    if not MACHINE_ROLE_FILE.exists():
        return UNCONFIGURED_ROLE
    role = MACHINE_ROLE_FILE.read_text(encoding='utf-8').strip().lower()
    if role not in _VALID_ROLES:
        raise MachineRoleConfigurationError(
            f"机器角色无效: {role!r}。请在 {MACHINE_ROLE_FILE} 中写入 master 或 replica。"
        )
    return role


def require_database_rebuild_authorization(command_line_arguments: Sequence[str]) -> str:
    """Reject destructive database rebuilds unless the machine role permits them."""
    role = read_machine_role()
    if role == UNCONFIGURED_ROLE:
        raise MachineRoleConfigurationError(
            f"未配置机器角色，拒绝重建数据库。请先在 {MACHINE_ROLE_FILE} 中写入 master 或 replica。"
        )
    acknowledgements = set(command_line_arguments)
    if role == MASTER_ROLE and not _MASTER_REBUILD_ACKNOWLEDGEMENT.issubset(acknowledgements):
        raise MachineRoleConfigurationError(
            "当前机器是 master，已拒绝覆盖/重建数据库。若确认承担数据丢失风险，必须同时添加 "
            "--force --i-know-this-is-master。"
        )
    return role


def master_merge_confirmation_required() -> bool:
    return read_machine_role() == MASTER_ROLE


def require_replica_pull_role() -> None:
    role = read_machine_role()
    if role != REPLICA_ROLE:
        raise MachineRoleConfigurationError(
            f"从 master 拉取增量只能在 replica 上执行；当前角色为 {role}。"
        )


def require_replica_git_upgrade_role() -> None:
    role = read_machine_role()
    if role != REPLICA_ROLE:
        raise MachineRoleConfigurationError(
            f"git pull 更新可能触碰跟踪数据，只允许 replica 使用；当前角色为 {role}。"
        )


def require_master_data_repair_role() -> None:
    role = read_machine_role()
    if role != MASTER_ROLE:
        raise MachineRoleConfigurationError(
            f"生产数据语义迁移只能在 master 上执行；当前角色为 {role}。"
        )


def confirm_master_merge(import_summary: dict) -> None:
    """Require an interactive confirmation before merging records on the master."""
    if not master_merge_confirmation_required():
        return
    print("\n[MASTER] 即将合并外部记录")
    print(f"  输入记录: {import_summary['records']} 条")
    print(f"  新申请号: {import_summary['new_applications']} 条")
    print(f"  更新已有: {import_summary['updated_applications']} 条")
    print(f"  时间范围: {import_summary['timestamp_from'] or '无'} → {import_summary['timestamp_to'] or '无'}")
    if not sys.stdin.isatty():
        raise MachineRoleConfigurationError("master 增量合并必须在交互终端确认，非交互执行已拒绝。")
    input("确认摘要无误后按 Enter 合并；按 Ctrl+C 取消: ")
