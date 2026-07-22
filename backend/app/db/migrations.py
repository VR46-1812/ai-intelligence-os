"""Numbered, checksum-verified SQLite schema migrations."""

from __future__ import annotations

import hashlib
import re
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from app.db.connection import DatabaseConnectionProvider, TransactionError, transaction

DEFAULT_MIGRATIONS_DIRECTORY = Path(__file__).with_name("migrations")
MIGRATION_FILENAME = re.compile(r"^(?P<version>\d{4})_(?P<name>[a-z][a-z0-9_]*)\.sql$")


class MigrationError(RuntimeError):
    """Raised for migration discovery, drift, or execution failures."""


@dataclass(frozen=True, slots=True)
class Migration:
    """An immutable SQL migration loaded from disk."""

    version: int
    name: str
    checksum: str
    sql: str
    path: Path

    def accepts_checksum(self, checksum: str) -> bool:
        """Accept only byte-identical SQL modulo Git-safe CRLF/LF conversion."""
        raw = self.sql.encode("utf-8")
        lf = raw.replace(b"\r\n", b"\n")
        crlf = lf.replace(b"\n", b"\r\n")
        return checksum in {
            self.checksum,
            hashlib.sha256(lf).hexdigest(),
            hashlib.sha256(crlf).hexdigest(),
        }


@dataclass(frozen=True, slots=True)
class AppliedMigration:
    """A durable migration record read from schema_migrations."""

    version: int
    name: str
    checksum: str
    applied_at: str


class MigrationRecordStore(Protocol):
    """Typed persistence boundary for schema migration history."""

    def ensure_schema(self, connection: sqlite3.Connection) -> None:
        """Create the migration history table when absent."""
        ...

    def list_applied(self, connection: sqlite3.Connection) -> tuple[AppliedMigration, ...]:
        """Return applied migrations ordered by version."""
        ...

    def add(self, connection: sqlite3.Connection, migration: Migration) -> AppliedMigration:
        """Persist one successfully applied migration."""
        ...


class SQLiteMigrationRecordStore:
    """SQLite implementation of the migration history boundary."""

    def ensure_schema(self, connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
              version INTEGER PRIMARY KEY,
              name TEXT NOT NULL,
              checksum TEXT NOT NULL,
              applied_at TEXT NOT NULL
            )
            """
        )

    def list_applied(self, connection: sqlite3.Connection) -> tuple[AppliedMigration, ...]:
        rows = connection.execute(
            """
            SELECT version, name, checksum, applied_at
            FROM schema_migrations
            ORDER BY version
            """
        ).fetchall()
        return tuple(
            AppliedMigration(
                version=int(row["version"]),
                name=str(row["name"]),
                checksum=str(row["checksum"]),
                applied_at=str(row["applied_at"]),
            )
            for row in rows
        )

    def add(self, connection: sqlite3.Connection, migration: Migration) -> AppliedMigration:
        applied_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        connection.execute(
            """
            INSERT INTO schema_migrations(version, name, checksum, applied_at)
            VALUES (?, ?, ?, ?)
            """,
            (migration.version, migration.name, migration.checksum, applied_at),
        )
        return AppliedMigration(
            version=migration.version,
            name=migration.name,
            checksum=migration.checksum,
            applied_at=applied_at,
        )


def discover_migrations(directory: Path = DEFAULT_MIGRATIONS_DIRECTORY) -> tuple[Migration, ...]:
    """Load contiguous numbered SQL files and calculate stable SHA-256 checksums."""
    if not directory.is_dir():
        raise MigrationError(f"Migration directory does not exist: {directory}")

    migrations: list[Migration] = []
    for path in sorted(directory.glob("*.sql")):
        match = MIGRATION_FILENAME.fullmatch(path.name)
        if match is None:
            raise MigrationError(f"Invalid migration filename: {path.name}")
        try:
            raw_sql = path.read_bytes()
            sql = raw_sql.decode("utf-8")
        except (OSError, UnicodeError) as error:
            raise MigrationError(f"Could not read UTF-8 migration {path}: {error}") from error
        migrations.append(
            Migration(
                version=int(match.group("version")),
                name=match.group("name"),
                checksum=hashlib.sha256(raw_sql).hexdigest(),
                sql=sql,
                path=path,
            )
        )

    if not migrations:
        raise MigrationError(f"No SQL migrations found in {directory}")
    versions = [migration.version for migration in migrations]
    expected_versions = list(range(1, len(migrations) + 1))
    if versions != expected_versions:
        raise MigrationError(
            "Migration versions must be contiguous from 1; "
            f"found {versions}, expected {expected_versions}"
        )
    return tuple(migrations)


def _iter_sql_statements(sql: str) -> Iterable[str]:
    buffer: list[str] = []
    for line in sql.splitlines(keepends=True):
        buffer.append(line)
        candidate = "".join(buffer)
        if sqlite3.complete_statement(candidate):
            statement = candidate.strip()
            if statement:
                yield statement
            buffer.clear()
    if "".join(buffer).strip():
        raise MigrationError("Migration contains an incomplete SQL statement")


def _is_connection_pragma(statement: str) -> bool:
    normalized = " ".join(statement.upper().split())
    return normalized in {"PRAGMA FOREIGN_KEYS = ON;", "PRAGMA JOURNAL_MODE = WAL;"}


class MigrationRunner:
    """Apply pending migrations once and reject previously applied file drift."""

    def __init__(
        self,
        database: DatabaseConnectionProvider,
        migrations_directory: Path = DEFAULT_MIGRATIONS_DIRECTORY,
        record_store: MigrationRecordStore | None = None,
    ) -> None:
        self._database = database
        self._migrations_directory = migrations_directory
        self._record_store = record_store or SQLiteMigrationRecordStore()

    def migrate(self) -> tuple[AppliedMigration, ...]:
        """Apply every pending migration and return only newly recorded versions."""
        migrations = discover_migrations(self._migrations_directory)
        connection = self._database.connect()
        try:
            self._record_store.ensure_schema(connection)
            applied = self._record_store.list_applied(connection)
            self._validate_history(migrations, applied)
            applied_versions = {record.version for record in applied}
            new_records: list[AppliedMigration] = []
            for migration in migrations:
                if migration.version not in applied_versions:
                    new_records.append(self._apply(connection, migration))
            return tuple(new_records)
        except sqlite3.Error as error:
            raise MigrationError(f"Could not access migration history: {error}") from error
        finally:
            connection.close()

    def _validate_history(
        self,
        migrations: tuple[Migration, ...],
        applied: tuple[AppliedMigration, ...],
    ) -> None:
        available = {migration.version: migration for migration in migrations}
        for record in applied:
            migration = available.get(record.version)
            if migration is None:
                raise MigrationError(
                    f"Database contains unknown migration version {record.version}"
                )
            if record.name != migration.name:
                raise MigrationError(
                    f"Migration {record.version} name drift: "
                    f"database={record.name}, file={migration.name}"
                )
            if not migration.accepts_checksum(record.checksum):
                raise MigrationError(
                    f"Migration {record.version} checksum drift for {migration.path.name}"
                )

    def _apply(self, connection: sqlite3.Connection, migration: Migration) -> AppliedMigration:
        try:
            with transaction(connection):
                for statement in _iter_sql_statements(migration.sql):
                    if not _is_connection_pragma(statement):
                        connection.execute(statement)
                return self._record_store.add(connection, migration)
        except (sqlite3.Error, MigrationError, TransactionError) as error:
            raise MigrationError(
                f"Failed to apply migration {migration.version} ({migration.name}): {error}"
            ) from error
