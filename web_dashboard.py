#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CNIPA 采集系统本地可视化控制台。

设计目标：
- 不引入 Flask/FastAPI/Node 等新依赖，直接用 Python 标准库启动本地 Web UI。
- 只通过白名单命令调用现有脚本，避免把命令行参数暴露成任意 shell。
- 后台任务统一收集日志，方便从浏览器观察 MITM、采集、策略生成等流程。
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import socket
import subprocess
import sys
import threading
import time
import uuid
from collections import Counter, deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from settings import (
    BASE_DIR,
    CONFIG_FILE,
    DATA_DIR,
    DETECTION_LOG_FILE,
    DETECTION_LOG_JSONL_FILE,
    MITM_HOST,
    MITM_PORT,
    PATENTS_EXCEL_FILE,
    RESULTS_DIR,
    SEARCH_LIST_FILE,
)


APP_NAME = "CNIPA 采集控制台"
SERVER_VERSION = "CNIPADashboard/0.1"
MAX_LOG_LINES = 1600
DEFAULT_LOGIN_WAIT_SECONDS = "75"


DOWNLOADS = {
    "excel": PATENTS_EXCEL_FILE,
    "jsonl": DETECTION_LOG_JSONL_FILE,
    "json": DETECTION_LOG_FILE,
    "dynamic": DATA_DIR / "update_list_dynamic.txt",
    "dynamic_7": DATA_DIR / "update_list_dynamic_7days.txt",
    "retry": DATA_DIR / "retry_dynamic.txt",
}


def resolve_task_python() -> str:
    """后台任务优先使用项目虚拟环境，避免系统 Python 缺依赖。"""
    override = os.getenv("DASHBOARD_TASK_PYTHON")
    if override:
        return override

    for candidate in (
        BASE_DIR / ".venv" / "bin" / "python",
        BASE_DIR / "venv" / "bin" / "python",
        BASE_DIR / ".venv" / "Scripts" / "python.exe",
        BASE_DIR / "venv" / "Scripts" / "python.exe",
    ):
        if candidate.exists():
            return str(candidate)

    return sys.executable


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_now() -> str:
    return utc_now().isoformat().replace("+00:00", "Z")


