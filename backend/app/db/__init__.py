"""Typed SQLite persistence foundation."""

from app.db.connection import (
    DatabaseConnectionError,
    DatabaseConnectionProvider,
    SQLiteDatabase,
    TransactionError,
    transaction,
)
from app.db.migrations import (
    AppliedMigration,
    Migration,
    MigrationError,
    MigrationRunner,
    SQLiteMigrationRecordStore,
)

__all__ = [
    "AppliedMigration",
    "DatabaseConnectionError",
    "DatabaseConnectionProvider",
    "Migration",
    "MigrationError",
    "MigrationRunner",
    "SQLiteDatabase",
    "SQLiteMigrationRecordStore",
    "TransactionError",
    "transaction",
]
