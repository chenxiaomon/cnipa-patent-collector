#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
采集进度同步脚本

用法：
  python sync.py status   # 查看当前同步状态
  python sync.py init     # 新机器一键初始化：git pull + 重建 DB
  python sync.py rebuild  # 从现有 JSONL 重建 DB（DB 损坏/迁移/恢复用）

日常数据回流固定使用 sync_pull_from_master.py；旧 pull/push 方向命令已禁用。
"""
import shlex
import shutil

import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from settings import DETECTION_LOG_JSONL_FILE, PATENTS_DB_FILE
from db_manager import PatentsDB
from machine_identity import (
    MachineRoleConfigurationError,
    require_database_rebuild_authorization,
)

LOG_FILE = str(DETECTION_LOG_JSONL_FILE)


def _find_git() -> str:
    """返回 git 可执行文件路径；找不到时打印提示并退出。"""
    found = shutil.which('git')
    if found:
        return found

    # Windows 常见安装位置（git 未加入 PATH 时兜底）
    if sys.platform == 'win32':
        candidates = [
            r'C:\Program Files\Git\cmd\git.exe',
            r'C:\Program Files (x86)\Git\cmd\git.exe',
            r'D:\Program Files\Git\cmd\git.exe',
            os.path.join(os.environ.get('LOCALAPPDATA', ''), r'Programs\Git\cmd\git.exe'),
            os.path.join(os.environ.get('PROGRAMFILES', ''), r'Git\cmd\git.exe'),
        ]
        for path in candidates:
            if os.path.isfile(path):
                return path

    print("[✗] 找不到 git，请安装 Git 或将其加入系统 PATH")
    print("    下载地址: https://git-scm.com/download/win")
    sys.exit(1)


_GIT = _find_git()


def run(cmd: str, check: bool = True) -> subprocess.CompletedProcess:
    parts = shlex.split(cmd)
    if parts and parts[0] == 'git':
        parts[0] = _GIT
    return subprocess.run(
        parts,
        text=True,
        encoding='utf-8',
        errors='replace',
        capture_output=True,
        check=check,
    )


def record_count() -> int:
    try:
        with open(LOG_FILE, 'r', encoding='utf-8') as f:
            return sum(1 for line in f if line.strip())
    except Exception:
        return 0


def _parse_jsonl(text: str) -> dict:
    """将 JSONL 文本解析为 {application_no: record} 字典"""
    result = {}
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
            app_no = record.get('application_no')
            if app_no:
                result[app_no] = record
        except json.JSONDecodeError:
            pass
    return result


def _auto_merge_conflict() -> bool:
    """
    git pull 产生冲突时自动合并 detection_log.jsonl。
    策略：以申请号为 key 合并双方记录，timestamp 较新的优先，两边独有的都保留。
    返回 True 表示合并成功并已 git add，False 表示合并失败需人工介入。
    """
    try:
        ours_raw   = run(f'git show :2:{LOG_FILE}', check=False).stdout
        theirs_raw = run(f'git show :3:{LOG_FILE}', check=False).stdout
        our_map    = _parse_jsonl(ours_raw)
        their_map  = _parse_jsonl(theirs_raw)
    except Exception as e:
        print(f"[!] 无法解析冲突文件，需人工处理: {e}")
        return False

    merged = dict(their_map)
    for app_no, record in our_map.items():
        if app_no not in merged:
            merged[app_no] = record
        else:
            if record.get('timestamp', '') > merged[app_no].get('timestamp', ''):
                merged[app_no] = record

    tmp = LOG_FILE + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        for record in merged.values():
            f.write(json.dumps(record, ensure_ascii=False) + '\n')
    os.replace(tmp, LOG_FILE)

    run(f'git add {LOG_FILE}')
    total = len(merged)
    ours_only   = len(set(our_map) - set(their_map))
    theirs_only = len(set(their_map) - set(our_map))
    print(f"[✓] 自动合并完成：共 {total} 条（本地独有 {ours_only} 条，远端独有 {theirs_only} 条）")
    return True


def cmd_status():
    print("=" * 60)
    print("📊 同步状态")
    print("=" * 60)
    print(f"本地记录数 : {record_count()} 条")

    log = run('git log --oneline -5 data/results/detection_log.jsonl', check=False).stdout
    if log.strip():
        print("最近提交:")
        for line in log.strip().splitlines():
            print(f"  {line}")
    else:
        print("尚无提交记录")

    ahead = run('git status -sb', check=False).stdout.strip()
    print(f"git 状态  : {ahead.splitlines()[0] if ahead else '未知'}")


def cmd_init():
    """新机器一键初始化：git pull + 从 JSONL 重建 DB"""
    print("=" * 60)

    require_database_rebuild_authorization(sys.argv[2:])
    print("🆕 初始化新机器")
    print("=" * 60)

    db_path = str(PATENTS_DB_FILE)
    if os.path.exists(db_path) and os.path.getsize(db_path) > 0:
        if sys.stdin.isatty():
            ans = input(f"[!] DB 文件已存在 ({db_path})，是否覆盖重建？(y/N): ").strip().lower()
            if ans != 'y':
                print("[!] 已取消")
                sys.exit(0)
        else:
            # 非交互模式：init 不应从 Dashboard 触发（无按钮），此处保险起见拒绝执行
            print(f"[!] 非交互模式下不允许覆盖已有 DB（{db_path}）")
            print("    请在终端中手动执行：python sync.py init")
            sys.exit(1)

    print("[*] 拉取最新进度...")
    result = run('git pull', check=False)
    if result.returncode != 0:
        if 'CONFLICT' in result.stdout or 'conflict' in result.stderr:
            print("[!] 检测到合并冲突，尝试自动合并...")
            if not _auto_merge_conflict():
                print("[✗] 自动合并失败，请手动检查后重试")
                sys.exit(1)
        else:
            print(f"[!] git pull 失败，尝试使用现有 JSONL 重建...\n{result.stderr.strip()}")

    db = PatentsDB(PATENTS_DB_FILE)
    imported = db.import_from_jsonl(DETECTION_LOG_JSONL_FILE)
    print(f"[✓] 初始化完成：共导入 {imported} 条记录")
    print("现在可以开始采集了。")


def cmd_rebuild():
    """从现有 JSONL 重建 DB（用于 DB 损坏、迁移、恢复场景）"""
    print("=" * 60)

    require_database_rebuild_authorization(sys.argv[2:])
    print("🔄 从 JSONL 重建 DB")
    print("=" * 60)

    if not os.path.exists(LOG_FILE):
        print(f"[✗] JSONL 文件不存在: {LOG_FILE}")
        sys.exit(1)

    db = PatentsDB(PATENTS_DB_FILE)
    imported = db.import_from_jsonl(DETECTION_LOG_JSONL_FILE)
    print(f"[✓] 重建完成：共导入 {imported} 条记录")


if __name__ == '__main__':
    _commands = {'status': cmd_status, 'init': cmd_init, 'rebuild': cmd_rebuild}
    if len(sys.argv) >= 2 and sys.argv[1] in {'pull', 'push'}:
        print("[✗] 旧双向同步命令已禁用。replica 请运行: python sync_pull_from_master.py")
        sys.exit(2)
    if len(sys.argv) < 2 or sys.argv[1] not in _commands:
        print(__doc__)
        sys.exit(0)

    os.chdir(os.path.dirname(os.path.abspath(__file__)))

    try:
        _commands[sys.argv[1]]()
    except MachineRoleConfigurationError as exc:
        print(f"[✗] {exc}")
        sys.exit(2)
