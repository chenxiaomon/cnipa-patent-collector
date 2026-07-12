#!/usr/bin/env python3
"""Verified code release installation, backup, and rollback operations."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

from settings import BASE_DIR

BACKUPS_DIR = BASE_DIR / 'backups'
_EXCLUDED_DIRECTORIES = {
    '.git', '.venv', 'venv', '__pycache__', '.pytest_cache',
    'data', 'backups', 'chromedriver-linux64', 'chromedriver-win64',
}
_EXCLUDED_FILES = {'.env', '.DS_Store'}
_BACKUP_INDEX = '.code_backup_index.json'


class CodeReleaseVerificationError(RuntimeError):
    """Raised when a release or backup cannot be trusted."""


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def code_file_paths(project_root: Path = BASE_DIR) -> list[Path]:
    """Return the project files owned by code backup and rollback."""
    owned_files: list[Path] = []
    for directory, directory_names, file_names in os.walk(project_root):
        directory_names[:] = [
            name for name in directory_names if name not in _EXCLUDED_DIRECTORIES
        ]
        current_directory = Path(directory)
        for file_name in file_names:
            if file_name in _EXCLUDED_FILES or file_name.endswith(('.pyc', '.pyo')):
                continue
            owned_files.append(current_directory / file_name)
    return sorted(owned_files)


def create_code_backup(project_root: Path = BASE_DIR, backups_dir: Path = BACKUPS_DIR) -> Path:
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    backup_path = backups_dir / f'code_{timestamp}'
    backup_path.mkdir(parents=True, exist_ok=False)
    relative_files: list[str] = []
    for source_path in code_file_paths(project_root):
        relative_path = source_path.relative_to(project_root)
        destination = backup_path / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, destination)
        relative_files.append(relative_path.as_posix())
    index_payload = {
        'created_at': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
        'files': relative_files,
    }
    (backup_path / _BACKUP_INDEX).write_text(
        json.dumps(index_payload, ensure_ascii=False, indent=2) + '\n',
        encoding='utf-8',
    )
    return backup_path


def _safe_relative_path(raw_path: str) -> Path:
    relative_path = Path(raw_path)
    if relative_path.is_absolute() or '..' in relative_path.parts or not relative_path.parts:
        raise CodeReleaseVerificationError(f'发布清单包含不安全路径: {raw_path!r}')
    if relative_path.parts[0] in _EXCLUDED_DIRECTORIES or relative_path.name in _EXCLUDED_FILES:
        raise CodeReleaseVerificationError(f'发布清单试图覆盖本机运行数据: {raw_path!r}')
    return relative_path


def validate_release_manifest(payload: dict) -> list[dict[str, str]]:
    if payload.get('manifest_version') != 1 or not isinstance(payload.get('files'), list):
        raise CodeReleaseVerificationError('发布清单格式或版本无效。')
    verified_entries: list[dict[str, str]] = []
    seen_paths: set[str] = set()
    for entry in payload['files']:
        if not isinstance(entry, dict):
            raise CodeReleaseVerificationError('发布清单的文件条目无效。')
        relative_path = _safe_relative_path(str(entry.get('path', ''))).as_posix()
        expected_hash = str(entry.get('sha256', '')).lower()
        if len(expected_hash) != 64 or any(char not in '0123456789abcdef' for char in expected_hash):
            raise CodeReleaseVerificationError(f'{relative_path} 的 SHA-256 无效。')
        if relative_path in seen_paths:
            raise CodeReleaseVerificationError(f'发布清单包含重复路径: {relative_path}')
        seen_paths.add(relative_path)
        verified_entries.append({'path': relative_path, 'sha256': expected_hash})
    if not verified_entries:
        raise CodeReleaseVerificationError('发布清单不包含任何代码文件。')
    return verified_entries


def verify_staged_release(staging_root: Path, manifest_entries: list[dict[str, str]]) -> None:
    for entry in manifest_entries:
        staged_path = staging_root / entry['path']
        if not staged_path.is_file():
            raise CodeReleaseVerificationError(f"发布包缺少文件: {entry['path']}")
        actual_hash = sha256_file(staged_path)
        if actual_hash != entry['sha256']:
            raise CodeReleaseVerificationError(
                f"{entry['path']} 哈希不匹配：期望 {entry['sha256']}，实际 {actual_hash}"
            )


def install_staged_release(
    staging_root: Path,
    manifest_entries: list[dict[str, str]],
    project_root: Path = BASE_DIR,
) -> None:
    for entry in manifest_entries:
        source_path = staging_root / entry['path']
        destination = project_root / entry['path']
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = destination.with_suffix(destination.suffix + '.update_tmp')
        shutil.copy2(source_path, temporary_path)
        temporary_path.replace(destination)


def restore_code_backup(backup_path: Path, project_root: Path = BASE_DIR) -> int:
    index_path = backup_path / _BACKUP_INDEX
    if not index_path.exists():
        raise CodeReleaseVerificationError(f'备份缺少 {_BACKUP_INDEX}: {backup_path}')
    payload = json.loads(index_path.read_text(encoding='utf-8'))
    relative_files = {_safe_relative_path(path).as_posix() for path in payload.get('files', [])}
    if not relative_files:
        raise CodeReleaseVerificationError(f'备份文件索引为空: {backup_path}')

    current_files = {
        path.relative_to(project_root).as_posix()
        for path in code_file_paths(project_root)
    }
    for stale_relative in sorted(current_files - relative_files):
        stale_path = project_root / stale_relative
        stale_path.unlink(missing_ok=True)

    restored = 0
    for relative in sorted(relative_files):
        source_path = backup_path / relative
        if not source_path.is_file():
            raise CodeReleaseVerificationError(f'备份文件缺失: {relative}')
        destination = project_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = destination.with_suffix(destination.suffix + '.rollback_tmp')
        shutil.copy2(source_path, temporary_path)
        temporary_path.replace(destination)
        restored += 1
    return restored


def latest_code_backup(backups_dir: Path = BACKUPS_DIR) -> Path:
    backups = sorted(path for path in backups_dir.glob('code_*') if path.is_dir())
    if not backups:
        raise CodeReleaseVerificationError(f'没有可用代码备份: {backups_dir}')
    return backups[-1]


def prune_code_backups(backups_dir: Path = BACKUPS_DIR, keep: int = 5) -> None:
    backups = sorted(path for path in backups_dir.glob('code_*') if path.is_dir())
    for expired_backup in backups[:-keep]:
        shutil.rmtree(expired_backup)
