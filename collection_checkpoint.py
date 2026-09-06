"""Keep a resumable list until each application's collection is persisted."""

from pathlib import Path


class CollectionCheckpoint:
    """A failed save stops collection while retaining the previous resume list."""

    def __init__(self, checkpoint_file: Path, application_nos: list[str]):
        self._checkpoint_file = checkpoint_file
        self._pending_applications = dict.fromkeys(application_nos)
        self._save_pending(self._pending_applications)

    @property
    def remaining_count(self) -> int:
        return len(self._pending_applications)

    def record_success(self, application_no: str) -> None:
        """Remove an application only after its successful collection is committed."""
        pending_applications = self._pending_applications.copy()
        del pending_applications[application_no]
        self._save_pending(pending_applications)
        self._pending_applications = pending_applications

    def _save_pending(self, pending_applications: dict[str, None]) -> None:
        self._checkpoint_file.parent.mkdir(parents=True, exist_ok=True)
        temporary_file = self._checkpoint_file.with_suffix(
            self._checkpoint_file.suffix + '.tmp'
        )
        temporary_file.write_text(
            ''.join(f'{application_no}\n' for application_no in pending_applications),
            encoding='utf-8',
        )
        temporary_file.replace(self._checkpoint_file)
