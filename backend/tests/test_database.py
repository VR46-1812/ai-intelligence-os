"""SQLite connection, migration, transaction, constraint, and backup tests."""

from __future__ import annotations

import hashlib
import shutil
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import pytest

from app.config import REPOSITORY_ROOT
from app.db.backup import BackupError, backup_database, manifest_path, restore_database
from app.db.connection import DatabaseConnectionError, SQLiteDatabase, transaction
from app.db.migration_repair import MigrationRepairError, repair_migration_7_checksum
from app.db.migrations import (
    DEFAULT_MIGRATIONS_DIRECTORY,
    MigrationError,
    MigrationRunner,
    discover_migrations,
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


def _seed_repair_preservation_graph(connection: sqlite3.Connection) -> None:
    now = "2026-07-22T00:00:00Z"
    connection.execute(
        """
        INSERT INTO works(
          id, work_type, canonical_title, normalized_title, created_at, updated_at
        ) VALUES ('repair-work', 'paper', 'Repair Paper', 'repair paper', ?, ?)
        """,
        (now, now),
    )
    connection.execute(
        """
        INSERT INTO work_versions(
          id, work_id, version_label, title, observed_at, is_current
        ) VALUES ('repair-version', 'repair-work', 'v1', 'Repair Paper', ?, 1)
        """,
        (now,),
    )
    connection.execute(
        """
        INSERT INTO documents(
          id, work_version_id, document_role, source_url, local_path, media_type,
          byte_size, sha256, acquired_at
        ) VALUES (
          'repair-document', 'repair-version', 'paper_pdf', 'https://example.test/paper.pdf',
          'raw/repair.pdf', 'application/pdf', 10, 'document-sha', ?
        )
        """,
        (now,),
    )
    connection.execute(
        """
        INSERT INTO evidence_spans(
          id, document_id, page_start, page_end, char_start, char_end,
          span_text, normalized_text_sha256, created_at
        ) VALUES ('repair-evidence', 'repair-document', 1, 1, 0, 8, 'Evidence', 'span-sha', ?)
        """,
        (now,),
    )
    connection.execute(
        """
        INSERT INTO pipeline_runs(
          id, run_type, trigger_type, status, config_snapshot_json, queued_at
        ) VALUES ('repair-run', 'daily', 'manual', 'succeeded', '{}', ?)
        """,
        (now,),
    )
    connection.execute(
        """
        INSERT INTO daily_reports(
          report_date, schema_version, pipeline_run_id, input_fingerprint,
          report_json, created_at, updated_at
        ) VALUES ('2026-07-22', '1.0', 'repair-run', 'fingerprint', '{}', ?, ?)
        """,
        (now, now),
    )
    connection.execute(
        """
        INSERT INTO linked_events(
          id, canonical_key, title, corroboration, created_at, updated_at
        ) VALUES ('repair-event', 'repair-key', 'Repair Event', 0.5, ?, ?)
        """,
        (now, now),
    )
    connection.execute(
        """
        INSERT INTO agent_executions(
          id, pipeline_run_id, agent_id, agent_version, stage_order, responsibility,
          status, idempotency_key, created_at, updated_at
        ) VALUES (
          'repair-agent', 'repair-run', 'orchestrator', '1.0', 1, 'coordinate',
          'succeeded', 'repair-idempotency', ?, ?
        )
        """,
        (now, now),
    )


def _alternate_line_ending_checksum() -> str:
    migration = next(item for item in discover_migrations() if item.version == 7)
    raw = migration.sql.encode()
    lf = raw.replace(b"\r\n", b"\n")
    alternate = lf if raw != lf else lf.replace(b"\n", b"\r\n")
    return hashlib.sha256(alternate).hexdigest()


def test_numbered_initial_migration_matches_contract() -> None:
    """The immutable version-one migration is sourced from the approved SQL contract."""
    contract_lines = (
        (REPOSITORY_ROOT / "contracts" / "schema.sql").read_text(encoding="utf-8").splitlines()
    )
    migration_lines = (
        (DEFAULT_MIGRATIONS_DIRECTORY / "0001_initial.sql").read_text(encoding="utf-8").splitlines()
    )

    assert contract_lines[: len(migration_lines)] == migration_lines


def test_new_database_migrates_with_required_sqlite_features(database_path: Path) -> None:
    """A clean file receives version one, WAL, foreign keys, busy timeout, and FTS5."""
    database = _database(database_path, busy_timeout_ms=3500)

    applied = MigrationRunner(database).migrate()

    assert [record.version for record in applied] == [1, 2, 3, 4, 5, 6, 7, 8]
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
        assert {
            "sources",
            "works",
            "evidence_spans",
            "knowledge_fts",
            "evidence_fts",
            "document_acquisition_attempts",
            "document_pages",
        } <= tables

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

    assert len(first) == 8
    assert second == ()
    connection = database.connect()
    try:
        assert connection.execute("SELECT count(*) FROM schema_migrations").fetchone()[0] == 8
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


def test_migration_7_repair_preserves_records_and_starts(database_path: Path) -> None:
    database = _migrate(database_path)
    connection = database.connect()
    try:
        _seed_repair_preservation_graph(connection)
        connection.execute(
            "UPDATE schema_migrations SET checksum=? WHERE version=7",
            (_alternate_line_ending_checksum(),),
        )
    finally:
        connection.close()

    result = repair_migration_7_checksum(database, database_path.parent, apply=True)

    assert result.repaired is True
    assert result.backup_verified is True
    assert result.schema_comparison.equivalent is True
    assert {record.table: record.row_count for record in result.preserved_records} == {
        "works": 1,
        "documents": 1,
        "evidence_spans": 1,
        "linked_events": 1,
        "daily_reports": 1,
        "agent_executions": 1,
    }
    assert result.audit_path is not None and Path(result.audit_path).is_file()
    assert MigrationRunner(database).migrate() == ()


def test_migration_7_repair_refuses_schema_difference(database_path: Path) -> None:
    database = _migrate(database_path)
    connection = database.connect()
    try:
        connection.execute("DROP INDEX idx_agent_executions_run_order")
        connection.execute(
            "UPDATE schema_migrations SET checksum=? WHERE version=7",
            (_alternate_line_ending_checksum(),),
        )
    finally:
        connection.close()

    with pytest.raises(MigrationRepairError, match="schema is not equivalent"):
        repair_migration_7_checksum(database, database_path.parent, apply=True)
    connection = database.connect()
    try:
        assert (
            connection.execute("SELECT checksum FROM schema_migrations WHERE version=7").fetchone()[
                0
            ]
            == _alternate_line_ending_checksum()
        )
    finally:
        connection.close()


def test_migration_7_repair_is_idempotent(database_path: Path) -> None:
    database = _migrate(database_path)
    connection = database.connect()
    try:
        connection.execute(
            "UPDATE schema_migrations SET checksum=? WHERE version=7",
            (_alternate_line_ending_checksum(),),
        )
    finally:
        connection.close()

    first = repair_migration_7_checksum(database, database_path.parent, apply=True)
    second = repair_migration_7_checksum(database, database_path.parent, apply=True)

    assert first.repaired is True
    assert second.repaired is False
    assert second.old_checksum == second.new_checksum == first.new_checksum


def test_migration_7_repair_does_not_mask_other_checksum_drift(database_path: Path) -> None:
    database = _migrate(database_path)
    connection = database.connect()
    try:
        connection.execute("UPDATE schema_migrations SET checksum='changed' WHERE version=1")
        connection.execute(
            "UPDATE schema_migrations SET checksum=? WHERE version=7",
            (_alternate_line_ending_checksum(),),
        )
    finally:
        connection.close()

    with pytest.raises(MigrationRepairError, match="Migration 1 checksum drift remains blocked"):
        repair_migration_7_checksum(database, database_path.parent, apply=True)


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

    assert [record.version for record in applied] == [1, 2, 3, 4, 5, 6, 7, 8]
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
    assert manifest_path(destination).is_file()
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


def test_backup_restore_round_trip_preserves_temporary_data(database_path: Path) -> None:
    database = _migrate(database_path)
    connection = database.connect()
    try:
        _insert_source(connection, "round-trip-source")
    finally:
        connection.close()
    backup = backup_database(
        database,
        database_path.parent / "backups" / "round-trip.db",
        database_path.parent,
    )
    restored = restore_database(
        backup,
        database_path.parent / "restored" / "state.db",
        database_path.parent,
    )

    connection = sqlite3.connect(restored)
    try:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert (
            connection.execute("SELECT id FROM sources WHERE id='round-trip-source'").fetchone()[0]
            == "round-trip-source"
        )
        assert connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == 8
    finally:
        connection.close()


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
