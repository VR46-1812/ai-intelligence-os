"""SQLite connection and transaction boundaries."""

from __future__ import annotations

import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol


class DatabaseConnectionError(RuntimeError):
    """Raised when a configured SQLite connection cannot be established safely."""


class TransactionError(RuntimeError):
    """Raised when a transaction cannot be started with the requested semantics."""


class DatabaseConnectionProvider(Protocol):
    """Typed boundary consumed by migrations and later repository implementations."""

    def connect(self) -> sqlite3.Connection:
        """Return a configured SQLite connection owned by the caller."""
        ...


@dataclass(frozen=True, slots=True)
class SQLiteDatabase:
    """Create lightweight SQLite connections with mandatory local safety pragmas."""

    path: Path
    busy_timeout_ms: int = 5000

    def connect(self) -> sqlite3.Connection:
        """Open a WAL connection with foreign keys and bounded lock waiting enabled."""
        if not self.path.parent.is_dir():
            raise DatabaseConnectionError(
                f"Database parent directory has not been initialized: {self.path.parent}"
            )
        if not 1000 <= self.busy_timeout_ms <= 30_000:
            raise DatabaseConnectionError("busy_timeout_ms must be between 1000 and 30000")

        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(
                self.path,
                timeout=self.busy_timeout_ms / 1000,
                isolation_level=None,
            )
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute(f"PRAGMA busy_timeout = {self.busy_timeout_ms}")
            journal_mode_row = connection.execute("PRAGMA journal_mode = WAL").fetchone()
            foreign_keys_row = connection.execute("PRAGMA foreign_keys").fetchone()
            if journal_mode_row is None or str(journal_mode_row[0]).lower() != "wal":
                raise DatabaseConnectionError("SQLite could not enable WAL journal mode")
            if foreign_keys_row is None or int(foreign_keys_row[0]) != 1:
                raise DatabaseConnectionError("SQLite could not enable foreign-key enforcement")
            return connection
        except DatabaseConnectionError:
            if connection is not None:
                connection.close()
            raise
        except sqlite3.Error as error:
            if connection is not None:
                connection.close()
            raise DatabaseConnectionError(
                f"Could not open SQLite database at {self.path}: {error}"
            ) from error


@contextmanager
def transaction(
    connection: sqlite3.Connection,
    mode: Literal["DEFERRED", "IMMEDIATE", "EXCLUSIVE"] = "IMMEDIATE",
) -> Generator[sqlite3.Connection]:
    """Commit a unit of work or roll it back on every exceptional exit."""
    if connection.in_transaction:
        raise TransactionError("Nested transactions are not supported by this helper")

    try:
        connection.execute(f"BEGIN {mode}")
    except sqlite3.Error as error:
        raise TransactionError(f"Could not begin {mode} SQLite transaction: {error}") from error

    try:
        yield connection
    except BaseException:
        connection.rollback()
        raise
    else:
        connection.commit()