def parse_timestamp(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def normalize_app_no(app_no: str | None) -> str:
    if not app_no:
        return ""
    return str(app_no).upper().replace("CN", "").replace(".", "").strip()


def safe_read_text(path: Path, default: str = "") -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return default


def safe_json_load(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default


def read_jsonl(path: Path) -> tuple[list[dict[str, Any]], int]:
    records: list[dict[str, Any]] = []
    bad_lines = 0
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    bad_lines += 1
                    continue
                if isinstance(item, dict):
                    records.append(item)
    except FileNotFoundError:
        return [], 0
    return records, bad_lines


def count_lines(path: Path) -> int:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return sum(1 for line in handle if line.strip())
    except FileNotFoundError:
        return 0


def file_info(path: Path) -> dict[str, Any]:
    exists = path.exists()
    stat = path.stat() if exists else None
    return {
        "exists": exists,
        "name": path.name,
        "path": str(path.relative_to(BASE_DIR) if path.is_relative_to(BASE_DIR) else path),
        "size": stat.st_size if stat else 0,
        "mtime": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat().replace("+00:00", "Z")
        if stat
        else None,
    }


def port_open(host: str, port: int, timeout: float = 0.2) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


@dataclass
class Job:
    id: str
    action: str
    title: str
    command: list[str]
    env_overrides: dict[str, str] = field(default_factory=dict)
    started_at: str = field(default_factory=iso_now)
    finished_at: str | None = None
    status: str = "running"
    returncode: int | None = None
    lines: deque[str] = field(default_factory=lambda: deque(maxlen=MAX_LOG_LINES))
    process: subprocess.Popen[str] | None = None

    def append(self, line: str) -> None:
        self.lines.append(line.rstrip("\n"))

    def to_dict(self, include_logs: bool = False) -> dict[str, Any]:
        data = {
            "id": self.id,
            "action": self.action,
            "title": self.title,
            "command": printable_command(self.command),
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "status": self.status,
            "returncode": self.returncode,
            "log_count": len(self.lines),
        }
        if include_logs:
            data["logs"] = list(self.lines)
        return data


def printable_command(command: list[str]) -> str:
    return " ".join(command)


class JobManager:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def list_jobs(self) -> list[dict[str, Any]]:
        with self._lock:
            jobs = sorted(self._jobs.values(), key=lambda item: item.started_at, reverse=True)
            return [job.to_dict() for job in jobs[:40]]

    def get_job(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def start(self, action: str, params: dict[str, Any]) -> Job:
        spec = build_job_spec(action, params)
        if self._already_running(spec["action"]):
            raise ValueError(f"{spec['title']} 已在运行")

        job = Job(
            id=uuid.uuid4().hex[:10],
            action=spec["action"],
            title=spec["title"],
            command=spec["command"],
            env_overrides=spec.get("env", {}),
        )
        env = os.environ.copy()
        env.update(job.env_overrides)
        env.setdefault("PYTHONUNBUFFERED", "1")

        process = subprocess.Popen(
            job.command,
            cwd=str(BASE_DIR),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        job.process = process
        job.append(f"$ {printable_command(job.command)}")

        with self._lock:
            self._jobs[job.id] = job

        thread = threading.Thread(target=self._watch_process, args=(job,), daemon=True)
        thread.start()
        return job

    def stop(self, job_id: str) -> bool:
        job = self.get_job(job_id)
        if not job or not job.process:
            return False
        if job.process.poll() is not None:
            return False
        job.status = "stopping"
        job.append("[dashboard] 正在停止任务...")
        job.process.terminate()
        threading.Thread(target=self._force_kill_later, args=(job,), daemon=True).start()
        return True

    def _already_running(self, action: str) -> bool:
        singleton_actions = {"mitm_proxy", "public_mitm_proxy", "phase0_browser", "public_browser"}
        if action not in singleton_actions:
            return False
        with self._lock:
            for job in self._jobs.values():
                if job.action == action and job.status in {"running", "stopping"}:
                    return True
        return False

    def _watch_process(self, job: Job) -> None:
        assert job.process is not None
        assert job.process.stdout is not None
        try:
            for line in job.process.stdout:
                job.append(line)
        finally:
            returncode = job.process.wait()
            job.returncode = returncode
            job.finished_at = iso_now()
            if job.status == "stopping":
                job.status = "stopped"
            elif returncode == 0:
                job.status = "finished"
            else:
                job.status = "failed"
            job.append(f"[dashboard] 任务结束，退出码: {returncode}")

    def _force_kill_later(self, job: Job) -> None:
        time.sleep(5)
        if job.process and job.process.poll() is None:
            job.append("[dashboard] 任务未正常退出，强制结束")
            job.process.kill()


def positive_int(value: Any, default: int | None = None, minimum: int = 1, maximum: int = 100000) -> int | None:
    if value in (None, ""):
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, parsed))


def relative_data_file(value: str | None, default: str) -> str:
    text = (value or default).strip()
    candidate = (BASE_DIR / text).resolve()
    data_root = DATA_DIR.resolve()
    if candidate == data_root or data_root not in candidate.parents:
        return default
    return str(candidate.relative_to(BASE_DIR))


def build_job_spec(action: str, params: dict[str, Any]) -> dict[str, Any]:
    py = resolve_task_python()
    action = action.strip()

    if action == "mitm_proxy":
        return {
            "action": action,
            "title": "主 MITM 代理",
            "command": [py, "-u", "start_mitm_proxy.py"],
        }
    if action == "public_mitm_proxy":
        return {
            "action": action,
            "title": "公开查询 MITM 代理",
            "command": [py, "-u", "start_mitm_public_search.py"],
        }
    if action == "main_full":
        return {
            "action": action,
            "title": "主流程采集",
            "command": [py, "-u", "main_automation.py"],
            "env": {"USE_MITM_PROXY": "true", "CNIPA_LOGIN_WAIT_SECONDS": DEFAULT_LOGIN_WAIT_SECONDS},
        }
    if action == "main_test":
        count = positive_int(params.get("count"), default=5, maximum=10000)
        return {
            "action": action,
            "title": f"强制测试前 {count} 条",
            "command": [py, "-u", "main_automation.py", "--update-list", "data/search_list.txt", "--test", str(count)],
            "env": {"USE_MITM_PROXY": "true", "CNIPA_LOGIN_WAIT_SECONDS": DEFAULT_LOGIN_WAIT_SECONDS},
        }
    if action == "main_update_dynamic":
        update_file = relative_data_file(params.get("file"), "data/update_list_dynamic.txt")
        command = [py, "-u", "main_automation.py", "--update-list", update_file]
        count = positive_int(params.get("count"), default=None, maximum=10000)
        if count:
            command.extend(["--test", str(count)])
        return {
            "action": action,
            "title": f"按清单更新 {Path(update_file).name}",
            "command": command,
            "env": {"USE_MITM_PROXY": "true", "CNIPA_LOGIN_WAIT_SECONDS": DEFAULT_LOGIN_WAIT_SECONDS},
        }
    if action == "collect_fwxx":
        command = [py, "-u", "collect_fwxx.py"]
        count = positive_int(params.get("count"), default=None, maximum=10000)
        if count:
            command.extend(["--test", str(count)])
        return {
            "action": action,
            "title": "补采发文信息",
            "command": command,
            "env": {"USE_MITM_PROXY": "true", "CNIPA_LOGIN_WAIT_SECONDS": DEFAULT_LOGIN_WAIT_SECONDS},
        }
    if action == "collect_fwxx_app":
        app_no = normalize_app_no(params.get("app_no"))
        if not app_no:
            raise ValueError("请输入申请号")
        return {
            "action": action,
            "title": f"补采发文 {app_no}",
            "command": [py, "-u", "collect_fwxx.py", "--app", app_no],
            "env": {"USE_MITM_PROXY": "true", "CNIPA_LOGIN_WAIT_SECONDS": DEFAULT_LOGIN_WAIT_SECONDS},
        }
    if action == "strategy_generate":
        command = [py, "-u", "update_by_strategy.py", "generate"]
        freq = positive_int(params.get("frequency"), default=None, maximum=3650)
        if freq:
            command.append(str(freq))
        return {
            "action": action,
            "title": "生成状态检查清单",
            "command": command,
        }
    if action == "strategy_status":
        command = [py, "-u", "update_by_strategy.py", "status"]
        freq = positive_int(params.get("frequency"), default=None, maximum=3650)
        if freq:
            command.append(str(freq))
        return {
            "action": action,
            "title": "查看策略状态",
            "command": command,
        }
    if action == "strategy_check":
        app_no = normalize_app_no(params.get("app_no"))
        if not app_no:
            raise ValueError("请输入申请号")
        return {
            "action": action,
            "title": f"检查申请号 {app_no}",
            "command": [py, "-u", "update_by_strategy.py", "check", app_no],
        }
    if action in {"strategy_prepare", "strategy_diff", "strategy_report", "strategy_validate", "strategy_stats"}:
        command_name = {
            "strategy_prepare": "prepare",
            "strategy_diff": "diff",
            "strategy_report": "report",
            "strategy_validate": "validate",
            "strategy_stats": "stats",
        }[action]
        title = {
            "strategy_prepare": "保存采集前快照",
            "strategy_diff": "查看状态变化",
            "strategy_report": "生成详细报告",
            "strategy_validate": "验证策略计数",
            "strategy_stats": "策略统计",
        }[action]
        return {
            "action": action,
            "title": title,
            "command": [py, "-u", "update_by_strategy.py", command_name],
        }
    if action == "export_excel":
        return {
            "action": action,
            "title": "导出 Excel",
            "command": [py, "-u", "-c", "from detection_logger import DetectionLogger; DetectionLogger().export_to_excel()"],
        }
    if action == "export_json":
        return {
            "action": action,
            "title": "导出 JSON",
            "command": [py, "-u", "-c", "from detection_logger import DetectionLogger; DetectionLogger().export_to_json()"],
        }
    if action == "phase0_browser":
        return {
            "action": action,
            "title": "Phase 0 浏览器",
            "command": [py, "-u", "start_browser_for_phase0.py"],
        }
    if action == "import_cache":
        return {
            "action": action,
            "title": "导入 MITM 缓存",
            "command": [py, "-u", "import_from_cache.py"],
        }
    if action == "public_browser":
        return {
            "action": action,
            "title": "公开查询浏览器",
            "command": [py, "-u", "launch_browser_with_proxy.py"],
        }
    if action == "public_auto_paginate":
        delay = params.get("delay", 1.5)
        try:
            delay_text = str(max(0.2, min(30.0, float(delay))))
        except (TypeError, ValueError):
            delay_text = "1.5"
        max_pages = positive_int(params.get("max_pages"), default=50, maximum=10000)
        return {
            "action": action,
            "title": f"公开查询自动翻页 {max_pages} 页",
            "command": [py, "-u", "auto_paginate.py", "--delay", delay_text, "--max-pages", str(max_pages)],
        }
    if action == "public_export":
        return {
            "action": action,
            "title": "导出公开查询结果",
            "command": [py, "-u", "export_public_search.py"],
        }

    raise ValueError(f"未知操作: {action}")


def build_summary(job_manager: JobManager) -> dict[str, Any]:
    records, bad_lines = read_jsonl(DETECTION_LOG_JSONL_FILE)
    latest: dict[str, dict[str, Any]] = {}
    for record in records:
        app_no = normalize_app_no(record.get("application_no"))
        if app_no:
            latest[app_no] = record

    latest_records = list(latest.values())
    success = sum(1 for item in latest_records if item.get("status_code") == 200)
    failed = len(latest_records) - success
    status_counter = Counter(item.get("anjianywzt") or "未知" for item in latest_records)
    applicant_counter = Counter(item.get("shenqingrxm") or "未知" for item in latest_records)
    rejection = sum(1 for item in latest_records if item.get("anjianywzt") == "驳回等复审请求")
    fwxx_collected = sum(1 for item in latest_records if item.get("fwxx_list"))
    fwxx_pending = sum(
        1
        for item in latest_records
        if item.get("anjianywzt") == "驳回等复审请求" and item.get("fwxx_list") is None
    )

    focus_strategy = safe_json_load(DATA_DIR / "focus_strategy.json", {})
    status_breakdown = focus_strategy.get("status_breakdown", {}) if isinstance(focus_strategy, dict) else {}
    update_groups = build_update_groups(latest_records, status_breakdown)

    search_count = count_lines(SEARCH_LIST_FILE)
    dynamic_count = count_lines(DATA_DIR / "update_list_dynamic.txt")
    retry_count = count_lines(DATA_DIR / "retry_dynamic.txt")
    config = safe_json_load(CONFIG_FILE, {})

    recent = sorted(records[-16:], key=lambda item: item.get("timestamp") or "", reverse=True)
    recent_rows = [
        {
            "application_no": item.get("application_no"),
            "status_code": item.get("status_code"),
            "anjianywzt": item.get("anjianywzt"),
            "zhuanlimc": item.get("zhuanlimc"),
            "shenqingrxm": item.get("shenqingrxm"),
            "timestamp": item.get("timestamp"),
            "response_time_ms": item.get("response_time_ms"),
        }
        for item in recent
    ]

    active_jobs = [
        job
        for job in job_manager.list_jobs()
        if job.get("status") in {"running", "stopping"}
    ]

    warnings: list[str] = []
    if search_count == 0:
        warnings.append("申请号列表为空")
    if not CONFIG_FILE.exists():
        warnings.append("鼠标坐标配置不存在")
    elif config.get("input_x") == config.get("button_x") and config.get("input_y") == config.get("button_y"):
        warnings.append("输入框和查询按钮坐标相同，强制测试可能无法点击查询按钮")
    if bad_lines:
        warnings.append(f"日志中有 {bad_lines} 行解析失败")
    if dynamic_count == 0:
        warnings.append("动态更新清单为空")

    return {
        "now": iso_now(),
        "records": {
            "events": len(records),
            "unique": len(latest_records),
            "success": success,
            "failed": failed,
            "success_rate": round(success / len(latest_records) * 100, 2) if latest_records else 0,
            "bad_lines": bad_lines,
        },
        "business": {
            "rejection": rejection,
            "fwxx_collected": fwxx_collected,
            "fwxx_pending": fwxx_pending,
            "tracked_total": sum(group["total"] for group in update_groups),
            "update_due": sum(group["due"] for group in update_groups),
        },
        "lists": {
            "search": search_count,
            "dynamic": dynamic_count,
            "retry": retry_count,
        },
        "proxy": {
            "host": MITM_HOST,
            "port": MITM_PORT,
            "reachable": port_open(MITM_HOST, MITM_PORT),
        },
        "config": config,
        "update_groups": update_groups,
        "status_counts": status_counter.most_common(12),
        "applicant_counts": applicant_counter.most_common(8),
        "recent": recent_rows,
        "files": {key: file_info(path) for key, path in DOWNLOADS.items()},
        "jobs": active_jobs,
        "warnings": warnings,
    }


def build_update_groups(records: list[dict[str, Any]], status_breakdown: dict[str, Any]) -> list[dict[str, Any]]:
    groups: dict[int, dict[str, Any]] = {}
    now = utc_now()
    for status, info in status_breakdown.items():
        freq = int(info.get("frequency_days", 0) or 0)
        if not freq:
            continue
        group = groups.setdefault(
            freq,
            {
                "frequency_days": freq,
                "frequency_name": info.get("frequency_name") or f"{freq}天检查",
                "statuses": [],
                "total": 0,
                "due": 0,
                "waiting": 0,
                "earliest": None,
            },
        )
        group["statuses"].append(status)

    for item in records:
        status = item.get("anjianywzt")
        info = status_breakdown.get(status)
        if not info:
            continue
        freq = int(info.get("frequency_days", 0) or 0)
        if freq not in groups:
            continue
        group = groups[freq]
        group["total"] += 1
        last_update = parse_timestamp(item.get("timestamp"))
        next_update = last_update + timedelta(days=freq) if last_update else None
        needs_update = next_update is None or now >= next_update
        if needs_update:
            group["due"] += 1
        else:
            group["waiting"] += 1
        if next_update and (group["earliest"] is None or next_update.isoformat() < group["earliest"]):
            group["earliest"] = next_update.isoformat().replace("+00:00", "Z")

    return [groups[key] for key in sorted(groups)]


HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>CNIPA 采集控制台</title>
  <link rel="stylesheet" href="/app.css">
</head>
<body>
  <main class="shell">
    <header class="topbar">
      <div>
        <h1>CNIPA 采集控制台</h1>
        <div class="subline">
          <span id="clock">--</span>
          <span class="dot"></span>
          <span id="proxyPill" class="pill muted">代理检测中</span>
        </div>
      </div>
      <div class="top-actions">
        <button class="btn secondary" data-action="export_excel">导出 Excel</button>
        <button class="btn secondary" data-action="export_json">导出 JSON</button>
        <button class="btn primary" data-action="mitm_proxy">启动主代理</button>
      </div>
    </header>

    <section id="warnings" class="warnings hidden"></section>

    <section class="metrics" aria-label="采集概览">
      <article class="metric"><span>唯一申请号</span><strong id="mUnique">0</strong><em id="mEvents">0 条记录</em></article>
      <article class="metric"><span>成功率</span><strong id="mRate">0%</strong><em id="mSuccess">0 成功 / 0 失败</em></article>
      <article class="metric"><span>驳回目标</span><strong id="mRejection">0</strong><em id="mFwxx">0 待补发文</em></article>
      <article class="metric"><span>现在应检查</span><strong id="mDue">0</strong><em id="mTracked">0 跟踪中</em></article>
      <article class="metric"><span>动态清单</span><strong id="mDynamic">0</strong><em id="mSearch">0 输入申请号</em></article>
    </section>

    <section class="grid two">
      <article class="panel">
        <div class="panel-head">
          <h2>日常采集</h2>
          <span class="hint">主流程</span>
        </div>
        <div class="control-grid">
          <label class="field">
            <span>测试条数</span>
            <input id="testCount" type="number" min="1" max="10000" value="5">
          </label>
          <button class="btn primary" id="runTest">强制测试</button>
          <button class="btn danger-soft" data-action="main_full">继续全量采集</button>
        </div>
        <div class="control-grid">
          <label class="field">
            <span>更新清单</span>
            <select id="updateFile">
              <option value="data/update_list_dynamic.txt">动态清单</option>
              <option value="data/update_list_dynamic_7days.txt">7 天动态清单</option>
              <option value="data/retry_dynamic.txt">动态重试清单</option>
              <option value="data/retry_failed.txt">失败重试清单</option>
            </select>
          </label>
          <label class="field">
            <span>限制条数</span>
            <input id="updateLimit" type="number" min="1" max="10000" placeholder="不限制">
          </label>
          <button class="btn primary" id="runUpdate">按清单更新</button>
        </div>
        <div class="button-row">
          <button class="btn secondary" data-action="collect_fwxx">补采发文</button>
          <button class="btn secondary" id="collectFwxxTest">补采测试</button>
          <button class="btn secondary" data-action="phase0_browser">Phase 0 浏览器</button>
          <button class="btn secondary" data-action="import_cache">导入缓存</button>
        </div>
      </article>

      <article class="panel">
        <div class="panel-head">
          <h2>状态检查策略</h2>
          <span class="hint">update_by_strategy</span>
        </div>
        <div class="control-grid">
          <label class="field">
            <span>周期</span>
            <select id="strategyFrequency">
              <option value="">全部</option>
              <option value="7">7 天</option>
              <option value="14">14 天</option>
              <option value="30">30 天</option>
              <option value="45">45 天</option>
            </select>
          </label>
          <button class="btn primary" id="generateStrategy">生成清单</button>
          <button class="btn secondary" id="statusStrategy">查看状态</button>
        </div>
        <div class="button-row">
          <button class="btn secondary" data-action="strategy_prepare">保存快照</button>
          <button class="btn secondary" data-action="strategy_diff">状态变化</button>
          <button class="btn secondary" data-action="strategy_report">详细报告</button>
          <button class="btn secondary" data-action="strategy_validate">校验策略</button>
        </div>
        <div class="check-line">
          <input id="singleAppNo" placeholder="输入申请号">
          <button class="btn primary" id="checkApp">单号判断</button>
        </div>
      </article>
    </section>

    <section class="grid two">
      <article class="panel">
        <div class="panel-head">
          <h2>策略分组</h2>
          <span class="hint">按检查周期</span>
        </div>
        <div id="strategyGroups" class="group-list"></div>
      </article>

      <article class="panel">
        <div class="panel-head">
          <h2>公开查询</h2>
          <span class="hint">publicSearch</span>
        </div>
        <div class="button-row">
          <button class="btn primary" data-action="public_mitm_proxy">公开代理</button>
          <button class="btn secondary" data-action="public_browser">公开浏览器</button>
          <button class="btn secondary" data-action="public_export">导出公开结果</button>
        </div>
        <div class="control-grid">
          <label class="field">
            <span>翻页间隔</span>
            <input id="pageDelay" type="number" min="0.2" max="30" step="0.1" value="1.5">
          </label>
          <label class="field">
            <span>最大页数</span>
            <input id="maxPages" type="number" min="1" max="10000" value="50">
          </label>
          <button class="btn primary" id="autoPaginate">自动翻页</button>
        </div>
        <div class="downloads">
          <a href="/download/excel">Excel</a>
          <a href="/download/jsonl">JSONL</a>
          <a href="/download/json">JSON</a>
          <a href="/download/dynamic">动态清单</a>
        </div>
      </article>
    </section>

    <section class="grid two wide-left">
      <article class="panel">
        <div class="panel-head">
          <h2>任务日志</h2>
          <div class="job-controls">
            <select id="jobSelect"></select>
            <button class="btn secondary" id="stopJob">停止</button>
          </div>
        </div>
        <pre id="jobLog" class="terminal">等待任务启动...</pre>
      </article>

      <article class="panel">
        <div class="panel-head">
          <h2>申请号列表</h2>
          <button class="btn primary" id="saveSearchList">保存</button>
        </div>
        <textarea id="searchList" spellcheck="false"></textarea>
        <div class="mini-grid">
          <div>
            <h3>状态分布</h3>
            <div id="statusCounts" class="mini-list"></div>
          </div>
          <div>
            <h3>申请人 TOP</h3>
            <div id="applicantCounts" class="mini-list"></div>
          </div>
        </div>
      </article>
    </section>

    <section class="panel">
      <div class="panel-head">
        <h2>最近记录</h2>
        <span class="hint">最新写入</span>
      </div>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>申请号</th>
              <th>状态码</th>
              <th>业务状态</th>
              <th>专利名称</th>
              <th>申请人</th>
              <th>耗时</th>
              <th>时间</th>
            </tr>
          </thead>
          <tbody id="recentRows"></tbody>
        </table>
      </div>
    </section>

    <section class="panel">
      <div class="panel-head">
        <h2>坐标配置</h2>
        <div class="button-row">
          <button class="btn secondary" id="resetConfig">重录坐标</button>
          <button class="btn primary" id="saveConfig">保存配置</button>
        </div>
      </div>
      <textarea id="configText" class="codebox" spellcheck="false"></textarea>
    </section>
  </main>

  <div id="toast" class="toast hidden"></div>
  <script src="/app.js"></script>
</body>
</html>
"""


CSS = r""":root {
  color-scheme: light;
  --bg: #eef1ef;
  --panel: #ffffff;
  --panel-soft: #f7f8f5;
  --ink: #202623;
  --muted: #66736d;
  --line: #d9ded8;
  --accent: #147a63;
  --accent-dark: #0e5d4d;
  --amber: #ad6b00;
  --red: #b13b3b;
  --shadow: 0 18px 50px rgba(31, 42, 36, 0.10);
}

* { box-sizing: border-box; }

body {
  margin: 0;
  min-height: 100vh;
  font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei", sans-serif;
  color: var(--ink);
  background:
    linear-gradient(180deg, rgba(255,255,255,0.82), rgba(255,255,255,0.12) 220px),
    var(--bg);
}

button, input, select, textarea {
  font: inherit;
}

.shell {
  width: min(1480px, calc(100vw - 32px));
  margin: 0 auto;
  padding: 22px 0 40px;
}

.topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 20px;
  padding: 18px 0 20px;
}

h1, h2, h3, p { margin: 0; }

h1 {
  font-size: clamp(24px, 3vw, 34px);
  line-height: 1.1;
  letter-spacing: 0;
}

h2 {
  font-size: 17px;
  letter-spacing: 0;
}

h3 {
  font-size: 13px;
  margin-bottom: 10px;
}

.subline {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 8px;
  color: var(--muted);
  font-size: 13px;
}

.dot {
  width: 4px;
  height: 4px;
  border-radius: 999px;
  background: #9aa59f;
}

.top-actions, .button-row, .job-controls, .downloads {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
}

.btn {
  border: 1px solid transparent;
  border-radius: 7px;
  min-height: 36px;
  padding: 0 13px;
  cursor: pointer;
  color: var(--ink);
  background: #edf1ec;
  transition: transform .12s ease, border-color .12s ease, background .12s ease;
  white-space: nowrap;
}

.btn:hover {
  transform: translateY(-1px);
}

.btn.primary {
  background: var(--accent);
  color: #fff;
}

.btn.primary:hover {
  background: var(--accent-dark);
}

.btn.secondary {
  background: #f4f5f2;
  border-color: var(--line);
}

.btn.danger-soft {
  background: #fff2ef;
  border-color: #f0c8bd;
  color: #913729;
}

.pill {
  display: inline-flex;
  align-items: center;
  min-height: 24px;
  padding: 0 9px;
  border-radius: 999px;
  border: 1px solid var(--line);
  background: #fff;
  color: var(--muted);
}

.pill.ok {
  color: var(--accent-dark);
  background: #e9f5ef;
  border-color: #b9dcca;
}

.pill.warn {
  color: var(--amber);
  background: #fff7e8;
  border-color: #efd19c;
}

.warnings {
  margin-bottom: 14px;
  border: 1px solid #efd19c;
  background: #fff8ec;
  color: #755014;
  border-radius: 8px;
  padding: 11px 13px;
  font-size: 14px;
}

.hidden { display: none !important; }

.metrics {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 12px;
  margin-bottom: 14px;
}

.metric {
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 14px;
  box-shadow: var(--shadow);
}

.metric span {
  display: block;
  color: var(--muted);
  font-size: 13px;
  margin-bottom: 7px;
}

.metric strong {
  display: block;
  font-size: 30px;
  line-height: 1;
  letter-spacing: 0;
}

.metric em {
  display: block;
  color: var(--muted);
  font-style: normal;
  font-size: 12px;
  margin-top: 8px;
}

.grid {
  display: grid;
  gap: 14px;
  margin-bottom: 14px;
}

.grid.two {
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
}

.grid.wide-left {
  grid-template-columns: minmax(0, 1.35fr) minmax(360px, .65fr);
}

.panel {
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 15px;
  box-shadow: var(--shadow);
}

.panel-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  margin-bottom: 13px;
}

