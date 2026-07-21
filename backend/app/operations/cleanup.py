"""Safe retention cleanup confined to configured local data roots."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.config import PathSettings, RetentionSettings
from app.operations.models import CleanupResult


@dataclass(frozen=True, slots=True)
class _Candidate:
    path: Path
    size: int
    modified_at: datetime


class RetentionCleaner:
    """Delete only eligible temporary, quarantine, and raw-response files."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        paths: PathSettings,
        policy: RetentionSettings,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._connection = connection
        self._paths = paths
        self._policy = policy
        self._clock = clock

    def run(self, *, dry_run: bool) -> CleanupResult:
        before = self.storage_bytes()
        now = self._clock().astimezone(UTC)
        candidates = self._age_candidates(now)
        budget = self._policy.maximum_storage_gib * 1024**3
        projected = before - sum(item.size for item in candidates)
        if projected > budget:
            selected = {item.path for item in candidates}
            for candidate in self._raw_candidates():
                if candidate.path in selected:
                    continue
                candidates.append(candidate)
                selected.add(candidate.path)
                projected -= candidate.size
                if projected <= budget:
                    break
        selected_bytes = sum(item.size for item in candidates)
        deleted = 0
        deleted_bytes = 0
        if not dry_run:
            for candidate in candidates:
                try:
                    candidate.path.unlink(missing_ok=True)
                except OSError:
                    continue
                deleted += 1
                deleted_bytes += candidate.size
        after = before if dry_run else self.storage_bytes()
        return CleanupResult(
            dry_run=dry_run,
            files_selected=len(candidates),
            bytes_selected=selected_bytes,
            files_deleted=deleted,
            bytes_deleted=deleted_bytes,
            storage_bytes_before=before,
            storage_bytes_after=after,
            storage_budget_bytes=budget,
            budget_exceeded=after > budget,
        )

    def storage_bytes(self) -> int:
        return sum(item.size for item in self._walk_files(self._paths.data_root))

    def _age_candidates(self, now: datetime) -> list[_Candidate]:
        candidates: dict[Path, _Candidate] = {}
        temporary_cutoff = now - timedelta(hours=self._policy.temporary_file_hours)
        failed_cutoff = now - timedelta(days=self._policy.failed_artifact_days)
        raw_cutoff = now - timedelta(days=self._policy.raw_payload_days)
        for item in self._walk_files(self._paths.temporary_root):
            if item.modified_at < temporary_cutoff:
                candidates[item.path] = item
        for item in self._walk_files(self._paths.quarantine_root):
            if item.modified_at < failed_cutoff:
                candidates[item.path] = item
        for item in self._raw_candidates(before=raw_cutoff):
            candidates[item.path] = item
        return sorted(candidates.values(), key=lambda item: (item.modified_at, str(item.path)))

    def _raw_candidates(self, *, before: datetime | None = None) -> list[_Candidate]:
        query = "SELECT raw_payload_path, observed_at FROM source_records ORDER BY observed_at, id"
        candidates: list[_Candidate] = []
        for row in self._connection.execute(query).fetchall():
            observed = datetime.fromisoformat(str(row["observed_at"]).replace("Z", "+00:00"))
            if before is not None and observed.astimezone(UTC) >= before:
                continue
            path = (self._paths.data_root / str(row["raw_payload_path"])).resolve(strict=False)
            if not self._safe_file(path, self._paths.raw_documents_root):
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            candidates.append(
                _Candidate(path, stat.st_size, datetime.fromtimestamp(stat.st_mtime, UTC))
            )
        return candidates

    def _walk_files(self, root: Path) -> Iterable[_Candidate]:
        resolved_root = root.resolve(strict=False)
        if not resolved_root.is_relative_to(self._paths.data_root):
            return ()
        items: list[_Candidate] = []
        for path in resolved_root.rglob("*"):
            if not self._safe_file(path, resolved_root):
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            items.append(_Candidate(path, stat.st_size, datetime.fromtimestamp(stat.st_mtime, UTC)))
        return items

    @staticmethod
    def _safe_file(path: Path, root: Path) -> bool:
        resolved = path.resolve(strict=False)
        return (
            resolved.is_relative_to(root.resolve(strict=False))
            and path.is_file()
            and not path.is_symlink()
        )
