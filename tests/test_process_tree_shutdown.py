"""Shutdown owns only the task's verified process groups, including descendants."""

from __future__ import annotations

import json
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import collection_watchdog


@unittest.skipIf(sys.platform == 'win32', 'POSIX process ownership and signals')
class ProcessTreeShutdownTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory(prefix='cnipa-shutdown-')
        self.addCleanup(self.temporary_directory.cleanup)
        self.scratch_directory = Path(self.temporary_directory.name)

    def start_task_tree(self, root_script: str):
        ready_path = self.scratch_directory / 'leaf-ready'
        term_path = self.scratch_directory / 'leaf-term'
        task = subprocess.Popen(
            [sys.executable, '-u', '-c', root_script, str(ready_path), str(term_path)],
            start_new_session=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True,
        )
        owned_test_groups = {task.pid}

        def reap_fixture():
            for group_id in owned_test_groups:
                try:
                    os.killpg(group_id, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            task.wait(timeout=5)
            task.stdout.close()
            task.stderr.close()

        self.addCleanup(reap_fixture)
        self.assertTrue(select.select([task.stdout], [], [], 5)[0], 'fixture did not become ready')
        descendant_pids = json.loads(task.stdout.readline())
        for pid in descendant_pids.values():
            owned_test_groups.add(os.getpgid(pid))
        return task, descendant_pids, term_path

    def assert_pid_stopped(self, pid):
        listing = subprocess.run(
            ['ps', '-p', str(pid), '-o', 'stat='],
            capture_output=True, text=True, check=False,
        )
        self.assertTrue(
            not listing.stdout.strip() or listing.stdout.strip().startswith('Z'),
            f'fixture PID {pid} remains alive: {listing.stdout.strip()}',
        )

    def test_root_exits_first_but_same_group_leaf_is_killed_and_pipe_closes(self):
        leaf_script = '''
import os, signal, sys, time
from pathlib import Path
signal.signal(signal.SIGTERM, lambda signum, frame: Path(sys.argv[2]).write_text('received'))
ready_path = Path(sys.argv[1])
ready_path.with_suffix('.tmp').write_text(str(os.getpid()))
ready_path.with_suffix('.tmp').replace(ready_path)
time.sleep(60)
'''
        child_script = f'''
import subprocess, sys, time
subprocess.Popen([sys.executable, '-u', '-c', {leaf_script!r}, *sys.argv[1:]])
time.sleep(60)
'''
        root_script = f'''
import json, subprocess, sys, time
from pathlib import Path
child = subprocess.Popen([sys.executable, '-u', '-c', {child_script!r}, *sys.argv[1:]])
ready_path = Path(sys.argv[1])
while not ready_path.exists():
    time.sleep(0.01)
print(json.dumps({{'child_pid': child.pid, 'leaf_pid': int(ready_path.read_text())}}), flush=True)
time.sleep(60)
'''
        task, descendant_pids, term_path = self.start_task_tree(root_script)
        monotonic_clock = time.monotonic
        clock_start = monotonic_clock()

        # Accelerate the production grace deadline without replacing real
        # process discovery/signals or making the fixture wait eight seconds.
        with patch.object(
            collection_watchdog.time, 'monotonic',
            side_effect=lambda: clock_start + (monotonic_clock() - clock_start) * 20,
        ):
            collection_watchdog.terminate_process_tree(task)

        self.assertIsNotNone(task.returncode)
        self.assertTrue(term_path.exists())
        self.assert_pid_stopped(descendant_pids['child_pid'])
        self.assert_pid_stopped(descendant_pids['leaf_pid'])
        task.communicate(timeout=3)

    def test_separate_descendant_session_is_stopped_and_unrelated_task_survives(self):
        leaf_script = '''
import os, signal, sys, time
from pathlib import Path
def on_term(signum, frame):
    Path(sys.argv[2]).write_text('received')
    raise SystemExit(0)
signal.signal(signal.SIGTERM, on_term)
ready_path = Path(sys.argv[1])
ready_path.with_suffix('.tmp').write_text(str(os.getpid()))
ready_path.with_suffix('.tmp').replace(ready_path)
time.sleep(60)
'''
        child_script = f'''
import subprocess, sys, time
subprocess.Popen([sys.executable, '-u', '-c', {leaf_script!r}, *sys.argv[1:]])
time.sleep(60)
'''
        root_script = f'''
import json, subprocess, sys, time
from pathlib import Path
child = subprocess.Popen(
    [sys.executable, '-u', '-c', {child_script!r}, *sys.argv[1:]], start_new_session=True,
)
ready_path = Path(sys.argv[1])
while not ready_path.exists():
    time.sleep(0.01)
print(json.dumps({{'child_pid': child.pid, 'leaf_pid': int(ready_path.read_text())}}), flush=True)
time.sleep(60)
'''
        task, descendant_pids, term_path = self.start_task_tree(root_script)
        unrelated = subprocess.Popen(
            [sys.executable, '-c', 'import time; time.sleep(60)'],
            start_new_session=True,
        )

        def reap_unrelated_fixture():
            unrelated.terminate()
            unrelated.wait(timeout=5)

        self.addCleanup(reap_unrelated_fixture)
        collection_watchdog.terminate_process_tree(task)

        self.assertTrue(term_path.exists())
        self.assert_pid_stopped(descendant_pids['child_pid'])
        self.assert_pid_stopped(descendant_pids['leaf_pid'])
        self.assertIsNone(unrelated.poll())
        task.communicate(timeout=3)

    def test_unisolated_task_cannot_authorize_signalling_our_own_group(self):
        task = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])

        def reap_unisolated_fixture():
            task.terminate()
            task.wait(timeout=5)

        self.addCleanup(reap_unisolated_fixture)
        with self.assertRaisesRegex(RuntimeError, '独立进程组'):
            collection_watchdog.terminate_process_tree(task)
        self.assertIsNone(task.poll())

    def test_reaped_task_does_not_authorize_new_process_discovery_or_signals(self):
        task = subprocess.Popen([sys.executable, '-c', 'pass'], start_new_session=True)
        task.wait(timeout=5)
        with (
            patch.object(collection_watchdog.subprocess, 'run') as discover,
            patch.object(collection_watchdog.os, 'killpg') as signal_group,
        ):
            collection_watchdog.terminate_process_tree(task)
        discover.assert_not_called()
        signal_group.assert_not_called()


if __name__ == '__main__':
    unittest.main()