.hint {
  color: var(--muted);
  font-size: 12px;
}

.control-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr) auto;
  gap: 10px;
  align-items: end;
  margin-bottom: 10px;
}

.field {
  display: grid;
  gap: 6px;
}

.field span {
  color: var(--muted);
  font-size: 12px;
}

input, select, textarea {
  width: 100%;
  border: 1px solid var(--line);
  border-radius: 7px;
  background: #fff;
  color: var(--ink);
  outline: none;
}

input, select {
  height: 36px;
  padding: 0 10px;
}

textarea {
  min-height: 280px;
  resize: vertical;
  padding: 11px;
  line-height: 1.45;
}

input:focus, select:focus, textarea:focus {
  border-color: rgba(20, 122, 99, .68);
  box-shadow: 0 0 0 3px rgba(20, 122, 99, .12);
}

.check-line {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 8px;
  margin-top: 10px;
}

.group-list {
  display: grid;
  gap: 9px;
}

.group-item {
  display: grid;
  grid-template-columns: 92px minmax(0, 1fr) 92px;
  gap: 10px;
  align-items: center;
  padding: 10px 0;
  border-top: 1px solid var(--line);
}

.group-item:first-child {
  border-top: 0;
}

.bar {
  height: 8px;
  background: #edf1ec;
  border-radius: 999px;
  overflow: hidden;
}

