#!/usr/bin/env python3
"""Own and supervise the unattended main collection process."""

from __future__ import annotations

import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from collection_checkpoint import CollectionBatch, read_collection_batch
from collection_health import (
    clear_collection_alert,
    read_alert_status,
    read_collection_heartbeat,
    record_collection_alert,
    write_collection_start_heartbeat,
    write_collection_stopped_heartbeat,
)
from desktop_collection_lock import (
    DetailCollectionDesktopBusyError,
    reserve_detail_collection_desktop,
    reserve_supervised_collection,
)
from main_collection_targets import select_main_collection_targets
from settings import (
    BASE_DIR,
    MITM_HOST,
    MITM_PORT,
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
        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=8)
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
            process.wait(timeout=8)


def collection_command(batch_id: str) -> list[str]:
    return [sys.executable, '-u', str(BASE_DIR / 'main_automation.py'), '--resume-batch', batch_id]


def require_main_mitm_proxy() -> None:
    try:
        with socket.create_connection((MITM_HOST, MITM_PORT), timeout=2):
            return
    except OSError as exc:
        raise ConnectionError(
            f'MITM 代理 {MITM_HOST}:{MITM_PORT} 未响应；'
            '请先启动：uv run python start_mitm_proxy.py'
        ) from exc


def start_collection_process(batch_id: str) -> subprocess.Popen:
    collection_environment = os.environ.copy()
    collection_environment['USE_MITM_PROXY'] = 'true'
    popen_kwargs = {
        'cwd': str(BASE_DIR),
        'env': collection_environment,
        'stdin': subprocess.DEVNULL,
    }
    if sys.platform == 'win32':
        popen_kwargs['creationflags'] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        popen_kwargs['start_new_session'] = True
    return subprocess.Popen(collection_command(batch_id), **popen_kwargs)


def supervision_failure(process: subprocess.Popen) -> tuple[str, str] | None:
    login_alert = read_alert_status()
    if login_alert.get('reason') == 'login_required':
        return 'login_required', login_alert['details']
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
    try:
        with reserve_supervised_collection('看门狗采集'):
            with reserve_detail_collection_desktop('看门狗采集准备'):
                clear_collection_alert()
                pending = select_main_collection_targets()
                if not pending:
                    write_collection_stopped_heartbeat(0, 0)
                    print('[watchdog] 没有待采集申请号。')
                    return 0
                require_main_mitm_proxy()
                batch_id = CollectionBatch.prepare('main', pending)
            print(f'[watchdog] 本次采集批次: {batch_id}，目标数={len(pending)}')
            return _supervise_collection_batch(batch_id)
    except (DetailCollectionDesktopBusyError, OSError, ValueError) as error:
        record_collection_alert('collection_start_failed', str(error), 0)
        print(f'[watchdog] 无法继续采集: {error}')
        return 1


def _supervise_collection_batch(batch_id: str) -> int:
    restart_count = 0
    while not _stop_requested:
        write_collection_start_heartbeat(0)
        process = start_collection_process(batch_id)
        print(f'[watchdog] 采集进程已启动，PID={process.pid}，重启次数={restart_count}')
        failure: tuple[str, str] | None = None
        while not _stop_requested:
            failure = supervision_failure(process)
            if failure:
                break
            if process.poll() == 0:
                batch = read_collection_batch(batch_id)
                if batch['status'] == 'completed' and batch['remaining'] == 0:
                    clear_collection_alert()
                    print(f'[watchdog] 采集批次 {batch_id} 已全部完成。')
                    return 0
                failure = ('batch_incomplete', f"批次 {batch_id} 未完成，剩余 {batch['remaining']} 条")
                break
            time.sleep(2)
        terminate_process_tree(process)
        if _stop_requested:
            print('[watchdog] 收到停止请求，采集进程树已终止。')
            return 0
        assert failure is not None
        reason, details = failure
        if reason == 'login_required':
            record_collection_alert(reason, details, restart_count)
            print(f'[watchdog] {details}；等待人工处理，不自动重启。')
            return 1
        batch = read_collection_batch(batch_id)
        if batch['remaining'] == 0:
            record_collection_alert('batch_finalization_failed', details, restart_count)
            print(f'[watchdog] 申请号已采集完成，但收尾失败: {details}；已停止。')
            return 1
        restart_count += 1
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
