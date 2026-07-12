#!/usr/bin/env python3
"""Own and supervise the unattended main collection process."""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from collection_health import (
    clear_collection_alert,
    read_collection_heartbeat,
    record_collection_alert,
    write_collection_start_heartbeat,
)
from settings import (
    BASE_DIR,
    WATCHDOG_FAILURE_THRESHOLD,
    WATCHDOG_HEARTBEAT_TIMEOUT_SECONDS,
    WATCHDOG_MAX_RESTARTS,
    WATCHDOG_MIN_FREE_GB,
)

_stop_requested = False


def _request_stop(signum, frame) -> None:
    del signum, frame
    global _stop_requested
    _stop_requested = True


def heartbeat_age_seconds(heartbeat: dict | None) -> float | None:
    if not heartbeat or not heartbeat.get('timestamp'):
        return None
    try:
        timestamp = datetime.fromisoformat(str(heartbeat['timestamp']).replace('Z', '+00:00'))
    except ValueError:
        return None
    return max(0.0, (datetime.now(timezone.utc) - timestamp).total_seconds())


def terminate_process_tree(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    if sys.platform == 'win32':
        subprocess.run(
            ['taskkill', '/PID', str(process.pid), '/T', '/F'],
            check=False,
            capture_output=True,
        )
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


def collection_command() -> list[str]:
    return [sys.executable, '-u', str(BASE_DIR / 'main_automation.py')]


def start_collection_process() -> subprocess.Popen:
    popen_kwargs = {'cwd': str(BASE_DIR), 'env': os.environ.copy()}
    if sys.platform == 'win32':
        popen_kwargs['creationflags'] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        popen_kwargs['start_new_session'] = True
    return subprocess.Popen(collection_command(), **popen_kwargs)


def supervision_failure(process: subprocess.Popen) -> tuple[str, str] | None:
    free_gb = shutil.disk_usage(BASE_DIR).free / (1024 ** 3)
    if free_gb < WATCHDOG_MIN_FREE_GB:
        return 'disk_space_low', f'磁盘剩余 {free_gb:.2f} GB，阈值 {WATCHDOG_MIN_FREE_GB:.2f} GB'
    heartbeat = read_collection_heartbeat()
    age = heartbeat_age_seconds(heartbeat)
    if age is not None and age > WATCHDOG_HEARTBEAT_TIMEOUT_SECONDS:
        return 'heartbeat_timeout', f'采集心跳已中断 {int(age)} 秒'
    consecutive_failures = int((heartbeat or {}).get('consecutive_failures') or 0)
    if consecutive_failures >= WATCHDOG_FAILURE_THRESHOLD:
        return 'consecutive_failures', f'连续采集失败 {consecutive_failures} 条'
    if process.poll() is not None and process.returncode != 0:
        return 'collection_exited', f'采集进程退出码 {process.returncode}'
    return None


def run_supervised_collection() -> int:
    signal.signal(signal.SIGTERM, _request_stop)
    signal.signal(signal.SIGINT, _request_stop)
    clear_collection_alert()
    restart_count = 0
    while not _stop_requested:
        write_collection_start_heartbeat(0)
        process = start_collection_process()
        print(f'[watchdog] 采集进程已启动，PID={process.pid}，重启次数={restart_count}')
        failure: tuple[str, str] | None = None
        while not _stop_requested:
            failure = supervision_failure(process)
            if failure:
                break
            if process.poll() == 0:
                clear_collection_alert()
                print('[watchdog] 采集任务正常完成。')
                return 0
            time.sleep(2)
        terminate_process_tree(process)
        if _stop_requested:
            print('[watchdog] 收到停止请求，采集进程树已终止。')
            return 0
        assert failure is not None
        restart_count += 1
        reason, details = failure
        record_collection_alert(reason, details, restart_count)
        print(f'[watchdog] {details}，准备第 {restart_count} 次重启。')
        if restart_count >= WATCHDOG_MAX_RESTARTS:
            record_collection_alert(
                'restart_limit_reached',
                f'连续重启失败 {restart_count} 次，已彻底停止。最后原因: {details}',
                restart_count,
            )
            return 1
        time.sleep(min(30, restart_count * 5))
    return 0


if __name__ == '__main__':
    raise SystemExit(run_supervised_collection())