.bar span {
  display: block;
  height: 100%;
  background: linear-gradient(90deg, var(--accent), #d69a38);
}

.downloads {
  margin-top: 14px;
}

.downloads a {
  display: inline-flex;
  align-items: center;
  height: 32px;
  padding: 0 10px;
  border-radius: 7px;
  color: var(--accent-dark);
  background: #edf6f0;
  text-decoration: none;
}

.terminal {
  min-height: 430px;
  max-height: 560px;
  overflow: auto;
  margin: 0;
  padding: 13px;
  border-radius: 8px;
  background: #1f2421;
  color: #e8eee9;
  font: 13px/1.45 ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace;
  white-space: pre-wrap;
}

#jobSelect {
  max-width: 320px;
}

.mini-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  gap: 14px;
  margin-top: 14px;
}

.mini-list {
  display: grid;
  gap: 7px;
  color: var(--muted);
  font-size: 13px;
}

.mini-list .row {
  display: flex;
  justify-content: space-between;
  gap: 10px;
  border-bottom: 1px solid #edf0ed;
  padding-bottom: 5px;
}

.table-wrap {
  overflow: auto;
}

table {
  width: 100%;
  border-collapse: collapse;
  min-width: 880px;
}

th, td {
  text-align: left;
  border-bottom: 1px solid var(--line);
  padding: 10px 8px;
  font-size: 13px;
  vertical-align: top;
}

