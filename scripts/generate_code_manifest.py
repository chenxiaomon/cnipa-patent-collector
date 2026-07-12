#!/usr/bin/env python3
"""Generate the hash manifest consumed by the verified HTTP updater."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = ROOT / 'release_manifest.json'
sys.path.insert(0, str(ROOT))


def tracked_release_files() -> list[Path]:
    completed = subprocess.run(
        ['git', 'ls-files', '--cached', '--others', '--exclude-standard', '-z'],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    files: list[Path] = []
    for raw_path in completed.stdout.decode('utf-8').split('\0'):
        if (
            not raw_path
            or raw_path in {MANIFEST_PATH.name, 'AGENTS.md', 'README.md'}
            or raw_path.startswith('data/')
        ):
            continue
        path = ROOT / raw_path
        if path.is_file():
            files.append(path)
    return sorted(files)


def main() -> None:
    from code_release_safety import sha256_file

    entries = [
        {'path': path.relative_to(ROOT).as_posix(), 'sha256': sha256_file(path)}
        for path in tracked_release_files()
    ]
    payload = {
        'manifest_version': 1,
        'files': entries,
    }
    temporary_path = MANIFEST_PATH.with_suffix('.tmp')
    temporary_path.write_text(json.dumps(payload, indent=2) + '\n', encoding='utf-8')
    temporary_path.replace(MANIFEST_PATH)
    print(f'[✓] 已生成 {MANIFEST_PATH}（{len(entries)} 个文件）')


if __name__ == '__main__':
    main()
