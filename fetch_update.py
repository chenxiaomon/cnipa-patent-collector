#!/usr/bin/env python3
"""Install a manifest-verified code release over the HTTP update channel."""

from __future__ import annotations

import json
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

from code_release_safety import (
    BACKUPS_DIR,
    CodeReleaseVerificationError,
    create_code_backup,
    install_staged_release,
    prune_code_backups,
    restore_code_backup,
    sha256_bytes,
    validate_release_manifest,
    verify_staged_release,
    verify_staged_release_version,
)
from settings import BASE_DIR, raw_file_urls

MANIFEST_NAME = 'release_manifest.json'


def download_release_file(relative_path: str, timeout: int = 30) -> bytes:
    failure_reasons: list[str] = []
    for url in raw_file_urls(relative_path):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as response:
                return response.read()
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
            failure_reasons.append(str(exc))
    raise CodeReleaseVerificationError(
        f'{relative_path} 无法从任何更新源下载: {failure_reasons[-1] if failure_reasons else "未知错误"}'
    )


def check_release_channel() -> None:
    payload = json.loads(download_release_file(MANIFEST_NAME).decode('utf-8'))
    entries = validate_release_manifest(payload)
    print(f'[✓] 发布通道可用，清单包含 {len(entries)} 个文件。')


def install_release(project_root: Path = BASE_DIR, backups_dir: Path = BACKUPS_DIR) -> None:
    manifest_payload = json.loads(download_release_file(MANIFEST_NAME).decode('utf-8'))
    manifest_entries = validate_release_manifest(manifest_payload)
    print(f'[release] 清单已验证，共 {len(manifest_entries)} 个代码文件。')

    backups_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='release_', dir=backups_dir) as staging_directory:
        staging_root = Path(staging_directory)
        for index, entry in enumerate(manifest_entries, 1):
            content = download_release_file(entry['path'])
            actual_hash = sha256_bytes(content)
            if actual_hash != entry['sha256']:
                raise CodeReleaseVerificationError(
                    f"{entry['path']} 哈希不匹配：期望 {entry['sha256']}，实际 {actual_hash}"
                )
            staged_path = staging_root / entry['path']
            staged_path.parent.mkdir(parents=True, exist_ok=True)
            staged_path.write_bytes(content)
            print(f"  [{index}/{len(manifest_entries)}] {entry['path']}")
        verify_staged_release(staging_root, manifest_entries)
        verify_staged_release_version(staging_root, project_root)
        backup_path = create_code_backup(project_root, backups_dir)
        print(f'[release] 当前代码已备份到 {backup_path}')
        try:
            install_staged_release(staging_root, manifest_entries, project_root)
        except (Exception, KeyboardInterrupt):
            restore_code_backup(backup_path, project_root)
            print('[rollback] 安装失败，已自动恢复更新前代码。')
            raise
    try:
        prune_code_backups(backups_dir, keep=5)
    except OSError as exc:
        print(f'[!] 代码已安装，但旧备份清理失败: {exc}')
    print('[✓] 新代码已通过完整哈希校验并安装。')
    print('如需撤销本次更新，运行: python rollback.py')


def main() -> None:
    try:
        if '--check' in sys.argv[1:]:
            check_release_channel()
        else:
            install_release()
    except (CodeReleaseVerificationError, json.JSONDecodeError, OSError) as exc:
        print(f'[✗] HTTP 代码更新失败: {exc}')
        raise SystemExit(1)


if __name__ == '__main__':
    main()
