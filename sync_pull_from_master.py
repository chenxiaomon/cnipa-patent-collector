#!/usr/bin/env python3
"""Pull patent deltas from the master Dashboard into this replica."""

from __future__ import annotations

import argparse
import errno
import http.client
import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from datetime import datetime, timezone
from cache_utils import normalize_app_no
from db_manager import SYNC_CURSOR_FIELD, PatentsDB, normalize_sync_cursor
from machine_identity import MachineRoleConfigurationError, require_replica_pull_role
from settings import (
    BASE_DIR,
    DETECTION_LOG_JSONL_FILE,
    MASTER_SYNC_CONFIG_FILE,
    MASTER_SYNC_LOCK_FILE,
    MASTER_SYNC_STATE_FILE,
    PATENTS_DB_FILE,
)
from update_readme_stats import update_readme_statistics

INITIAL_SYNC_TIMESTAMP = '1970-01-01T00:00:00Z'
_MASTER_SNAPSHOT_IMPORT_VERSION = 2


class MasterSyncConfigurationError(RuntimeError):
    """Raised when the replica cannot safely identify its master endpoint."""


class MasterSyncBusyError(RuntimeError):
    """Another process is already updating this replica from its master."""


@contextmanager
def reserve_master_sync():
    """Serialize cursor reads, replica writes and cursor commits across processes."""
    MASTER_SYNC_LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    # Keep one stable file; deleting it on release would allow locking different inodes.
    with MASTER_SYNC_LOCK_FILE.open('a+b') as lock_stream:
        lock_stream.seek(0, os.SEEK_END)
        if lock_stream.tell() == 0:
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
        except OSError as exc:
            if exc.errno not in {errno.EACCES, errno.EAGAIN, errno.EDEADLK} and getattr(exc, 'winerror', None) not in {33, 36}:
                raise
            raise MasterSyncBusyError('已有从 master 同步的任务正在运行，请等待该任务结束。') from exc
        yield


def load_master_url() -> str:
    configured_url = os.getenv('CNIPA_MASTER_URL', '').strip()
    if not configured_url and MASTER_SYNC_CONFIG_FILE.exists():
        payload = json.loads(MASTER_SYNC_CONFIG_FILE.read_text(encoding='utf-8'))
        configured_url = str(payload.get('master_url', '')).strip()
    parsed = urllib.parse.urlparse(configured_url)
    if parsed.scheme not in {'http', 'https'} or not parsed.netloc:
        raise MasterSyncConfigurationError(
            f"请设置 CNIPA_MASTER_URL，或在 {MASTER_SYNC_CONFIG_FILE} 写入 "
            '{"master_url":"http://部署机:8765"}。'
        )
    return configured_url.rstrip('/')


def load_sync_cursor(master_url: str) -> str:
    if not MASTER_SYNC_STATE_FILE.exists():
        return INITIAL_SYNC_TIMESTAMP
    payload = json.loads(MASTER_SYNC_STATE_FILE.read_text(encoding='utf-8'))
    if str(payload.get('master_url', '')).rstrip('/') != master_url.rstrip('/'):
        return INITIAL_SYNC_TIMESTAMP
    # Reconcile rows missed by historic timestamp ordering or NULL-preserving imports.
    if payload.get('snapshot_import_version') != _MASTER_SNAPSHOT_IMPORT_VERSION:
        return INITIAL_SYNC_TIMESTAMP
    timestamp = str(payload.get('last_sync_updated_at', '')).strip()
    return timestamp or INITIAL_SYNC_TIMESTAMP


