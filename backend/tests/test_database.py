"""SQLite connection, migration, transaction, constraint, and backup tests."""

from __future__ import annotations

import shutil
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import pytest

from app.config import REPOSITORY_ROOT
from app.db.backup import BackupError, backup_database
from app.db.connection import DatabaseConnectionError, SQLiteDatabase, transaction
from app.db.migrations import (
    DEFAULT_MIGRATIONS_DIRECTORY,
    MigrationError,
    MigrationRunner,
)

FIXTURES_DIRECTORY = Path(__file__).with_name("fixtures")


@pytest.fixture
def database_path() -> Iterator[Path]:
    """Create an isolated database location inside the ignored repository data root."""
    root = REPOSITORY_ROOT / "data" / ".test-sqlite" / uuid4().hex
    root.mkdir(parents=True)
    try:
        yield root / "test.db"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _database(path: Path, busy_timeout_ms: int = 5000) -> SQLiteDatabase:
    return SQLiteDatabase(path=path, busy_timeout_ms=busy_timeout_ms)


def _migrate(path: Path, busy_timeout_ms: int = 5000) -> SQLiteDatabase:
    database = _database(path, busy_timeout_ms)
    MigrationRunner(database).migrate()
    return database


def _insert_source(connection: sqlite3.Connection, source_id: str = "source-1") -> None:
    connection.execute(
        """
        INSERT INTO sources(
          id, source_key, display_name, trust_tier, base_url,
          poll_interval_minutes, connector_version, created_at, updated_at
        ) VALUES (?, ?, ?, 'A', 'https://example.test', 60, 'fixture-v1', ?, ?)
        """,
        (
            source_id,
            f"key-{source_id}",
            f"Source {source_id}",
            "2026-07-17T00:00:00Z",
            "2026-07-17T00:00:00Z",
        ),
    )


def test_numbered_initial_migration_matches_contract() -> None:
    """The immutable version-one migration is sourced from the approved SQL contract."""
    contract_lines = (
        (REPOSITORY_ROOT / "contracts" / "schema.sql").read_text(encoding="utf-8").splitlines()
    )
    migration_lines = (
        (DEFAULT_MIGRATIONS_DIRECTORY / "0001_initial.sql").read_text(encoding="utf-8").splitlines()
    )

    assert migration_lines == contract_lines


def test_new_database_migrates_with_required_sqlite_features(database_path: Path) -> None:
    """A clean file receives version one, WAL, foreign keys, busy timeout, and FTS5."""
    database = _database(database_path, busy_timeout_ms=3500)

    applied = MigrationRunner(database).migrate()

    assert [record.version for record in applied] == [1]
    connection = database.connect()
    try:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert connection.execute("PRAGMA busy_timeout").fetchone()[0] == 3500
        migration_row = connection.execute(
            "SELECT version, name, length(checksum) FROM schema_migrations"
        ).fetchone()
        assert tuple(migration_row) == (1, "initial", 64)
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
            )
        }
        assert {"sources", "works", "evidence_spans", "knowledge_fts"} <= tables

        connection.execute(
            """
            INSERT INTO knowledge_fts(entity_type, entity_id, title, body, topics)
            VALUES ('work', 'work-1', 'Agent Memory', 'Local retrieval memory', 'agents')
            """
        )
        fts_row = connection.execute(
            "SELECT entity_id FROM knowledge_fts WHERE knowledge_fts MATCH 'retrieval'"
        ).fetchone()
        assert fts_row[0] == "work-1"
    finally:
        connection.close()


def test_repeated_migration_is_idempotent(database_path: Path) -> None:
    """Reapplying the same migration set creates no work or duplicate history."""
    database = _database(database_path)
    runner = MigrationRunner(database)

    first = runner.migrate()
    second = runner.migrate()

    assert len(first) == 1
    assert second == ()
    connection = database.connect()
    try:
        assert connection.execute("SELECT count(*) FROM schema_migrations").fetchone()[0] == 1
    finally:
        connection.close()


def test_applied_migration_checksum_drift_is_rejected(database_path: Path) -> None:
    """An already-applied migration cannot be silently edited or replaced."""
    database = _migrate(database_path)
    connection = database.connect()
    try:
        connection.execute("UPDATE schema_migrations SET checksum = 'changed' WHERE version = 1")
    finally:
        connection.close()

    with pytest.raises(MigrationError, match="checksum drift"):
        MigrationRunner(database).migrate()


