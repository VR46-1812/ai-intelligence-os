"""Consistent local SQLite backup operation and command-line entry point."""

from __future__ import annotations

import argparse
import os
import sqlite3
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import NoReturn

from app.config import initialize_directories, load_settings
from app.db.connection import SQLiteDatabase


class BackupError(RuntimeError):
    """Raised when a consistent local SQLite backup cannot be created."""


def backup_database(
    database: SQLiteDatabase,
    destination: Path,
    allowed_root: Path,
    *,
    overwrite: bool = False,
) -> Path:
    """Create and verify an atomic SQLite online backup."""
    allowed_root = allowed_root.resolve(strict=False)
    destination = destination.resolve(strict=False)
    source_path = database.path.resolve(strict=False)
    if not source_path.is_file():
        raise BackupError(f"SQLite backup source does not exist: {source_path}")
    if not destination.is_relative_to(allowed_root):
        raise BackupError(f"Backup destination must remain inside {allowed_root}")
    if destination == source_path:
        raise BackupError("Backup destination cannot replace the active database")
    if destination.suffix.lower() not in {".db", ".sqlite3"}:
        raise BackupError("Backup destination must use a .db or .sqlite3 extension")
    if destination.exists() and not overwrite:
        raise BackupError(f"Backup destination already exists: {destination}")
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise BackupError(
            f"Could not initialize backup directory {destination.parent}: {error}"
        ) from error

    temporary_path: Path | None = None
    source: sqlite3.Connection | None = None
    target: sqlite3.Connection | None = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix=f".{destination.stem}-",
            suffix=".tmp",
            dir=destination.parent,
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)

        source = database.connect()
        target = sqlite3.connect(temporary_path)
        source.backup(target)
        integrity_row = target.execute("PRAGMA integrity_check").fetchone()
        if integrity_row is None or str(integrity_row[0]).lower() != "ok":
            raise BackupError("SQLite backup failed its integrity check")
        target.close()
        target = None
        source.close()
        source = None
        if destination.exists() and not overwrite:
            raise BackupError(f"Backup destination appeared during backup: {destination}")
        os.replace(temporary_path, destination)
        temporary_path = None
        return destination
    except (OSError, sqlite3.Error) as error:
        raise BackupError(f"Could not create SQLite backup at {destination}: {error}") from error
    finally:
        if target is not None:
            target.close()
        if source is not None:
            source.close()
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _default_backup_path(data_root: Path, database_path: Path) -> Path:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return data_root / "backups" / f"{database_path.stem}-{timestamp}.db"


def _fail(message: str) -> NoReturn:
    raise SystemExit(message)


def main() -> None:
    """Back up the configured database to a safe path under data_root."""
    parser = argparse.ArgumentParser(description="Create a consistent local SQLite backup.")
    parser.add_argument(
        "--destination",
        type=Path,
        help="Backup path under the configured data root.",
    )
    parser.add_argument("--overwrite", action="store_true", help="Replace an existing backup file.")
    arguments = parser.parse_args()

    settings = load_settings()
    initialize_directories(settings.paths)
    source_path = settings.paths.database_path
    if not source_path.is_file():
        _fail(f"Configured SQLite database does not exist: {source_path}")

    destination = arguments.destination or _default_backup_path(
        settings.paths.data_root,
        source_path,
    )
    resolved_destination = (
        destination if destination.is_absolute() else settings.paths.data_root / destination
    ).resolve(strict=False)
    if not resolved_destination.is_relative_to(settings.paths.data_root):
        _fail(f"Backup destination must remain inside {settings.paths.data_root}")
    if resolved_destination == source_path:
        _fail("Backup destination cannot replace the active database")

    database = SQLiteDatabase(source_path, settings.database.busy_timeout_ms)
    try:
        completed_path = backup_database(
            database,
            resolved_destination,
            settings.paths.data_root,
            overwrite=arguments.overwrite,
        )
    except BackupError as error:
        _fail(str(error))
    print(completed_path)


if __name__ == "__main__":
    main()
