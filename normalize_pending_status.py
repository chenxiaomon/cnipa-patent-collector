#!/usr/bin/env python3
"""Preview or apply the NULL status_code to explicit pending-status migration."""

from __future__ import annotations

import sys

from db_manager import PENDING_STATUS_CODE, PatentsDB
from machine_identity import MachineRoleConfigurationError, require_master_data_repair_role
from settings import PATENTS_DB_FILE


def main() -> None:
    db = PatentsDB(PATENTS_DB_FILE)
    pending_count = db.count_unattempted_records()
    print(f'[preview] status_code IS NULL: {pending_count} 条')
    print(f'[semantic] 这些记录定义为“已入库但从未成功采集”，状态码设为 {PENDING_STATUS_CODE}。')
    if '--apply' not in sys.argv[1:]:
        print('仅预览；在 master 上确认后运行: python normalize_pending_status.py --apply')
        return
    try:
        require_master_data_repair_role()
    except MachineRoleConfigurationError as exc:
        print(f'[✗] {exc}')
        raise SystemExit(2)
    updated = db.mark_unattempted_records_pending()
    print(f'[✓] 已将 {updated} 条 NULL 记录迁移为 status_code={PENDING_STATUS_CODE}。')


if __name__ == '__main__':
    main()
