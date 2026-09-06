#!/usr/bin/env python3
"""Roll back the code directory to its most recent verified backup."""

from code_release_safety import (
    CodeReleaseVerificationError,
    latest_code_backup,
    restore_code_backup,
)
from desktop_collection_lock import (
    DetailCollectionDesktopBusyError,
    reserve_code_maintenance,
)


def main() -> None:
    try:
        with reserve_code_maintenance('代码回滚'):
            backup_path = latest_code_backup()
            restored = restore_code_backup(backup_path)
            print(f'[✓] 已从 {backup_path} 恢复 {restored} 个代码文件。')
    except (CodeReleaseVerificationError, DetailCollectionDesktopBusyError, OSError) as exc:
        print(f'[✗] 回滚失败: {exc}')
        raise SystemExit(1)


if __name__ == '__main__':
    main()
