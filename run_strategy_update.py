#!/usr/bin/env python3
"""Generate one strategy list, snapshot statuses, and run its collection."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys

from update_by_strategy import prepare_for_update, write_update_list_file


def main() -> None:
    parser = argparse.ArgumentParser(description='立即采集一个策略周期组')
    parser.add_argument('frequency', type=int, nargs='?', default=None, help='策略周期天数')
    parser.add_argument('--test', type=int, default=None, metavar='N', help='仅采集前 N 条')
    args = parser.parse_args()

    update_list_path = write_update_list_file(args.frequency)
    if update_list_path.stat().st_size == 0:
        print('[✓] 当前策略组没有到期申请号。')
        return
    prepare_for_update()
    command = [sys.executable, '-u', 'main_automation.py', '--update-list', str(update_list_path)]
    if args.test:
        command.extend(['--test', str(args.test)])
    environment = os.environ.copy()
    environment['USE_MITM_PROXY'] = 'true'
    completed = subprocess.run(command, env=environment, check=False)
    raise SystemExit(completed.returncode)


if __name__ == '__main__':
    main()