def test_failed_migration_rolls_back_schema_and_history(database_path: Path) -> None:
    """Malformed SQL cannot leave a partially applied schema or migration record."""
    migrations_directory = database_path.parent / "invalid-migrations"
    migrations_directory.mkdir()
    (migrations_directory / "0001_invalid.sql").write_text(
        "CREATE TABLE should_roll_back (id INTEGER PRIMARY KEY);\nCREATE TABLE incomplete (\n",
        encoding="utf-8",
    )
    database = _database(database_path)

    with pytest.raises(MigrationError, match="Failed to apply migration 1"):
        MigrationRunner(database, migrations_directory).migrate()

    connection = database.connect()
    try:
        assert (
            connection.execute(
                "SELECT count(*) FROM sqlite_master WHERE name = 'should_roll_back'"
            ).fetchone()[0]
            == 0
        )
        assert connection.execute("SELECT count(*) FROM schema_migrations").fetchone()[0] == 0
    finally:
        connection.close()


def test_foreign_key_violation_fails(database_path: Path) -> None:
    """Every configured connection enforces parent-child integrity."""
    database = _migrate(database_path)
    connection = database.connect()
    try:
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY constraint failed"):
            connection.execute(
                """
                INSERT INTO source_records(
                  id, source_id, upstream_id, canonical_url, payload_sha256,
                  raw_payload_path, observed_at
                ) VALUES (
                  'record-1', 'missing-source', 'upstream-1', 'https://example.test/item',
                  'sha256', 'raw/item.json', '2026-07-17T00:00:00Z'
                )
                """
            )
    finally:
        connection.close()


def test_check_constraint_violation_fails(database_path: Path) -> None:
    """Contract-level enum checks reject malformed rows deterministically."""
    database = _migrate(database_path)
    connection = database.connect()
    try:
        with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
            connection.execute(
                """
                INSERT INTO sources(
                  id, source_key, display_name, trust_tier, base_url,
                  poll_interval_minutes, connector_version, created_at, updated_at
                ) VALUES (
                  'source-1', 'invalid', 'Invalid', 'Z', 'https://example.test',
                  60, 'fixture-v1', '2026-07-17T00:00:00Z', '2026-07-17T00:00:00Z'
                )
                """
            )
    finally:
        connection.close()


def test_transaction_helper_commits_and_rolls_back(database_path: Path) -> None:
    """The typed transaction boundary commits success and rolls back exceptions."""
    database = _migrate(database_path)
    connection = database.connect()
    try:
        with transaction(connection):
            _insert_source(connection, "committed")

        with pytest.raises(RuntimeError, match="force rollback"), transaction(connection):
            _insert_source(connection, "rolled-back")
            raise RuntimeError("force rollback")

        rows = connection.execute("SELECT id FROM sources ORDER BY id").fetchall()
        assert [str(row[0]) for row in rows] == ["committed"]
    finally:
        connection.close()


def test_previous_schema_fixture_migrates_without_data_loss(database_path: Path) -> None:
    """The fixture-upgrade harness preserves legacy data for later release fixtures."""
    fixture_sql = (FIXTURES_DIRECTORY / "previous_schema_v0.sql").read_text(encoding="utf-8")
    fixture_connection = sqlite3.connect(database_path)
    try:
        fixture_connection.executescript(fixture_sql)
    finally:
        fixture_connection.close()

    database = _database(database_path)
    applied = MigrationRunner(database).migrate()

    assert [record.version for record in applied] == [1]
    connection = database.connect()
    try:
        marker = connection.execute("SELECT marker FROM previous_release_marker").fetchone()[0]
        assert marker == "v0-fixture"
    finally:
        connection.close()


def test_online_backup_is_consistent_and_confined(database_path: Path) -> None:
    """The backup API preserves data, verifies integrity, and rejects unsafe paths."""
    database = _migrate(database_path)
    connection = database.connect()
    try:
        _insert_source(connection, "backup-source")
    finally:
        connection.close()

    allowed_root = database_path.parent
    destination = allowed_root / "backups" / "snapshot.db"
    completed = backup_database(database, destination, allowed_root)

    assert completed == destination
    backup_connection = sqlite3.connect(destination)
    try:
        assert backup_connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert backup_connection.execute("SELECT count(*) FROM sources").fetchone()[0] == 1
    finally:
        backup_connection.close()

    with pytest.raises(BackupError, match="already exists"):
        backup_database(database, destination, allowed_root)
    with pytest.raises(BackupError, match="must remain inside"):
        backup_database(database, allowed_root.parent / "escaped.db", allowed_root)
    with pytest.raises(BackupError, match="cannot replace the active database"):
        backup_database(database, database_path, allowed_root, overwrite=True)


def test_backup_rejects_missing_source_without_creating_it(database_path: Path) -> None:
    """A backup request cannot turn a missing source into an empty SQLite file."""
    database = _database(database_path)

    with pytest.raises(BackupError, match="source does not exist"):
        backup_database(database, database_path.parent / "backup.db", database_path.parent)

    assert not database_path.exists()


def test_connection_requires_explicit_directory_initialization(database_path: Path) -> None:
    """The connection provider does not bypass configured path initialization."""
    missing_path = database_path.parent / "missing" / "database.db"

    with pytest.raises(DatabaseConnectionError, match="has not been initialized"):
        _database(missing_path).connect()
