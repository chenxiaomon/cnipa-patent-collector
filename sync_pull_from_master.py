#!/usr/bin/env python3
"""Pull patent deltas from the master Dashboard into this replica."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from cache_utils import normalize_app_no
from db_manager import SYNC_CURSOR_FIELD, PatentsDB
from machine_identity import MachineRoleConfigurationError, require_replica_pull_role
from settings import (
    BASE_DIR,
    DETECTION_LOG_JSONL_FILE,
    MASTER_SYNC_CONFIG_FILE,
    MASTER_SYNC_STATE_FILE,
    PATENTS_DB_FILE,
)
from update_readme_stats import update_readme_statistics

INITIAL_SYNC_TIMESTAMP = '1970-01-01T00:00:00Z'


class MasterSyncConfigurationError(RuntimeError):
    """Raised when the replica cannot safely identify its master endpoint."""


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
    with urllib.request.urlopen(request, timeout=60) as response:
        for raw_line in response:
            line = raw_line.decode('utf-8').strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                bad_lines += 1
                continue
            app_no = normalize_app_no(record.get('application_no'))
            sync_updated_at = str(record.get(SYNC_CURSOR_FIELD, '')).strip()
            try:
                datetime.fromisoformat(sync_updated_at.replace('Z', '+00:00'))
            except ValueError:
                sync_updated_at = ''
            if not app_no or not sync_updated_at:
                bad_lines += 1
                continue
            record['application_no'] = app_no
            record[SYNC_CURSOR_FIELD] = sync_updated_at
            records.append(record)
    return records, bad_lines


def merge_master_delta(records: list[dict]) -> dict:
    db = PatentsDB(PATENTS_DB_FILE)
    summary = db.summarize_record_import(records)
    if records:
        db.upsert_batch(records)
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
        'synced_at': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
    }
    MASTER_SYNC_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = MASTER_SYNC_STATE_FILE.with_suffix('.tmp')
    temporary_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary_path.replace(MASTER_SYNC_STATE_FILE)


def main() -> None:
    try:
        require_replica_pull_role()
        master_url = load_master_url()
        since = load_sync_cursor(master_url)
        print(f"[replica] master: {master_url}")
        print(f"[replica] 拉取起点: {since}")
        records, bad_lines = fetch_master_delta(master_url, since)
        if bad_lines:
            raise RuntimeError(f"增量响应包含 {bad_lines} 行无效记录，已拒绝导入。")
        if not records:
            print("[✓] master 没有新记录，本地游标保持不变。")
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
