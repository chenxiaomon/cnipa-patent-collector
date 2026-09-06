#!/usr/bin/env python3
"""Generate the hash manifest consumed by the verified HTTP updater."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = ROOT / 'release_manifest.json'


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
    return sorted(files, key=lambda path: path.relative_to(ROOT).as_posix())


def release_file_sha256(path: Path) -> str:
    """Hash repository text as LF while preserving binary bytes unchanged."""
    content = path.read_bytes()
    # Git and GitHub raw downloads use LF for repository text; Windows working
    # trees may contain CRLF. Git uses the same NUL heuristic to identify binary files.
    if b'\0' not in content[:8000]:
        content = content.replace(b'\r\n', b'\n')
    return hashlib.sha256(content).hexdigest()


def main() -> None:
    entries = [
        {'path': path.relative_to(ROOT).as_posix(), 'sha256': release_file_sha256(path)}
        for path in tracked_release_files()
    ]
    payload = {
        'manifest_version': 1,
        'release': {
            'version': (ROOT / 'VERSION').read_text(encoding='utf-8').strip(),
            'revision': json.loads((ROOT / 'RELEASE_REVISION').read_text(encoding='utf-8')),
        },
        'files': entries,
    }
    temporary_path = MANIFEST_PATH.with_suffix('.tmp')
    temporary_path.write_text(json.dumps(payload, indent=2) + '\n', encoding='utf-8')
    temporary_path.replace(MANIFEST_PATH)
    print(f'[OK] 已生成 {MANIFEST_PATH}（{len(entries)} 个文件）')


if __name__ == '__main__':
    main()
