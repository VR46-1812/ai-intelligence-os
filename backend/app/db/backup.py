"""Consistent local SQLite backup operation and command-line entry point."""

from __future__ import annotations

import argparse
import hashlib
import os
import sqlite3
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import NoReturn

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.config import initialize_directories, load_settings
from app.db.connection import SQLiteDatabase


class BackupError(RuntimeError):
    """Raised when a consistent local SQLite backup cannot be created."""


class BackupManifest(BaseModel):
    """Integrity metadata required before a local restore is accepted."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: str = Field(pattern=r"^1\.0$")
    database_file: str
    byte_size: int = Field(gt=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    migration_versions: tuple[int, ...]
    created_at: str


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def manifest_path(database_backup: Path) -> Path:
    return database_backup.with_suffix(database_backup.suffix + ".manifest.json")


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
        verification = sqlite3.connect(destination)
        try:
            versions = tuple(
                int(row[0])
                for row in verification.execute(
                    "SELECT version FROM schema_migrations ORDER BY version"
                ).fetchall()
            )
        finally:
            verification.close()
        manifest = BackupManifest(
            schema_version="1.0",
            database_file=destination.name,
            byte_size=destination.stat().st_size,
            sha256=_sha256(destination),
            migration_versions=versions,
            created_at=datetime.now(UTC).isoformat(),
        )
        manifest_destination = manifest_path(destination)
        manifest_temporary = manifest_destination.with_suffix(manifest_destination.suffix + ".tmp")
        manifest_temporary.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
        os.replace(manifest_temporary, manifest_destination)
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


def restore_database(
    backup_path: Path,
    destination: Path,
    allowed_root: Path,
    *,
    overwrite: bool = False,
) -> Path:
    """Validate a backup manifest and atomically restore a SQLite database."""
    allowed_root = allowed_root.resolve(strict=False)
    backup_path = backup_path.resolve(strict=False)
    destination = destination.resolve(strict=False)
    if not backup_path.is_relative_to(allowed_root) or not destination.is_relative_to(allowed_root):
        raise BackupError(f"Backup and restore paths must remain inside {allowed_root}")
    if not backup_path.is_file():
        raise BackupError(f"SQLite backup does not exist: {backup_path}")
    if destination == backup_path:
        raise BackupError("Restore destination cannot replace the backup")
    if destination.exists() and not overwrite:
        raise BackupError(f"Restore destination already exists: {destination}")
    metadata_path = manifest_path(backup_path)
    try:
        manifest = BackupManifest.model_validate_json(metadata_path.read_text(encoding="utf-8"))
    except (OSError, ValidationError, ValueError) as error:
        raise BackupError("Backup manifest is missing or invalid") from error
    if (
        manifest.database_file != backup_path.name
        or manifest.byte_size != backup_path.stat().st_size
        or manifest.sha256 != _sha256(backup_path)
    ):
        raise BackupError("Backup does not match its integrity manifest")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    source: sqlite3.Connection | None = None
    target: sqlite3.Connection | None = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix=f".{destination.stem}-restore-",
            suffix=".tmp",
            dir=destination.parent,
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
        source = sqlite3.connect(f"file:{backup_path.as_posix()}?mode=ro", uri=True)
        target = sqlite3.connect(temporary_path)
        source.backup(target)
        integrity = target.execute("PRAGMA integrity_check").fetchone()
        versions = tuple(
            int(row[0])
            for row in target.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            ).fetchall()
        )
        if integrity is None or str(integrity[0]).casefold() != "ok":
            raise BackupError("Restored SQLite database failed its integrity check")
        if versions != manifest.migration_versions:
            raise BackupError("Restored migration history does not match the backup manifest")
        target.close()
        target = None
        source.close()
        source = None
        if destination.exists() and not overwrite:
            raise BackupError(f"Restore destination appeared during restore: {destination}")
        os.replace(temporary_path, destination)
        temporary_path = None
        return destination
    except (OSError, sqlite3.Error) as error:
        raise BackupError(f"Could not restore SQLite backup: {error}") from error
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
    parser.add_argument(
        "--restore-from",
        type=Path,
        help="Restore a manifest-verified backup instead of creating one.",
    )
    arguments = parser.parse_args()

    settings = load_settings()
    initialize_directories(settings.paths)
    source_path = settings.paths.database_path
    if arguments.restore_from is not None:
        restore_source = (
            arguments.restore_from
            if arguments.restore_from.is_absolute()
            else settings.paths.data_root / arguments.restore_from
        )
        restore_destination = arguments.destination or source_path
        restore_destination = (
            restore_destination
            if restore_destination.is_absolute()
            else settings.paths.data_root / restore_destination
        )
        try:
            completed = restore_database(
                restore_source,
                restore_destination,
                settings.paths.data_root,
                overwrite=arguments.overwrite,
            )
        except BackupError as error:
            _fail(str(error))
        print(completed)
        return
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