th {
  color: var(--muted);
  font-weight: 600;
  background: var(--panel-soft);
}

td .clip {
  max-width: 320px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.codebox {
  min-height: 180px;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace;
}

.toast {
  position: fixed;
  right: 20px;
  bottom: 20px;
  max-width: min(420px, calc(100vw - 40px));
  padding: 12px 14px;
  border-radius: 8px;
  background: #202623;
  color: #fff;
  box-shadow: var(--shadow);
  font-size: 14px;
}

@media (max-width: 1060px) {
  .metrics { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .grid.two, .grid.wide-left { grid-template-columns: 1fr; }
  .topbar { align-items: flex-start; flex-direction: column; }
}

@media (max-width: 720px) {
  .shell { width: min(100vw - 20px, 1480px); padding-top: 10px; }
  .metrics { grid-template-columns: 1fr; }
  .control-grid, .check-line { grid-template-columns: 1fr; }
  .mini-grid { grid-template-columns: 1fr; }
  .panel { padding: 12px; }
  .top-actions .btn, .button-row .btn { flex: 1 1 auto; }
}
"""


JS = r"""const state = {
  selectedJobId: null,
  searchLoaded: false,
  configLoaded: false,
};

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => Array.from(document.querySelectorAll(selector));

function fmtNumber(value) {
  return Number(value || 0).toLocaleString("zh-CN");
}

function shortTime(value) {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("zh-CN", { hour12: false });
}

function showToast(message) {
  const toast = $("#toast");
  toast.textContent = message;
  toast.classList.remove("hidden");
  clearTimeout(showToast.timer);
  showToast.timer = setTimeout(() => toast.classList.add("hidden"), 2600);
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const text = await response.text();
  let payload = {};
  try {
    payload = text ? JSON.parse(text) : {};
  } catch {
    payload = { raw: text };
  }
  if (!response.ok) {
    throw new Error(payload.error || response.statusText);
  }
  return payload;
}

async function startJob(action, params = {}) {
  const payload = await api("/api/jobs", {
    method: "POST",
    body: JSON.stringify({ action, params }),
  });
  state.selectedJobId = payload.job.id;
  showToast(`已启动：${payload.job.title}`);
  await refreshJobs();
  await refreshSummary();
}

async function refreshSummary() {
  const data = await api("/api/summary");
  renderSummary(data);
}

function renderSummary(data) {
  $("#clock").textContent = `当前 ${shortTime(data.now)}`;
  const proxy = $("#proxyPill");
  proxy.textContent = data.proxy.reachable
    ? `主代理在线 ${data.proxy.host}:${data.proxy.port}`
    : `主代理未连接 ${data.proxy.host}:${data.proxy.port}`;
  proxy.className = `pill ${data.proxy.reachable ? "ok" : "warn"}`;

  $("#mUnique").textContent = fmtNumber(data.records.unique);
  $("#mEvents").textContent = `${fmtNumber(data.records.events)} 条写入记录`;
  $("#mRate").textContent = `${data.records.success_rate}%`;
  $("#mSuccess").textContent = `${fmtNumber(data.records.success)} 成功 / ${fmtNumber(data.records.failed)} 失败`;
  $("#mRejection").textContent = fmtNumber(data.business.rejection);
  $("#mFwxx").textContent = `${fmtNumber(data.business.fwxx_pending)} 待补发文`;
  $("#mDue").textContent = fmtNumber(data.business.update_due);
  $("#mTracked").textContent = `${fmtNumber(data.business.tracked_total)} 跟踪中`;
  $("#mDynamic").textContent = fmtNumber(data.lists.dynamic);
  $("#mSearch").textContent = `${fmtNumber(data.lists.search)} 输入申请号`;

  const warnings = $("#warnings");
  if (data.warnings.length) {
    warnings.textContent = data.warnings.join(" · ");
    warnings.classList.remove("hidden");
  } else {
    warnings.classList.add("hidden");
  }

  renderGroups(data.update_groups);
  renderCounts("#statusCounts", data.status_counts);
  renderCounts("#applicantCounts", data.applicant_counts);
  renderRecent(data.recent);

  if (!state.configLoaded) {
    $("#configText").value = JSON.stringify(data.config || {}, null, 2);
    state.configLoaded = true;
  }
}

function renderGroups(groups) {
  const root = $("#strategyGroups");
  if (!groups.length) {
    root.innerHTML = `<div class="hint">暂无策略配置</div>`;
    return;
  }
  const maxDue = Math.max(...groups.map((item) => item.due), 1);
  root.innerHTML = groups.map((item) => {
    const width = Math.max(3, Math.round(item.due / maxDue * 100));
    const statusText = item.statuses.join("、");
    return `<div class="group-item">
      <strong>${item.frequency_days} 天</strong>
      <div>
        <div>${statusText}</div>
        <div class="bar"><span style="width:${width}%"></span></div>
        <div class="hint">${fmtNumber(item.total)} 件，最早 ${shortTime(item.earliest)}</div>
      </div>
      <div><strong>${fmtNumber(item.due)}</strong><div class="hint">应检查</div></div>
    </div>`;
  }).join("");
}

function renderCounts(selector, rows) {
  const root = $(selector);
  if (!rows.length) {
    root.innerHTML = `<span class="hint">暂无数据</span>`;
    return;
  }
  root.innerHTML = rows.map(([name, count]) => {
    return `<div class="row"><span title="${escapeHtml(name)}">${escapeHtml(name)}</span><strong>${fmtNumber(count)}</strong></div>`;
  }).join("");
}

function renderRecent(rows) {
  const root = $("#recentRows");
  if (!rows.length) {
    root.innerHTML = `<tr><td colspan="7">暂无记录</td></tr>`;
    return;
  }
  root.innerHTML = rows.map((row) => {
    const ok = row.status_code === 200;
    return `<tr>
      <td>${escapeHtml(row.application_no || "-")}</td>
      <td><span class="pill ${ok ? "ok" : "warn"}">${escapeHtml(String(row.status_code ?? "-"))}</span></td>
      <td>${escapeHtml(row.anjianywzt || "-")}</td>
      <td><div class="clip" title="${escapeHtml(row.zhuanlimc || "")}">${escapeHtml(row.zhuanlimc || "-")}</div></td>
      <td><div class="clip" title="${escapeHtml(row.shenqingrxm || "")}">${escapeHtml(row.shenqingrxm || "-")}</div></td>
      <td>${escapeHtml(String(row.response_time_ms ?? "-"))}</td>
      <td>${shortTime(row.timestamp)}</td>
    </tr>`;
  }).join("");
}

async function refreshJobs() {
  const data = await api("/api/jobs");
  const select = $("#jobSelect");
  const jobs = data.jobs || [];
  if (!state.selectedJobId && jobs.length) {
    state.selectedJobId = jobs[0].id;
  }
  select.innerHTML = jobs.length
    ? jobs.map((job) => `<option value="${job.id}">${escapeHtml(job.title)} · ${job.status}</option>`).join("")
    : `<option value="">暂无任务</option>`;
  if (state.selectedJobId) {
    select.value = state.selectedJobId;
  }
  await refreshSelectedJob();
}

async function refreshSelectedJob() {
  if (!state.selectedJobId) {
    $("#jobLog").textContent = "等待任务启动...";
    return;
  }
  try {
    const data = await api(`/api/jobs/${state.selectedJobId}`);
    const logs = data.job.logs || [];
    $("#jobLog").textContent = logs.join("\n") || "任务暂无输出...";
    const terminal = $("#jobLog");
    terminal.scrollTop = terminal.scrollHeight;
  } catch {
    state.selectedJobId = null;
  }
}

async function loadSearchList() {
  const data = await api("/api/search-list");
  $("#searchList").value = data.text || "";
  state.searchLoaded = true;
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function bindEvents() {
  $$("[data-action]").forEach((button) => {
    button.addEventListener("click", () => startJob(button.dataset.action));
  });

  $("#runTest").addEventListener("click", () => {
    startJob("main_test", { count: $("#testCount").value });
  });

  $("#runUpdate").addEventListener("click", () => {
    startJob("main_update_dynamic", {
      file: $("#updateFile").value,
      count: $("#updateLimit").value,
    });
  });

  $("#collectFwxxTest").addEventListener("click", () => {
    startJob("collect_fwxx", { count: $("#testCount").value });
  });

  $("#generateStrategy").addEventListener("click", () => {
    startJob("strategy_generate", { frequency: $("#strategyFrequency").value });
  });

  $("#statusStrategy").addEventListener("click", () => {
    startJob("strategy_status", { frequency: $("#strategyFrequency").value });
  });

  $("#checkApp").addEventListener("click", () => {
    startJob("strategy_check", { app_no: $("#singleAppNo").value });
  });

  $("#autoPaginate").addEventListener("click", () => {
    startJob("public_auto_paginate", {
      delay: $("#pageDelay").value,
      max_pages: $("#maxPages").value,
    });
  });

  $("#jobSelect").addEventListener("change", (event) => {
    state.selectedJobId = event.target.value;
    refreshSelectedJob();
  });

  $("#stopJob").addEventListener("click", async () => {
    if (!state.selectedJobId) return;
    await api(`/api/jobs/${state.selectedJobId}/stop`, { method: "POST", body: "{}" });
    showToast("已请求停止任务");
    await refreshJobs();
  });

  $("#saveSearchList").addEventListener("click", async () => {
    await api("/api/search-list", {
      method: "POST",
      body: JSON.stringify({ text: $("#searchList").value }),
    });
    showToast("申请号列表已保存");
    await refreshSummary();
  });

  $("#saveConfig").addEventListener("click", async () => {
    await api("/api/config", {
      method: "POST",
      body: JSON.stringify({ text: $("#configText").value }),
    });
    showToast("坐标配置已保存");
    state.configLoaded = false;
    await refreshSummary();
  });

  $("#resetConfig").addEventListener("click", async () => {
    await api("/api/config/reset", {
      method: "POST",
      body: "{}",
    });
    showToast("旧坐标已备份，下次采集会重新记录");
    state.configLoaded = false;
    await refreshSummary();
  });
}

async function boot() {
  bindEvents();
  await Promise.all([refreshSummary(), refreshJobs(), loadSearchList()]);
  setInterval(refreshSummary, 5000);
  setInterval(refreshJobs, 2500);
}

boot().catch((error) => showToast(error.message));
"""


class DashboardHandler(BaseHTTPRequestHandler):
    server_version = SERVER_VERSION
    job_manager: JobManager

    def log_message(self, format: str, *args: Any) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        print(f"[{timestamp}] {self.address_string()} {format % args}")

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            if path == "/":
                self.send_text(HTML, "text/html; charset=utf-8")
            elif path == "/app.css":
                self.send_text(CSS, "text/css; charset=utf-8")
            elif path == "/app.js":
                self.send_text(JS, "application/javascript; charset=utf-8")
            elif path == "/api/summary":
                self.send_json(build_summary(self.job_manager))
            elif path == "/api/jobs":
                self.send_json({"jobs": self.job_manager.list_jobs()})
            elif path.startswith("/api/jobs/"):
                self.handle_get_job(path)
            elif path == "/api/search-list":
                self.send_json({"text": safe_read_text(SEARCH_LIST_FILE), "path": str(SEARCH_LIST_FILE)})
            elif path == "/api/config":
                self.send_json({"text": safe_read_text(CONFIG_FILE, "{}"), "path": str(CONFIG_FILE)})
            elif path.startswith("/download/"):
                self.handle_download(path)
            else:
                self.send_error(HTTPStatus.NOT_FOUND, "Not found")
        except Exception as exc:
            self.send_json({"error": str(exc)}, status=500)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            if path == "/api/jobs":
                payload = self.read_json_body()
                job = self.job_manager.start(payload.get("action", ""), payload.get("params") or {})
                self.send_json({"job": job.to_dict(include_logs=True)}, status=201)
            elif path.startswith("/api/jobs/") and path.endswith("/stop"):
                job_id = path.split("/")[3]
                ok = self.job_manager.stop(job_id)
                self.send_json({"ok": ok})
            elif path == "/api/search-list":
                payload = self.read_json_body()
                text = str(payload.get("text", ""))
                SEARCH_LIST_FILE.parent.mkdir(parents=True, exist_ok=True)
                SEARCH_LIST_FILE.write_text(text, encoding="utf-8")
                self.send_json({"ok": True, "lines": count_lines(SEARCH_LIST_FILE)})
            elif path == "/api/config":
                payload = self.read_json_body()
                text = str(payload.get("text", "{}"))
                parsed_json = json.loads(text)
                CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
                CONFIG_FILE.write_text(json.dumps(parsed_json, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                self.send_json({"ok": True})
            elif path == "/api/config/reset":
                backup = None
                if CONFIG_FILE.exists():
                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                    backup = CONFIG_FILE.with_name(f"config_backup_{timestamp}.json")
                    CONFIG_FILE.rename(backup)
                self.send_json({"ok": True, "backup": str(backup) if backup else None})
            else:
                self.send_error(HTTPStatus.NOT_FOUND, "Not found")
        except json.JSONDecodeError:
            self.send_json({"error": "JSON 格式不正确"}, status=400)
        except ValueError as exc:
            self.send_json({"error": str(exc)}, status=400)
        except Exception as exc:
            self.send_json({"error": str(exc)}, status=500)

    def handle_get_job(self, path: str) -> None:
        job_id = path.split("/")[3]
        job = self.job_manager.get_job(job_id)
        if not job:
            self.send_json({"error": "任务不存在"}, status=404)
            return
        self.send_json({"job": job.to_dict(include_logs=True)})

    def handle_download(self, path: str) -> None:
        key = path.rsplit("/", 1)[-1]
        target = DOWNLOADS.get(key)
        if not target or not target.exists():
            self.send_error(HTTPStatus.NOT_FOUND, "File not found")
            return
        content_type = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        data = target.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Content-Disposition", f'attachment; filename="{target.name}"')
        self.end_headers()
        self.wfile.write(data)

    def read_json_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length == 0:
            return {}
        body = self.rfile.read(length).decode("utf-8")
        payload = json.loads(body)
        if not isinstance(payload, dict):
            raise ValueError("请求体必须是 JSON 对象")
        return payload

    def send_text(self, text: str, content_type: str, status: int = 200) -> None:
        data = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def send_json(self, payload: dict[str, Any], status: int = 200) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)


def run_server(host: str, port: int) -> None:
    job_manager = JobManager()
    DashboardHandler.job_manager = job_manager
    server = ThreadingHTTPServer((host, port), DashboardHandler)
    print(f"{APP_NAME} 已启动: http://{host}:{port}")
    print("按 Ctrl+C 停止控制台")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n正在停止控制台...")
    finally:
        server.server_close()


def main() -> None:
    parser = argparse.ArgumentParser(description="启动 CNIPA 本地可视化控制台")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址，默认 127.0.0.1")
    parser.add_argument("--port", type=int, default=8765, help="监听端口，默认 8765")
    args = parser.parse_args()
    run_server(args.host, args.port)


if __name__ == "__main__":
    main()
