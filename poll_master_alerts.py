#!/usr/bin/env python3
"""Poll the master alert endpoint and forward new alerts through ServerChan."""

from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from machine_identity import MachineRoleConfigurationError, require_replica_pull_role
from settings import ALERT_FORWARD_STATE_FILE, ALERT_POLL_SECONDS
from sync_pull_from_master import MasterSyncConfigurationError, load_master_url


def fetch_master_alert(master_url: str) -> dict:
    request = urllib.request.Request(f'{master_url}/api/alert-status', headers={'Accept': 'application/json'})
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode('utf-8'))


def load_last_forwarded_timestamp() -> str | None:
    if not ALERT_FORWARD_STATE_FILE.exists():
        return None
    payload = json.loads(ALERT_FORWARD_STATE_FILE.read_text(encoding='utf-8'))
    return payload.get('last_alert_timestamp')


def save_forwarded_timestamp(timestamp: str) -> None:
    payload = {
        'last_alert_timestamp': timestamp,
        'forwarded_at': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
    }
    temporary_path = ALERT_FORWARD_STATE_FILE.with_suffix('.tmp')
    temporary_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary_path.replace(ALERT_FORWARD_STATE_FILE)


def send_serverchan_alert(alert_status: dict) -> None:
    send_key = os.getenv('SERVERCHAN_SENDKEY', '').strip()
    if not send_key:
        raise RuntimeError('未设置 SERVERCHAN_SENDKEY，无法转发报警。')
    title = f"CNIPA 部署机报警: {alert_status.get('reason') or '未知原因'}"
    description = (
        f"时间: {alert_status.get('timestamp')}\n\n"
        f"原因: {alert_status.get('reason')}\n\n"
        f"详情: {alert_status.get('details')}\n\n"
        f"重启次数: {alert_status.get('restart_count', 0)}"
    )
    body = urllib.parse.urlencode({'title': title, 'desp': description}).encode('utf-8')
    request = urllib.request.Request(
        f'https://sctapi.ftqq.com/{send_key}.send',
        data=body,
        headers={'Content-Type': 'application/x-www-form-urlencoded'},
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        response.read()


def poll_once(master_url: str) -> bool:
    alert_status = fetch_master_alert(master_url)
    timestamp = alert_status.get('timestamp')
    if alert_status.get('status') != 'alert' or not timestamp:
        return False
    if timestamp == load_last_forwarded_timestamp():
        return False
    send_serverchan_alert(alert_status)
    save_forwarded_timestamp(timestamp)
    print(f"[✓] 已转发报警: {alert_status.get('reason')}")
    return True


def main() -> None:
    try:
        require_replica_pull_role()
        master_url = load_master_url()
        print(f'[alert-poller] 正在轮询 {master_url}，间隔 {ALERT_POLL_SECONDS} 秒。')
        while True:
            try:
                poll_once(master_url)
            except Exception as exc:
                print(f'[!] 报警轮询失败: {exc}')
            time.sleep(ALERT_POLL_SECONDS)
    except (MachineRoleConfigurationError, MasterSyncConfigurationError) as exc:
        print(f'[✗] {exc}')
        raise SystemExit(1)


if __name__ == '__main__':
    main()
