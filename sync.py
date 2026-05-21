#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
采集进度同步脚本

用法：
  python sync.py pull   # 采集前：拉取最新进度（B 电脑接着 A 的进度）
  python sync.py push   # 采集后：上传本次进度
  python sync.py status # 查看当前同步状态
"""
import shlex

import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from settings import DETECTION_LOG_JSONL_FILE

LOG_FILE = str(DETECTION_LOG_JSONL_FILE)


def run(cmd: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(shlex.split(cmd), text=True,
                          capture_output=True, check=check)


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


def cmd_pull():
    print("=" * 60)
    print("⬇  拉取最新采集进度")
    print("=" * 60)

    before = record_count()

    # 检查本地是否有未提交的修改
    dirty = run('git status --porcelain data/results/detection_log.jsonl').stdout.strip()
    if dirty:
        print(f"[!] 本地有未提交的修改（{before} 条），先提交再拉取")
        ans = input("    是否先提交本地数据？(y/N): ").strip().lower()
        if ans == 'y':
            cmd_push()
        else:
            print("[!] 已取消，请手动处理后重试")
            sys.exit(1)

    result = run('git pull', check=False)
    if result.returncode != 0:
        if 'CONFLICT' in result.stdout or 'conflict' in result.stderr:
            print("[!] 检测到合并冲突，尝试自动合并...")
            if not _auto_merge_conflict():
                print("[✗] 自动合并失败，请手动检查 detection_log.jsonl")
                sys.exit(1)
        else:
            print(f"[✗] pull 失败（网络或权限问题）:\n{result.stderr.strip()}")
            sys.exit(1)

    after = record_count()
    print(f"[✓] 同步完成：{before} → {after} 条（新增 {after - before} 条）")
    print("现在可以开始采集了。")


def cmd_push():
    print("=" * 60)
    print("⬆  上传本次采集进度")
    print("=" * 60)

    count = record_count()
    if count == 0:
        print("[!] 日志为空，跳过上传")
        return

    # 先 pull 合并远端（防止冲突）
    print("[*] 先拉取远端最新版本...")
    pull = run('git pull', check=False)
    if pull.returncode != 0:
        if 'CONFLICT' in pull.stdout or 'conflict' in pull.stderr:
            print("[!] 检测到合并冲突，尝试自动合并...")
            if not _auto_merge_conflict():
                print("[✗] 自动合并失败，请手动检查 detection_log.jsonl")
                sys.exit(1)
        else:
            print(f"[✗] pull 失败（网络或权限问题）:\n{pull.stderr.strip()}")
            sys.exit(1)

    after_pull = record_count()

    run(f'git add {LOG_FILE}')

    # 检查是否有实质变化
    diff = run('git diff --cached --stat').stdout.strip()
    if not diff:
        print("[✓] 无变化，无需提交")
        return

    msg = f"sync: update detection_log ({after_pull} records)"
    run(f'git commit -m "{msg}"')

    push = run('git push', check=False)
    if push.returncode != 0:
        print(f"[✗] push 失败:\n{push.stderr}")
        sys.exit(1)

    print(f"[✓] 上传完成：共 {after_pull} 条记录")


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


if __name__ == '__main__':
    if len(sys.argv) < 2 or sys.argv[1] not in ('pull', 'push', 'status'):
        print(__doc__)
        sys.exit(0)

    os.chdir(os.path.dirname(os.path.abspath(__file__)))

    {'pull': cmd_pull, 'push': cmd_push, 'status': cmd_status}[sys.argv[1]]()
