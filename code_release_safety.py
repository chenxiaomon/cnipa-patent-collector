#!/usr/bin/env python3
"""Verified code release installation, backup, and rollback operations."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

from settings import BASE_DIR, RELEASE_REVISION_FILE, VERSION_FILE

BACKUPS_DIR = BASE_DIR / 'backups'
_EXCLUDED_DIRECTORIES = {
    '.git', '.venv', 'venv', '__pycache__', '.pytest_cache',
    'data', 'backups', 'chromedriver-linux64', 'chromedriver-win64',
}
_EXCLUDED_FILES = {'.env', '.ds_store'}
_BACKUP_INDEX = '.code_backup_index.json'
_CALENDAR_VERSION_PATTERN = re.compile(r'([0-9]{4})\.([0-9]{2})\.([0-9]{2})')


class CodeReleaseVerificationError(RuntimeError):
    """Raised when a release or backup cannot be trusted."""


def parse_calendar_version(version_text: str) -> tuple[int, int, int]:
    match = _CALENDAR_VERSION_PATTERN.fullmatch(version_text)
    if match is None:
        raise CodeReleaseVerificationError(
            f'Invalid calendar version {version_text!r}; expected YYYY.MM.DD'
        )
    year, month, day = (int(component) for component in match.groups())
    try:
        date(year, month, day)
    except ValueError as exc:
        raise CodeReleaseVerificationError(
            f'Invalid calendar version {version_text!r}: {exc}'
        ) from exc
    return year, month, day


@dataclass(frozen=True, order=True)
class CodeReleaseVersion:
    """Keep calendar versions compatible with old installers; order same-day revisions."""
    version: str
    revision: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.version, str):
            raise CodeReleaseVerificationError('Release version must be a calendar date.')
        parse_calendar_version(self.version)
        if type(self.revision) is not int or self.revision < 0:
            raise CodeReleaseVerificationError('Release revision must be a non-negative integer.')

    def __str__(self) -> str:
        return f'{self.version} r{self.revision}'

    @classmethod
    def read(cls, project_root: Path = BASE_DIR) -> CodeReleaseVersion:
        try:
            version = (project_root / VERSION_FILE.name).read_text(encoding='utf-8').strip()
            try:
                revision = json.loads((project_root / RELEASE_REVISION_FILE.name).read_text(encoding='utf-8'))
            except FileNotFoundError:
                # Releases predating revision support are revision zero.
                revision = 0
            return cls(version, revision)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise CodeReleaseVerificationError(f'Unable to read release version from {project_root}: {exc}') from exc

    @classmethod
    def from_manifest(cls, manifest: dict) -> CodeReleaseVersion:
        release = manifest.get('release')
        if not isinstance(release, dict) or 'revision' not in release:
            raise CodeReleaseVerificationError('发布清单缺少版本或修订信息。')
        identity_paths = {entry['path'] for entry in manifest['files']}
        if not {VERSION_FILE.name, RELEASE_REVISION_FILE.name}.issubset(identity_paths):
            raise CodeReleaseVerificationError('发布清单缺少版本或修订文件。')
        return cls(release.get('version'), release['revision'])


def verify_staged_release_version(
    staging_root: Path,
    project_root: Path = BASE_DIR,
) -> None:
    """Reject a staged release whose version is missing, invalid, or older."""
    installed_version = CodeReleaseVersion.read(project_root)
    staged_version = CodeReleaseVersion.read(staging_root)
    if installed_version.revision and not (staging_root / RELEASE_REVISION_FILE.name).is_file():
        raise CodeReleaseVerificationError('发布包缺少修订文件，拒绝遗留旧修订号。')
    if staged_version < installed_version:
        raise CodeReleaseVerificationError(
            f'Refusing release downgrade: installed {installed_version}, staged {staged_version}'
        )


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
            name for name in directory_names if name.casefold() not in _EXCLUDED_DIRECTORIES
        ]
        current_directory = Path(directory)
        for file_name in file_names:
            if file_name.casefold() in _EXCLUDED_FILES or file_name.endswith(('.pyc', '.pyo')):
                continue
            owned_files.append(current_directory / file_name)
    return sorted(owned_files)


def create_code_backup(project_root: Path = BASE_DIR, backups_dir: Path = BACKUPS_DIR) -> Path:
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    backup_path = backups_dir / f'code_{timestamp}'
    backups_dir.mkdir(parents=True, exist_ok=True)
    # Only complete backups receive the code_* name used by rollback and retention.
    with tempfile.TemporaryDirectory(prefix='.code_staging_', dir=backups_dir) as staging_directory:
        staging_root = Path(staging_directory)
        file_sha256: dict[str, str] = {}
        for source_path in code_file_paths(project_root):
            relative_path = source_path.relative_to(project_root)
            destination = staging_root / relative_path
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_path, destination)
            file_sha256[relative_path.as_posix()] = sha256_file(destination)
        index_payload = {
            'created_at': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
            'files': list(file_sha256),
            'file_sha256': file_sha256,
        }
        (staging_root / _BACKUP_INDEX).write_text(
            json.dumps(index_payload, ensure_ascii=False, indent=2) + '\n',
            encoding='utf-8',
        )
        staging_root.replace(backup_path)
    return backup_path


def _safe_relative_path(raw_path: str) -> Path:
    if not isinstance(raw_path, str) or '\\' in raw_path or ':' in raw_path:
        raise CodeReleaseVerificationError(f'发布清单包含不安全路径: {raw_path!r}')
    relative_path = Path(raw_path)
    if relative_path.is_absolute() or '..' in relative_path.parts or not relative_path.parts:
        raise CodeReleaseVerificationError(f'发布清单包含不安全路径: {raw_path!r}')
    if any(part.endswith(('.', ' ')) for part in relative_path.parts):
        raise CodeReleaseVerificationError(f'发布清单包含 Windows 路径别名: {raw_path!r}')
    if relative_path.parts[0].casefold() in _EXCLUDED_DIRECTORIES or relative_path.name.casefold() in _EXCLUDED_FILES:
        raise CodeReleaseVerificationError(f'发布清单试图覆盖本机运行数据: {raw_path!r}')
    return relative_path


def validate_release_manifest(payload: dict) -> list[dict[str, str]]:
    if not isinstance(payload, dict) or payload.get('manifest_version') != 1 or not isinstance(payload.get('files'), list):
        raise CodeReleaseVerificationError('发布清单格式或版本无效。')
    verified_entries: list[dict[str, str]] = []
    seen_paths: set[str] = set()
    for entry in payload['files']:
        if not isinstance(entry, dict):
            raise CodeReleaseVerificationError('发布清单的文件条目无效。')
        relative_path = _safe_relative_path(entry.get('path', '')).as_posix()
        expected_hash = str(entry.get('sha256', '')).lower()
        if len(expected_hash) != 64 or any(char not in '0123456789abcdef' for char in expected_hash):
            raise CodeReleaseVerificationError(f'{relative_path} 的 SHA-256 无效。')
        if relative_path.casefold() in seen_paths:
            raise CodeReleaseVerificationError(f'发布清单包含重复路径: {relative_path}')
        seen_paths.add(relative_path.casefold())
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
    # Publish the installed identity after the code files, so the Dashboard
    # does not report a completed update during a partial installation.
    identity_order = {VERSION_FILE.name: 1, RELEASE_REVISION_FILE.name: 2}
    for entry in sorted(manifest_entries, key=lambda entry: identity_order.get(entry['path'], 0)):
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
    try:
        payload = json.loads(index_path.read_text(encoding='utf-8'))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CodeReleaseVerificationError(f'无法读取备份索引: {backup_path}: {exc}') from exc
    if not isinstance(payload, dict) or not isinstance(payload.get('files'), list):
        raise CodeReleaseVerificationError(f'备份文件索引格式无效: {backup_path}')
    relative_files = {_safe_relative_path(path).as_posix() for path in payload['files']}
    if not relative_files:
        raise CodeReleaseVerificationError(f'备份文件索引为空: {backup_path}')
    file_sha256 = payload.get('file_sha256')
    if not isinstance(file_sha256, dict):
        raise CodeReleaseVerificationError(
            f'备份缺少文件哈希，无法自动验证，拒绝覆盖当前代码。请人工核对旧备份: {backup_path}'
        )
    if set(file_sha256) != relative_files:
        raise CodeReleaseVerificationError(f'备份哈希索引与文件列表不一致: {backup_path}')
    for relative in sorted(relative_files):
        source_path = backup_path / relative
        if not source_path.is_file():
            raise CodeReleaseVerificationError(f'备份文件缺失: {relative}')
        if sha256_file(source_path) != file_sha256[relative]:
            raise CodeReleaseVerificationError(f'备份文件哈希不匹配: {relative}')

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
        destination = project_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = destination.with_suffix(destination.suffix + '.rollback_tmp')
        shutil.copy2(source_path, temporary_path)
        temporary_path.replace(destination)
        restored += 1
    return restored


def latest_code_backup(backups_dir: Path = BACKUPS_DIR) -> Path:
    backups = sorted(
        path for path in backups_dir.glob('code_*')
        if path.is_dir() and (path / _BACKUP_INDEX).is_file()
    )
    if not backups:
        raise CodeReleaseVerificationError(f'没有可用代码备份: {backups_dir}')
    return backups[-1]


def prune_code_backups(backups_dir: Path = BACKUPS_DIR, keep: int = 5) -> None:
    backups = sorted(path for path in backups_dir.glob('code_*') if path.is_dir())
    for expired_backup in backups[:-keep]:
        shutil.rmtree(expired_backup)
