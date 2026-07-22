"""Audited, schema-gated repair for the released migration-seven checksum incident."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import NoReturn

from app.config import initialize_directories, load_settings
from app.db.backup import backup_database, manifest_path, restore_database
from app.db.connection import SQLiteDatabase, transaction
from app.db.migrations import Migration, discover_migrations

REPAIR_MIGRATION_VERSION = 7
REPAIR_MIGRATION_NAME = "weekend_beta_agents"
REPAIR_TABLES = ("agent_executions", "linked_event_artifacts", "watchlist_inputs")
PRESERVED_TABLES = (
    "works",
    "documents",
    "evidence_spans",
    "linked_events",
    "daily_reports",
    "agent_executions",
)


class MigrationRepairError(RuntimeError):
    """Raised when a migration-history repair cannot be proven safe."""


@dataclass(frozen=True, slots=True)
class SchemaComparison:
    equivalent: bool
    differences: tuple[str, ...]
    expected_sha256: str
    actual_sha256: str


@dataclass(frozen=True, slots=True)
class PreservationRecord:
    table: str
    row_count: int
    content_sha256: str


@dataclass(frozen=True, slots=True)
class MigrationRepairResult:
    database_path: str
    database_identity_sha256: str
    old_checksum: str
    new_checksum: str
    repaired_at: str
    schema_comparison: SchemaComparison
    backup_path: str | None
    backup_sha256: str | None
    backup_verified: bool
    audit_path: str | None
    repaired: bool
    preserved_records: tuple[PreservationRecord, ...]


def _normalize_sql(sql: str | None) -> str:
    if sql is None:
        return ""
    return " ".join(sql.casefold().split())


def _rows(connection: sqlite3.Connection, statement: str) -> tuple[tuple[object, ...], ...]:
    return tuple(tuple(row) for row in connection.execute(statement).fetchall())


def _table_schema(connection: sqlite3.Connection, table: str) -> dict[str, object]:
    table_row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    if table_row is None:
        return {"missing": True}
    indexes: list[dict[str, object]] = []
    for row in connection.execute(f'PRAGMA index_list("{table}")').fetchall():
        index_name = str(row[1])
        sql_row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='index' AND name=?", (index_name,)
        ).fetchone()
        indexes.append(
            {
                "name": index_name,
                "unique": int(row[2]),
                "origin": str(row[3]),
                "partial": int(row[4]),
                "columns": _rows(connection, f'PRAGMA index_xinfo("{index_name}")'),
                "sql": _normalize_sql(None if sql_row is None else str(sql_row[0])),
            }
        )
    return {
        "sql": _normalize_sql(str(table_row[0])),
        "columns": _rows(connection, f'PRAGMA table_xinfo("{table}")'),
        "foreign_keys": _rows(connection, f'PRAGMA foreign_key_list("{table}")'),
        "indexes": tuple(sorted(indexes, key=lambda item: str(item["name"]))),
    }


def _schema_snapshot(connection: sqlite3.Connection) -> dict[str, object]:
    return {table: _table_schema(connection, table) for table in REPAIR_TABLES}


def _snapshot_digest(snapshot: dict[str, object]) -> str:
    encoded = json.dumps(snapshot, sort_keys=True, separators=(",", ":"), default=list).encode()
    return hashlib.sha256(encoded).hexdigest()


def _execute_migration(connection: sqlite3.Connection, migration: Migration) -> None:
    # executescript is safe here because the expected database is isolated and disposable.
    connection.executescript(migration.sql)


def compare_migration_7_schema(database: SQLiteDatabase) -> SchemaComparison:
    """Compare all migration-seven schema effects with an independently built schema."""
    migrations = discover_migrations()
    migration_7 = next(
        (migration for migration in migrations if migration.version == REPAIR_MIGRATION_VERSION),
        None,
    )
    if migration_7 is None or migration_7.name != REPAIR_MIGRATION_NAME:
        raise MigrationRepairError("The committed migration 7 is missing or has an unexpected name")

    expected = sqlite3.connect(":memory:")
    actual = database.connect()
    try:
        expected.execute("PRAGMA foreign_keys = ON")
        for migration in migrations[:REPAIR_MIGRATION_VERSION]:
            _execute_migration(expected, migration)
        expected_snapshot = _schema_snapshot(expected)
        actual_snapshot = _schema_snapshot(actual)
    finally:
        expected.close()
        actual.close()

    differences = tuple(
        f"{table} differs from committed migration 7"
        for table in REPAIR_TABLES
        if actual_snapshot[table] != expected_snapshot[table]
    )
    return SchemaComparison(
        equivalent=not differences,
        differences=differences,
        expected_sha256=_snapshot_digest(expected_snapshot),
        actual_sha256=_snapshot_digest(actual_snapshot),
    )


def _content_fingerprint(connection: sqlite3.Connection, table: str) -> PreservationRecord:
    columns = tuple(str(row[1]) for row in connection.execute(f'PRAGMA table_info("{table}")'))
    if not columns:
        raise MigrationRepairError(f"Required preserved table is missing: {table}")
    quoted = ", ".join(f'"{column}"' for column in columns)
    digest = hashlib.sha256()
    count = 0
    for row in connection.execute(f'SELECT {quoted} FROM "{table}" ORDER BY rowid'):
        digest.update(json.dumps(tuple(row), separators=(",", ":"), default=str).encode())
        digest.update(b"\n")
        count += 1
    return PreservationRecord(table=table, row_count=count, content_sha256=digest.hexdigest())


def _preservation_snapshot(connection: sqlite3.Connection) -> tuple[PreservationRecord, ...]:
    return tuple(_content_fingerprint(connection, table) for table in PRESERVED_TABLES)


def _validate_other_history(
    connection: sqlite3.Connection, migrations: tuple[Migration, ...]
) -> tuple[str, str]:
    records = connection.execute(
        "SELECT version, name, checksum FROM schema_migrations ORDER BY version"
    ).fetchall()
    available = {migration.version: migration for migration in migrations}
    old_checksum = ""
    for row in records:
        version, name, checksum = int(row[0]), str(row[1]), str(row[2])
        migration = available.get(version)
        if migration is None:
            raise MigrationRepairError(f"Database contains unknown migration version {version}")
        if name != migration.name:
            raise MigrationRepairError(f"Migration {version} name drift remains blocked")
        if version == REPAIR_MIGRATION_VERSION:
            if name != REPAIR_MIGRATION_NAME:
                raise MigrationRepairError("Migration 7 has an unexpected name")
            old_checksum = checksum
        elif not migration.accepts_checksum(checksum):
            raise MigrationRepairError(f"Migration {version} checksum drift remains blocked")
    if not old_checksum:
        raise MigrationRepairError("Migration 7 has not been recorded in this database")
    return old_checksum, available[REPAIR_MIGRATION_VERSION].checksum


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_audit(path: Path, result: MigrationRepairResult) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(asdict(result), indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def repair_migration_7_checksum(
    database: SQLiteDatabase,
    data_root: Path,
    *,
    apply: bool,
) -> MigrationRepairResult:
    """Repair only migration 7 after backup, full schema proof, and history validation."""
    data_root = data_root.resolve(strict=False)
    database_path = database.path.resolve(strict=True)
    if not database_path.is_relative_to(data_root):
        raise MigrationRepairError("Configured database must remain inside the data root")
    migrations = discover_migrations()
    connection = database.connect()
    try:
        old_checksum, new_checksum = _validate_other_history(connection, migrations)
        preserved_before = _preservation_snapshot(connection)
    finally:
        connection.close()
    comparison = compare_migration_7_schema(database)
    if not comparison.equivalent:
        details = "; ".join(comparison.differences)
        raise MigrationRepairError(
            f"Migration 7 schema is not equivalent; repair refused: {details}"
        )
    identity = hashlib.sha256(
        f"{database_path}|{comparison.actual_sha256}|{old_checksum}".encode()
    ).hexdigest()
    timestamp = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    if old_checksum == new_checksum or not apply:
        return MigrationRepairResult(
            database_path=str(database_path),
            database_identity_sha256=identity,
            old_checksum=old_checksum,
            new_checksum=new_checksum,
            repaired_at=timestamp,
            schema_comparison=comparison,
            backup_path=None,
            backup_sha256=None,
            backup_verified=False,
            audit_path=None,
            repaired=False,
            preserved_records=preserved_before,
        )

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    backup_path = data_root / "backups" / f"migration7-checksum-repair-{stamp}.db"
    restored_path = data_root / "temporary" / f"migration7-checksum-repair-verify-{stamp}.db"
    audit_path = data_root / "audit" / f"migration7-checksum-repair-{stamp}.json"
    backup_database(database, backup_path, data_root)
    restore_database(backup_path, restored_path, data_root)
    restored_path.unlink(missing_ok=True)
    backup_sha256 = _sha256(backup_path)
    manifest = json.loads(manifest_path(backup_path).read_text(encoding="utf-8"))
    if manifest.get("sha256") != backup_sha256:
        raise MigrationRepairError("Verified backup checksum does not match its manifest")

    connection = database.connect()
    try:
        with transaction(connection, "IMMEDIATE"):
            current_old, current_new = _validate_other_history(connection, migrations)
            if current_old != old_checksum or current_new != new_checksum:
                raise MigrationRepairError("Migration history changed during repair")
            updated = connection.execute(
                """
                UPDATE schema_migrations SET checksum = ?
                WHERE version = ? AND name = ? AND checksum = ?
                """,
                (new_checksum, REPAIR_MIGRATION_VERSION, REPAIR_MIGRATION_NAME, old_checksum),
            )
            if updated.rowcount != 1:
                raise MigrationRepairError("Migration 7 history changed during repair")
    finally:
        connection.close()

    connection = database.connect()
    try:
        repaired_old, repaired_new = _validate_other_history(connection, migrations)
        preserved_after = _preservation_snapshot(connection)
    finally:
        connection.close()
    if repaired_old != repaired_new or preserved_after != preserved_before:
        raise MigrationRepairError("Post-repair verification failed")
    final_comparison = compare_migration_7_schema(database)
    if not final_comparison.equivalent:
        raise MigrationRepairError("Post-repair schema verification failed")
    result = MigrationRepairResult(
        database_path=str(database_path),
        database_identity_sha256=identity,
        old_checksum=old_checksum,
        new_checksum=new_checksum,
        repaired_at=timestamp,
        schema_comparison=final_comparison,
        backup_path=str(backup_path),
        backup_sha256=backup_sha256,
        backup_verified=True,
        audit_path=str(audit_path),
        repaired=True,
        preserved_records=preserved_after,
    )
    _write_audit(audit_path, result)
    return result


def _fail(message: str) -> NoReturn:
    raise SystemExit(message)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Verify or repair the known migration-7 checksum incident."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply the checksum repair after backup and semantic verification.",
    )
    arguments = parser.parse_args()
    settings = load_settings()
    initialize_directories(settings.paths)
    database = SQLiteDatabase(settings.paths.database_path, settings.database.busy_timeout_ms)
    try:
        result = repair_migration_7_checksum(
            database,
            settings.paths.data_root,
            apply=arguments.apply,
        )
    except (MigrationRepairError, OSError, sqlite3.Error) as error:
        _fail(str(error))
    print(json.dumps(asdict(result), indent=2))


if __name__ == "__main__":
    main()