def fetch_master_delta(master_url: str, since: str) -> tuple[list[dict], int]:
    query = urllib.parse.urlencode({'since': since})
    request = urllib.request.Request(
        f'{master_url}/api/export/delta?{query}',
        headers={'Accept': 'application/x-ndjson'},
    )
    records: list[dict] = []
    bad_lines = 0
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            declared_length = response.headers.get('Content-Length')
            try:
                expected_bytes = int(declared_length)
            except (TypeError, ValueError) as exc:
                raise RuntimeError('master 增量响应缺少有效的 Content-Length，无法确认响应完整性。') from exc
            if expected_bytes < 0:
                raise RuntimeError('master 增量响应的 Content-Length 无效，无法确认响应完整性。')

            received_bytes = 0
            for raw_line in response:
                received_bytes += len(raw_line)
                line = raw_line.decode('utf-8').strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    bad_lines += 1
                    continue
                if not isinstance(record, dict):
                    bad_lines += 1
                    continue
                app_no = normalize_app_no(record.get('application_no'))
                try:
                    sync_updated_at = normalize_sync_cursor(record.get(SYNC_CURSOR_FIELD))
                except ValueError:
                    sync_updated_at = ''
                if not app_no or not sync_updated_at:
                    bad_lines += 1
                    continue
                record['application_no'] = app_no
                record[SYNC_CURSOR_FIELD] = sync_updated_at
                records.append(record)
    except http.client.IncompleteRead as exc:
        raise RuntimeError('master 增量响应在传输完成前中断，已拒绝导入。') from exc
    if received_bytes != expected_bytes:
        raise RuntimeError(
            f'master 增量响应不完整：声明 {expected_bytes} 字节，实际收到 {received_bytes} 字节，已拒绝导入。'
        )
    return records, bad_lines


def merge_master_delta(records: list[dict]) -> dict:
    db = PatentsDB(PATENTS_DB_FILE)
    summary = db.summarize_record_import(records)
    if records:
        db.apply_master_delta(records)
        db.export_to_jsonl(DETECTION_LOG_JSONL_FILE)
    return summary


def commit_patent_backup(summary: dict) -> bool:
    git = shutil.which('git')
    if not git:
        raise MasterSyncConfigurationError('找不到 git，无法提交增量数据备份。')
    relative_backup = str(DETECTION_LOG_JSONL_FILE.relative_to(BASE_DIR))
    relative_readme = 'README.md'
    subprocess.run([git, 'add', '--', relative_backup, relative_readme], cwd=BASE_DIR, check=True)
    staged = subprocess.run(
        [git, 'diff', '--cached', '--quiet', '--', relative_backup, relative_readme],
        cwd=BASE_DIR,
        check=False,
    )
    if staged.returncode == 0:
        return False
    if staged.returncode != 1:
        raise subprocess.CalledProcessError(staged.returncode, staged.args)
    message = (
        f"sync: pull master delta ({summary['records']} records, "
        f"{summary['new_applications']} new)"
    )
    subprocess.run(
        [git, 'commit', '-m', message, '--', relative_backup, relative_readme],
        cwd=BASE_DIR,
        check=True,
    )
    return True


def save_sync_cursor(master_url: str, timestamp: str) -> None:
    payload = {
        'master_url': master_url,
        'last_sync_updated_at': timestamp,
        'snapshot_import_version': _MASTER_SNAPSHOT_IMPORT_VERSION,
        'synced_at': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
    }
    MASTER_SYNC_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = MASTER_SYNC_STATE_FILE.with_suffix('.tmp')
    temporary_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary_path.replace(MASTER_SYNC_STATE_FILE)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--full', action='store_true', help='忽略本地游标，重新合并 master 的全部专利记录')
    args = parser.parse_args(argv)
    try:
        require_replica_pull_role()
        with reserve_master_sync():
            master_url = load_master_url()
            since = INITIAL_SYNC_TIMESTAMP if args.full else load_sync_cursor(master_url)
            print(f"[replica] master: {master_url}")
            print(f"[replica] 拉取起点: {since}")
            records, bad_lines = fetch_master_delta(master_url, since)
            if bad_lines:
                raise RuntimeError(f"增量响应包含 {bad_lines} 行无效记录，已拒绝导入。")
            if not records:
                save_sync_cursor(master_url, since)
                print("[✓] master 没有新记录，已保存同步起点。")
                return
            summary = merge_master_delta(records)
            update_readme_statistics()
            committed = commit_patent_backup(summary)
            latest_sync_updated_at = max(str(record[SYNC_CURSOR_FIELD]) for record in records)
            save_sync_cursor(master_url, latest_sync_updated_at)
            print(
                f"[✓] 已合并 {summary['records']} 条：新增 {summary['new_applications']}，"
                f"更新 {summary['updated_applications']}。"
            )
            print("[✓] 已创建数据提交。" if committed else "[✓] 数据未产生新的 Git 差异。")
            print("下一步请检查提交后执行: git push")
    except (MachineRoleConfigurationError, MasterSyncConfigurationError, urllib.error.URLError,
            OSError, subprocess.CalledProcessError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"[✗] 从 master 拉取失败: {exc}")
        sys.exit(1)


if __name__ == '__main__':
    main()
