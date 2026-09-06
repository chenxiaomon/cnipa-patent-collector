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
import shutil

import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from settings import BASE_DIR, DETECTION_LOG_JSONL_FILE, PATENTS_DB_FILE
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


def run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
    parts = list(cmd)
    if parts and parts[0] == 'git':
        parts[0] = _GIT
    return subprocess.run(
        parts,
        cwd=BASE_DIR,
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


def cmd_status():
    print("=" * 60)
    print("📊 同步状态")
    print("=" * 60)
    print(f"本地记录数 : {record_count()} 条")

    log = run(
        ['git', 'log', '--oneline', '-5', '--',
         DETECTION_LOG_JSONL_FILE.relative_to(BASE_DIR).as_posix()],
        check=False,
    ).stdout
    if log.strip():
        print("最近提交:")
        for line in log.strip().splitlines():
            print(f"  {line}")
    else:
        print("尚无提交记录")

    ahead = run(['git', 'status', '-sb'], check=False).stdout.strip()
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
    # 基础状态和费用使用不同的快照时间，Git 合并无法确定整条记录的权威版本。
    pull_attempt = run(['git', 'pull', '--ff-only'], check=False)
    if pull_attempt.returncode != 0:
        print(f"[✗] git pull 失败，未导入数据库。\n{pull_attempt.stderr.strip()}")
        print("    请先处理 Git 状态后重试；仅从已核实的本地备份恢复请运行 python sync.py rebuild。")
        sys.exit(1)

    if not DETECTION_LOG_JSONL_FILE.is_file():
        print(f"[✗] JSONL 文件不存在，未导入数据库: {DETECTION_LOG_JSONL_FILE}")
        sys.exit(1)

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
    except ValueError as exc:
        print(f"[✗] {exc}")
        sys.exit(1)
