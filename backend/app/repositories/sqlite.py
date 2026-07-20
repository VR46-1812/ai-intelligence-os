"""SQLite implementations of the M1.2 repository boundaries."""

# SQL statements intentionally preserve schema column groupings.
# ruff: noqa: E501

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from difflib import SequenceMatcher
from enum import Enum
from typing import cast

from pydantic import BaseModel, ValidationError

from app.domain.models import (
    AnalysisRun,
    AnalysisRunFilter,
    Author,
    Document,
    DocumentFilter,
    ExternalIdentifier,
    ExternalIdType,
    PageRequest,
    PipelineRun,
    PipelineRunFilter,
    RankingProfile,
    RankingProfileFilter,
    RankingResult,
    RankingResultFilter,
    Source,
    SourceFilter,
    SourceRecord,
    SourceRecordFilter,
    Topic,
    Work,
    WorkAuthor,
    WorkFilter,
    WorkVersion,
    WorkVersionFilter,
)
from app.domain.repositories import CreateResult

_SOURCE_FILTER = SourceFilter()
_SOURCE_RECORD_FILTER = SourceRecordFilter()
_WORK_FILTER = WorkFilter()
_WORK_VERSION_FILTER = WorkVersionFilter()
_DOCUMENT_FILTER = DocumentFilter()
_RANKING_PROFILE_FILTER = RankingProfileFilter()
_RANKING_RESULT_FILTER = RankingResultFilter()
_ANALYSIS_FILTER = AnalysisRunFilter()
_PIPELINE_FILTER = PipelineRunFilter()


class RepositoryError(RuntimeError):
    """Base class for actionable persistence failures."""


class RepositoryTransactionError(RepositoryError):
    """Raised when a write is attempted without an explicit transaction."""


class RepositoryNotFoundError(RepositoryError):
    """Raised when an update target no longer exists."""


class RepositoryDuplicateError(RepositoryError):
    """Raised when a non-idempotent create violates a unique key."""


class RepositoryConstraintError(RepositoryError):
    """Raised when persisted data violates a schema constraint."""


class RepositoryDataError(RepositoryError):
    """Raised when persisted rows cannot be decoded into the typed domain."""


def _json(value: object) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def _datetime(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _value(value: object) -> object:
    return value.value if isinstance(value, Enum) else value


def _decode_json(value: object, column: str) -> dict[str, object]:
    try:
        decoded = json.loads(str(value))
    except (TypeError, ValueError) as error:
        raise RepositoryDataError(f"Invalid JSON in {column}") from error
    if not isinstance(decoded, dict):
        raise RepositoryDataError(f"Expected a JSON object in {column}")
    return cast(dict[str, object], decoded)


def _model[ModelT: BaseModel](model_type: type[ModelT], values: dict[str, object]) -> ModelT:
    try:
        return model_type.model_validate(values)
    except ValidationError as error:
        raise RepositoryDataError(f"Invalid persisted {model_type.__name__}: {error}") from error


def _where(filters: list[tuple[str, object | None]]) -> tuple[str, list[object]]:
    selected = [(column, value) for column, value in filters if value is not None]
    if not selected:
        return "", []
    return " WHERE " + " AND ".join(f"{column} = ?" for column, _ in selected), [
        _value(value) for _, value in selected
    ]


class _SQLiteRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def _require_transaction(self) -> None:
        if not self._connection.in_transaction:
            raise RepositoryTransactionError("Repository writes require an explicit transaction")

    def _execute_write(self, sql: str, parameters: tuple[object, ...], operation: str) -> None:
        self._require_transaction()
        try:
            cursor = self._connection.execute(sql, parameters)
        except sqlite3.IntegrityError as error:
            if "UNIQUE constraint failed" in str(error):
                raise RepositoryDuplicateError(f"{operation}: {error}") from error
            raise RepositoryConstraintError(f"{operation}: {error}") from error
        except sqlite3.Error as error:
            raise RepositoryError(f"{operation}: {error}") from error
        if sql.lstrip().upper().startswith("UPDATE") and cursor.rowcount != 1:
            raise RepositoryNotFoundError(f"{operation}: target not found")

    def _fetchone(
        self, sql: str, parameters: tuple[object, ...], operation: str
    ) -> sqlite3.Row | None:
        try:
            return self._connection.execute(sql, parameters).fetchone()
        except sqlite3.Error as error:
            raise RepositoryError(f"{operation}: {error}") from error

    def _fetchall(self, sql: str, parameters: list[object], operation: str) -> list[sqlite3.Row]:
        try:
            return self._connection.execute(sql, parameters).fetchall()
        except sqlite3.Error as error:
            raise RepositoryError(f"{operation}: {error}") from error


class SQLiteSourceRepository(_SQLiteRepository):
    _columns = """id, source_key, display_name, trust_tier, base_url, enabled,
        poll_interval_minutes, minimum_request_interval_ms, connector_version, config_json,
        cursor_json, health_status, last_attempt_at, last_success_at, created_at, updated_at"""

    @staticmethod
    def _from_row(row: sqlite3.Row) -> Source:
        return _model(
            Source,
            {
                "id": row["id"],
                "source_key": row["source_key"],
                "display_name": row["display_name"],
                "trust_tier": row["trust_tier"],
                "base_url": row["base_url"],
                "enabled": bool(row["enabled"]),
                "poll_interval_minutes": row["poll_interval_minutes"],
                "minimum_request_interval_ms": row["minimum_request_interval_ms"],
                "connector_version": row["connector_version"],
                "config": _decode_json(row["config_json"], "config_json"),
                "cursor": None
                if row["cursor_json"] is None
                else _decode_json(row["cursor_json"], "cursor_json"),
                "health_status": row["health_status"],
                "last_attempt_at": row["last_attempt_at"],
                "last_success_at": row["last_success_at"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            },
        )

    @staticmethod
    def _params(source: Source) -> tuple[object, ...]:
        return (
            source.id,
            source.source_key,
            source.display_name,
            source.trust_tier.value,
            source.base_url,
            int(source.enabled),
            source.poll_interval_minutes,
            source.minimum_request_interval_ms,
            source.connector_version,
            _json(source.config),
            None if source.cursor is None else _json(source.cursor),
            source.health_status.value,
            _datetime(source.last_attempt_at),
            _datetime(source.last_success_at),
            _datetime(source.created_at),
            _datetime(source.updated_at),
        )

    def create(self, source: Source) -> Source:
        self._execute_write(
            f"INSERT INTO sources ({self._columns}) VALUES ({','.join('?' for _ in range(16))})",
            self._params(source),
            "create source",
        )
        return source

    def get(self, source_id: str) -> Source | None:
        row = self._fetchone(
            f"SELECT {self._columns} FROM sources WHERE id = ?", (source_id,), "get source"
        )
        return None if row is None else self._from_row(row)

    def update(self, source: Source) -> Source:
        params = self._params(source)
        self._execute_write(
            """UPDATE sources SET source_key=?, display_name=?, trust_tier=?, base_url=?, enabled=?,
            poll_interval_minutes=?, minimum_request_interval_ms=?, connector_version=?, config_json=?, cursor_json=?,
            health_status=?, last_attempt_at=?, last_success_at=?, created_at=?, updated_at=? WHERE id=?""",
            (*params[1:], source.id),
            "update source",
        )
        return source

    def list(self, page: PageRequest, filters: SourceFilter = _SOURCE_FILTER) -> tuple[Source, ...]:
        clause, params = _where(
            [
                ("enabled", None if filters.enabled is None else int(filters.enabled)),
                ("health_status", filters.health_status),
                ("trust_tier", filters.trust_tier),
            ]
        )
        rows = self._fetchall(
            f"SELECT {self._columns} FROM sources{clause} ORDER BY source_key, id LIMIT ? OFFSET ?",
            [*params, page.limit, page.offset],
            "list sources",
        )
        return tuple(self._from_row(row) for row in rows)


class SQLiteSourceRecordRepository(_SQLiteRepository):
    _columns = """id, source_id, upstream_id, upstream_version, canonical_url, payload_sha256,
        raw_payload_path, observed_at, published_at, updated_at_upstream, normalization_status,
        error_code, error_detail"""

    @staticmethod
    def _from_row(row: sqlite3.Row) -> SourceRecord:
        return _model(
            SourceRecord,
            {
                key: row[key]
                for key in (
                    "id",
                    "source_id",
                    "upstream_id",
                    "upstream_version",
                    "canonical_url",
                    "payload_sha256",
                    "raw_payload_path",
                    "observed_at",
                    "published_at",
                    "updated_at_upstream",
                    "normalization_status",
                    "error_code",
                    "error_detail",
                )
            },
        )

    @staticmethod
    def _params(record: SourceRecord) -> tuple[object, ...]:
        return (
            record.id,
            record.source_id,
            record.upstream_id,
            record.upstream_version,
            record.canonical_url,
            record.payload_sha256,
            record.raw_payload_path,
            _datetime(record.observed_at),
            _datetime(record.published_at),
            _datetime(record.updated_at_upstream),
            record.normalization_status.value,
            record.error_code,
            record.error_detail,
        )

    def create_or_get(self, record: SourceRecord) -> CreateResult[SourceRecord]:
        self._require_transaction()
        existing = self._fetchone(
            f"SELECT {self._columns} FROM source_records WHERE source_id=? AND upstream_id=? AND payload_sha256=?",
            (record.source_id, record.upstream_id, record.payload_sha256),
            "deduplicate source record",
        )
        if existing is not None:
            return CreateResult(self._from_row(existing), False)
        self._execute_write(
            f"INSERT INTO source_records ({self._columns}) VALUES ({','.join('?' for _ in range(13))})",
            self._params(record),
            "create source record",
        )
        return CreateResult(record, True)

    def get(self, record_id: str) -> SourceRecord | None:
        row = self._fetchone(
            f"SELECT {self._columns} FROM source_records WHERE id=?",
            (record_id,),
            "get source record",
        )
        return None if row is None else self._from_row(row)

    def update(self, record: SourceRecord) -> SourceRecord:
        params = self._params(record)
        self._execute_write(
            """UPDATE source_records SET source_id=?, upstream_id=?, upstream_version=?, canonical_url=?,
            payload_sha256=?, raw_payload_path=?, observed_at=?, published_at=?, updated_at_upstream=?,
            normalization_status=?, error_code=?, error_detail=? WHERE id=?""",
            (*params[1:], record.id),
            "update source record",
        )
        return record

    def list(
        self, page: PageRequest, filters: SourceRecordFilter = _SOURCE_RECORD_FILTER
    ) -> tuple[SourceRecord, ...]:
        clause, params = _where(
            [
                ("source_id", filters.source_id),
                ("normalization_status", filters.normalization_status),
            ]
        )
        rows = self._fetchall(
            f"SELECT {self._columns} FROM source_records{clause} ORDER BY observed_at DESC, id LIMIT ? OFFSET ?",
            [*params, page.limit, page.offset],
            "list source records",
        )
        return tuple(self._from_row(row) for row in rows)


class SQLiteWorkRepository(_SQLiteRepository):
    _columns = """id, work_type, canonical_title, normalized_title, abstract, language,
        publication_status, first_published_at, current_version_id, lifecycle_state, created_at, updated_at"""

    @staticmethod
    def _from_row(row: sqlite3.Row) -> Work:
        return _model(
            Work,
            {
                key: row[key]
                for key in (
                    "id",
                    "work_type",
                    "canonical_title",
                    "normalized_title",
                    "abstract",
                    "language",
                    "publication_status",
                    "first_published_at",
                    "current_version_id",
                    "lifecycle_state",
                    "created_at",
                    "updated_at",
                )
            },
        )

    @staticmethod
    def _params(work: Work) -> tuple[object, ...]:
        return (
            work.id,
            work.work_type.value,
            work.canonical_title,
            work.normalized_title,
            work.abstract,
            work.language,
            work.publication_status.value,
            _datetime(work.first_published_at),
            work.current_version_id,
            work.lifecycle_state.value,
            _datetime(work.created_at),
            _datetime(work.updated_at),
        )

    def create(self, work: Work) -> Work:
        self._execute_write(
            f"INSERT INTO works ({self._columns}) VALUES ({','.join('?' for _ in range(12))})",
            self._params(work),
            "create work",
        )
        return work

    def get(self, work_id: str) -> Work | None:
        row = self._fetchone(
            f"SELECT {self._columns} FROM works WHERE id=?", (work_id,), "get work"
        )
        return None if row is None else self._from_row(row)

    def update(self, work: Work) -> Work:
        params = self._params(work)
        self._execute_write(
            """UPDATE works SET work_type=?, canonical_title=?, normalized_title=?, abstract=?, language=?,
            publication_status=?, first_published_at=?, current_version_id=?, lifecycle_state=?, created_at=?, updated_at=? WHERE id=?""",
            (*params[1:], work.id),
            "update work",
        )
        return work

    def list(self, page: PageRequest, filters: WorkFilter = _WORK_FILTER) -> tuple[Work, ...]:
        clause, params = _where(
            [
                ("work_type", filters.work_type),
                ("publication_status", filters.publication_status),
                ("lifecycle_state", filters.lifecycle_state),
            ]
        )
        rows = self._fetchall(
            f"SELECT {self._columns} FROM works{clause} ORDER BY created_at DESC, id LIMIT ? OFFSET ?",
            [*params, page.limit, page.offset],
            "list works",
        )
        return tuple(self._from_row(row) for row in rows)


class SQLiteCatalogIdentityRepository(_SQLiteRepository):
    _external_id_columns = (
        "id, work_id, id_type, normalized_value, raw_value, source_record_id, created_at"
    )

    @staticmethod
    def _external_id_from_row(row: sqlite3.Row) -> ExternalIdentifier:
        return _model(
            ExternalIdentifier,
            {
                "id": row["id"],
                "work_id": row["work_id"],
                "id_type": row["id_type"],
                "normalized_value": row["normalized_value"],
                "raw_value": row["raw_value"],
                "source_record_id": row["source_record_id"],
                "created_at": row["created_at"],
            },
        )

    def create_external_id_or_get(
        self, identifier: ExternalIdentifier
    ) -> CreateResult[ExternalIdentifier]:
        self._require_transaction()
        existing = self.get_external_id(identifier.id_type, identifier.normalized_value)
        if existing is not None:
            return CreateResult(existing, False)
        self._execute_write(
            f"INSERT INTO external_ids ({self._external_id_columns}) VALUES (?,?,?,?,?,?,?)",
            (
                identifier.id,
                identifier.work_id,
                identifier.id_type.value,
                identifier.normalized_value,
                identifier.raw_value,
                identifier.source_record_id,
                _datetime(identifier.created_at),
            ),
            "create external identifier",
        )
        return CreateResult(identifier, True)

    def get_external_id(
        self, id_type: ExternalIdType, normalized_value: str
    ) -> ExternalIdentifier | None:
        row = self._fetchone(
            f"SELECT {self._external_id_columns} FROM external_ids "
            "WHERE id_type=? AND normalized_value=?",
            (id_type.value, normalized_value),
            "get external identifier",
        )
        return None if row is None else self._external_id_from_row(row)

    def list_external_ids(self, work_id: str) -> tuple[ExternalIdentifier, ...]:
        rows = self._fetchall(
            f"SELECT {self._external_id_columns} FROM external_ids "
            "WHERE work_id=? ORDER BY id_type, normalized_value, id",
            [work_id],
            "list external identifiers",
        )
        return tuple(self._external_id_from_row(row) for row in rows)

    def create_author(self, author: Author) -> Author:
        self._execute_write(
            """INSERT INTO authors(
                id, normalized_name, display_name, orcid, affiliation_text, created_at, updated_at
            ) VALUES (?,?,?,?,?,?,?)""",
            (
                author.id,
                author.normalized_name,
                author.display_name,
                author.orcid,
                author.affiliation_text,
                _datetime(author.created_at),
                _datetime(author.updated_at),
            ),
            "create author",
        )
        return author

    def create_work_author(self, work_author: WorkAuthor) -> WorkAuthor:
        self._execute_write(
            """INSERT INTO work_authors(
                work_id, author_id, author_order, is_corresponding
            ) VALUES (?,?,?,?)""",
            (
                work_author.work_id,
                work_author.author_id,
                work_author.author_order,
                int(work_author.is_corresponding),
            ),
            "create work author",
        )
        return work_author

    def find_candidate_work_ids(
        self,
        *,
        normalized_title: str,
        normalized_first_author: str,
        publication_year: int,
        fuzzy_title_threshold: float,
    ) -> tuple[str, ...]:
        try:
            rows = self._connection.execute(
                """SELECT w.id, w.normalized_title
                FROM works AS w
                JOIN work_authors AS wa ON wa.work_id = w.id AND wa.author_order = 1
                JOIN authors AS a ON a.id = wa.author_id
                WHERE a.normalized_name = ?
                  AND CAST(substr(w.first_published_at, 1, 4) AS INTEGER) = ?
                ORDER BY w.id""",
                (normalized_first_author, publication_year),
            ).fetchall()
        except sqlite3.Error as error:
            raise RepositoryError(f"find identity candidates: {error}") from error

        return tuple(
            str(row["id"])
            for row in rows
            if SequenceMatcher(
                None,
                normalized_title,
                str(row["normalized_title"]),
                autojunk=False,
            ).ratio()
            >= fuzzy_title_threshold
        )


class SQLiteTopicRepository(_SQLiteRepository):
    _columns = "id, topic_key, display_name, parent_topic_id, description, active"

    @staticmethod
    def _from_row(row: sqlite3.Row) -> Topic:
        return _model(
            Topic,
            {
                "id": row["id"],
                "topic_key": row["topic_key"],
                "display_name": row["display_name"],
                "parent_topic_id": row["parent_topic_id"],
                "description": row["description"],
                "active": bool(row["active"]),
            },
        )

    def upsert(self, topic: Topic) -> CreateResult[Topic]:
        self._require_transaction()
        existing = self.get_by_key(topic.topic_key)
        if existing is None:
            self._execute_write(
                f"INSERT INTO topics ({self._columns}) VALUES (?,?,?,?,?,?)",
                (
                    topic.id,
                    topic.topic_key,
                    topic.display_name,
                    topic.parent_topic_id,
                    topic.description,
                    int(topic.active),
                ),
                "create topic",
            )
            return CreateResult(topic, True)

        updated = topic.model_copy(update={"id": existing.id})
        self._execute_write(
            """UPDATE topics SET display_name=?, parent_topic_id=?, description=?, active=?
            WHERE id=?""",
            (
                updated.display_name,
                updated.parent_topic_id,
                updated.description,
                int(updated.active),
                updated.id,
            ),
            "update topic",
        )
        return CreateResult(updated, False)

    def get_by_key(self, topic_key: str) -> Topic | None:
        row = self._fetchone(
            f"SELECT {self._columns} FROM topics WHERE topic_key=?",
            (topic_key,),
            "get topic",
        )
        return None if row is None else self._from_row(row)

    def list(self, *, active: bool | None = None) -> tuple[Topic, ...]:
        clause = "" if active is None else " WHERE active=?"
        parameters: list[object] = [] if active is None else [int(active)]
        rows = self._fetchall(
            f"SELECT {self._columns} FROM topics{clause} ORDER BY topic_key, id",
            parameters,
            "list topics",
        )
        return tuple(self._from_row(row) for row in rows)


class SQLiteWorkVersionRepository(_SQLiteRepository):
    _columns = """id, work_id, version_label, content_sha256, title, abstract, metadata_json,
        source_record_id, published_at, observed_at, is_current"""

    @staticmethod
    def _from_row(row: sqlite3.Row) -> WorkVersion:
        return _model(
            WorkVersion,
            {
                "id": row["id"],
                "work_id": row["work_id"],
                "version_label": row["version_label"],
                "content_sha256": row["content_sha256"],
                "title": row["title"],
                "abstract": row["abstract"],
                "metadata": _decode_json(row["metadata_json"], "metadata_json"),
                "source_record_id": row["source_record_id"],
                "published_at": row["published_at"],
                "observed_at": row["observed_at"],
                "is_current": bool(row["is_current"]),
            },
        )

    @staticmethod
    def _params(version: WorkVersion) -> tuple[object, ...]:
        return (
            version.id,
            version.work_id,
            version.version_label,
            version.content_sha256,
            version.title,
            version.abstract,
            _json(version.metadata),
            version.source_record_id,
            _datetime(version.published_at),
            _datetime(version.observed_at),
            int(version.is_current),
        )

    def create_or_get(self, version: WorkVersion) -> CreateResult[WorkVersion]:
        self._require_transaction()
        existing = self._fetchone(
            f"SELECT {self._columns} FROM work_versions WHERE work_id=? AND version_label=?",
            (version.work_id, version.version_label),
            "deduplicate work version",
        )
        if existing is not None:
            return CreateResult(self._from_row(existing), False)
        self._execute_write(
            f"INSERT INTO work_versions ({self._columns}) VALUES ({','.join('?' for _ in range(11))})",
            self._params(version),
            "create work version",
        )
        return CreateResult(version, True)

    def get(self, version_id: str) -> WorkVersion | None:
        row = self._fetchone(
            f"SELECT {self._columns} FROM work_versions WHERE id=?",
            (version_id,),
            "get work version",
        )
        return None if row is None else self._from_row(row)

    def update(self, version: WorkVersion) -> WorkVersion:
        params = self._params(version)
        self._execute_write(
            """UPDATE work_versions SET work_id=?, version_label=?, content_sha256=?, title=?, abstract=?,
            metadata_json=?, source_record_id=?, published_at=?, observed_at=?, is_current=? WHERE id=?""",
            (*params[1:], version.id),
            "update work version",
        )
        return version

    def list(
        self, page: PageRequest, filters: WorkVersionFilter = _WORK_VERSION_FILTER
    ) -> tuple[WorkVersion, ...]:
        clause, params = _where(
            [
                ("work_id", filters.work_id),
                ("is_current", None if filters.is_current is None else int(filters.is_current)),
            ]
        )
        rows = self._fetchall(
            f"SELECT {self._columns} FROM work_versions{clause} ORDER BY observed_at DESC, id LIMIT ? OFFSET ?",
            [*params, page.limit, page.offset],
            "list work versions",
        )
        return tuple(self._from_row(row) for row in rows)


class SQLiteDocumentRepository(_SQLiteRepository):
    _columns = """id, work_version_id, document_role, source_url, local_path, media_type, byte_size,
        sha256, parser_name, parser_version, parse_status, page_count, acquired_at, parsed_at"""

    @staticmethod
    def _from_row(row: sqlite3.Row) -> Document:
        return _model(
            Document,
            {
                key: row[key]
                for key in (
                    "id",
                    "work_version_id",
                    "document_role",
                    "source_url",
                    "local_path",
                    "media_type",
                    "byte_size",
                    "sha256",
                    "parser_name",
                    "parser_version",
                    "parse_status",
                    "page_count",
                    "acquired_at",
                    "parsed_at",
                )
            },
        )

    @staticmethod
    def _params(document: Document) -> tuple[object, ...]:
        return (
            document.id,
            document.work_version_id,
            document.document_role.value,
            document.source_url,
            document.local_path,
            document.media_type,
            document.byte_size,
            document.sha256,
            document.parser_name,
            document.parser_version,
            document.parse_status.value,
            document.page_count,
            _datetime(document.acquired_at),
            _datetime(document.parsed_at),
        )

    def create_or_get(self, document: Document) -> CreateResult[Document]:
        self._require_transaction()
        existing = self._fetchone(
            f"SELECT {self._columns} FROM documents WHERE work_version_id=? AND document_role=? AND sha256=?",
            (document.work_version_id, document.document_role.value, document.sha256),
            "deduplicate document",
        )
        if existing is not None:
            return CreateResult(self._from_row(existing), False)
        self._execute_write(
            f"INSERT INTO documents ({self._columns}) VALUES ({','.join('?' for _ in range(14))})",
            self._params(document),
            "create document",
        )
        return CreateResult(document, True)

    def get(self, document_id: str) -> Document | None:
        row = self._fetchone(
            f"SELECT {self._columns} FROM documents WHERE id=?", (document_id,), "get document"
        )
        return None if row is None else self._from_row(row)

    def update(self, document: Document) -> Document:
        params = self._params(document)
        self._execute_write(
            """UPDATE documents SET work_version_id=?, document_role=?, source_url=?, local_path=?,
            media_type=?, byte_size=?, sha256=?, parser_name=?, parser_version=?, parse_status=?, page_count=?,
            acquired_at=?, parsed_at=? WHERE id=?""",
            (*params[1:], document.id),
            "update document",
        )
        return document

    def list(
        self, page: PageRequest, filters: DocumentFilter = _DOCUMENT_FILTER
    ) -> tuple[Document, ...]:
        clause, params = _where(
            [
                ("work_version_id", filters.work_version_id),
                ("document_role", filters.document_role),
                ("parse_status", filters.parse_status),
            ]
        )
        rows = self._fetchall(
            f"SELECT {self._columns} FROM documents{clause} ORDER BY acquired_at DESC, id LIMIT ? OFFSET ?",
            [*params, page.limit, page.offset],
            "list documents",
        )
        return tuple(self._from_row(row) for row in rows)


class SQLiteRankingRepository(_SQLiteRepository):
    _profile_columns = (
        "id, profile_key, version, weights_json, normalization_json, active, created_at"
    )
    _result_columns = "id, work_id, profile_id, score_kind, total_score, components_json, feature_snapshot_json, calculated_at"

    @staticmethod
    def _profile_from_row(row: sqlite3.Row) -> RankingProfile:
        return _model(
            RankingProfile,
            {
                "id": row["id"],
                "profile_key": row["profile_key"],
                "version": row["version"],
                "weights": _decode_json(row["weights_json"], "weights_json"),
                "normalization": _decode_json(row["normalization_json"], "normalization_json"),
                "active": bool(row["active"]),
                "created_at": row["created_at"],
            },
        )

    @staticmethod
    def _result_from_row(row: sqlite3.Row) -> RankingResult:
        return _model(
            RankingResult,
            {
                "id": row["id"],
                "work_id": row["work_id"],
                "profile_id": row["profile_id"],
                "score_kind": row["score_kind"],
                "total_score": row["total_score"],
                "components": _decode_json(row["components_json"], "components_json"),
                "feature_snapshot": _decode_json(
                    row["feature_snapshot_json"], "feature_snapshot_json"
                ),
                "calculated_at": row["calculated_at"],
            },
        )

    def create_profile(self, profile: RankingProfile) -> RankingProfile:
        self._execute_write(
            f"INSERT INTO ranking_profiles ({self._profile_columns}) VALUES (?,?,?,?,?,?,?)",
            (
                profile.id,
                profile.profile_key,
                profile.version,
                _json(profile.weights),
                _json(profile.normalization),
                int(profile.active),
                _datetime(profile.created_at),
            ),
            "create ranking profile",
        )
        return profile

    def get_profile(self, profile_id: str) -> RankingProfile | None:
        row = self._fetchone(
            f"SELECT {self._profile_columns} FROM ranking_profiles WHERE id=?",
            (profile_id,),
            "get ranking profile",
        )
        return None if row is None else self._profile_from_row(row)

    def update_profile(self, profile: RankingProfile) -> RankingProfile:
        self._execute_write(
            """UPDATE ranking_profiles SET profile_key=?, version=?, weights_json=?, normalization_json=?,
            active=?, created_at=? WHERE id=?""",
            (
                profile.profile_key,
                profile.version,
                _json(profile.weights),
                _json(profile.normalization),
                int(profile.active),
                _datetime(profile.created_at),
                profile.id,
            ),
            "update ranking profile",
        )
        return profile

    def list_profiles(
        self, page: PageRequest, filters: RankingProfileFilter = _RANKING_PROFILE_FILTER
    ) -> tuple[RankingProfile, ...]:
        clause, params = _where(
            [
                ("profile_key", filters.profile_key),
                ("active", None if filters.active is None else int(filters.active)),
            ]
        )
        rows = self._fetchall(
            f"SELECT {self._profile_columns} FROM ranking_profiles{clause} ORDER BY profile_key, version DESC, id LIMIT ? OFFSET ?",
            [*params, page.limit, page.offset],
            "list ranking profiles",
        )
        return tuple(self._profile_from_row(row) for row in rows)

    def create_result_or_get(self, result: RankingResult) -> CreateResult[RankingResult]:
        self._require_transaction()
        existing = self._fetchone(
            f"SELECT {self._result_columns} FROM ranking_results WHERE work_id=? AND profile_id=? AND score_kind=?",
            (result.work_id, result.profile_id, result.score_kind.value),
            "deduplicate ranking result",
        )
        if existing is not None:
            return CreateResult(self._result_from_row(existing), False)
        self._execute_write(
            f"INSERT INTO ranking_results ({self._result_columns}) VALUES (?,?,?,?,?,?,?,?)",
            (
                result.id,
                result.work_id,
                result.profile_id,
                result.score_kind.value,
                result.total_score,
                _json(result.components),
                _json(result.feature_snapshot),
                _datetime(result.calculated_at),
            ),
            "create ranking result",
        )
        return CreateResult(result, True)

    def get_result(self, result_id: str) -> RankingResult | None:
        row = self._fetchone(
            f"SELECT {self._result_columns} FROM ranking_results WHERE id=?",
            (result_id,),
            "get ranking result",
        )
        return None if row is None else self._result_from_row(row)

    def update_result(self, result: RankingResult) -> RankingResult:
        self._execute_write(
            """UPDATE ranking_results SET work_id=?, profile_id=?, score_kind=?, total_score=?,
            components_json=?, feature_snapshot_json=?, calculated_at=? WHERE id=?""",
            (
                result.work_id,
                result.profile_id,
                result.score_kind.value,
                result.total_score,
                _json(result.components),
                _json(result.feature_snapshot),
                _datetime(result.calculated_at),
                result.id,
            ),
            "update ranking result",
        )
        return result

    def list_results(
        self, page: PageRequest, filters: RankingResultFilter = _RANKING_RESULT_FILTER
    ) -> tuple[RankingResult, ...]:
        clause, params = _where(
            [
                ("work_id", filters.work_id),
                ("profile_id", filters.profile_id),
                ("score_kind", filters.score_kind),
            ]
        )
        rows = self._fetchall(
            f"SELECT {self._result_columns} FROM ranking_results{clause} ORDER BY total_score DESC, id LIMIT ? OFFSET ?",
            [*params, page.limit, page.offset],
            "list ranking results",
        )
        return tuple(self._result_from_row(row) for row in rows)


class SQLiteAnalysisRepository(_SQLiteRepository):
    _columns = """id, work_id, work_version_id, analysis_type, status, model_profile_id,
        prompt_version_id, input_fingerprint, started_at, completed_at, duration_ms, error_code,
        error_detail, output_json, created_at"""

    @staticmethod
    def _from_row(row: sqlite3.Row) -> AnalysisRun:
        return _model(
            AnalysisRun,
            {
                "id": row["id"],
                "work_id": row["work_id"],
                "work_version_id": row["work_version_id"],
                "analysis_type": row["analysis_type"],
                "status": row["status"],
                "model_profile_id": row["model_profile_id"],
                "prompt_version_id": row["prompt_version_id"],
                "input_fingerprint": row["input_fingerprint"],
                "started_at": row["started_at"],
                "completed_at": row["completed_at"],
                "duration_ms": row["duration_ms"],
                "error_code": row["error_code"],
                "error_detail": row["error_detail"],
                "output": None
                if row["output_json"] is None
                else _decode_json(row["output_json"], "output_json"),
                "created_at": row["created_at"],
            },
        )

    @staticmethod
    def _params(run: AnalysisRun) -> tuple[object, ...]:
        return (
            run.id,
            run.work_id,
            run.work_version_id,
            run.analysis_type.value,
            run.status.value,
            run.model_profile_id,
            run.prompt_version_id,
            run.input_fingerprint,
            _datetime(run.started_at),
            _datetime(run.completed_at),
            run.duration_ms,
            run.error_code,
            run.error_detail,
            None if run.output is None else _json(run.output),
            _datetime(run.created_at),
        )

    def create_or_get(self, run: AnalysisRun) -> CreateResult[AnalysisRun]:
        self._require_transaction()
        existing = self._fetchone(
            f"""SELECT {self._columns} FROM analysis_runs WHERE analysis_type=?
            AND input_fingerprint=? AND model_profile_id IS ? AND prompt_version_id IS ?""",
            (
                run.analysis_type.value,
                run.input_fingerprint,
                run.model_profile_id,
                run.prompt_version_id,
            ),
            "deduplicate analysis run",
        )
        if existing is not None:
            return CreateResult(self._from_row(existing), False)
        self._execute_write(
            f"INSERT INTO analysis_runs ({self._columns}) VALUES ({','.join('?' for _ in range(15))})",
            self._params(run),
            "create analysis run",
        )
        return CreateResult(run, True)

    def get(self, run_id: str) -> AnalysisRun | None:
        row = self._fetchone(
            f"SELECT {self._columns} FROM analysis_runs WHERE id=?", (run_id,), "get analysis run"
        )
        return None if row is None else self._from_row(row)

    def update(self, run: AnalysisRun) -> AnalysisRun:
        params = self._params(run)
        self._execute_write(
            """UPDATE analysis_runs SET work_id=?, work_version_id=?, analysis_type=?, status=?,
            model_profile_id=?, prompt_version_id=?, input_fingerprint=?, started_at=?, completed_at=?, duration_ms=?,
            error_code=?, error_detail=?, output_json=?, created_at=? WHERE id=?""",
            (*params[1:], run.id),
            "update analysis run",
        )
        return run

    def list(
        self, page: PageRequest, filters: AnalysisRunFilter = _ANALYSIS_FILTER
    ) -> tuple[AnalysisRun, ...]:
        clause, params = _where(
            [
                ("work_id", filters.work_id),
                ("analysis_type", filters.analysis_type),
                ("status", filters.status),
            ]
        )
        rows = self._fetchall(
            f"SELECT {self._columns} FROM analysis_runs{clause} ORDER BY created_at DESC, id LIMIT ? OFFSET ?",
            [*params, page.limit, page.offset],
            "list analysis runs",
        )
        return tuple(self._from_row(row) for row in rows)


class SQLitePipelineRunRepository(_SQLiteRepository):
    _columns = "id, run_type, trigger_type, status, config_snapshot_json, queued_at, started_at, completed_at, error_summary"

    @staticmethod
    def _from_row(row: sqlite3.Row) -> PipelineRun:
        return _model(
            PipelineRun,
            {
                "id": row["id"],
                "run_type": row["run_type"],
                "trigger_type": row["trigger_type"],
                "status": row["status"],
                "config_snapshot": _decode_json(
                    row["config_snapshot_json"], "config_snapshot_json"
                ),
                "queued_at": row["queued_at"],
                "started_at": row["started_at"],
                "completed_at": row["completed_at"],
                "error_summary": row["error_summary"],
            },
        )

    @staticmethod
    def _params(run: PipelineRun) -> tuple[object, ...]:
        return (
            run.id,
            run.run_type.value,
            run.trigger_type.value,
            run.status.value,
            _json(run.config_snapshot),
            _datetime(run.queued_at),
            _datetime(run.started_at),
            _datetime(run.completed_at),
            run.error_summary,
        )

    def create(self, run: PipelineRun) -> PipelineRun:
        self._execute_write(
            f"INSERT INTO pipeline_runs ({self._columns}) VALUES ({','.join('?' for _ in range(9))})",
            self._params(run),
            "create pipeline run",
        )
        return run

    def get(self, run_id: str) -> PipelineRun | None:
        row = self._fetchone(
            f"SELECT {self._columns} FROM pipeline_runs WHERE id=?", (run_id,), "get pipeline run"
        )
        return None if row is None else self._from_row(row)

    def update(self, run: PipelineRun) -> PipelineRun:
        params = self._params(run)
        self._execute_write(
            """UPDATE pipeline_runs SET run_type=?, trigger_type=?, status=?, config_snapshot_json=?,
            queued_at=?, started_at=?, completed_at=?, error_summary=? WHERE id=?""",
            (*params[1:], run.id),
            "update pipeline run",
        )
        return run

    def list(
        self, page: PageRequest, filters: PipelineRunFilter = _PIPELINE_FILTER
    ) -> tuple[PipelineRun, ...]:
        clause, params = _where(
            [
                ("run_type", filters.run_type),
                ("trigger_type", filters.trigger_type),
                ("status", filters.status),
            ]
        )
        rows = self._fetchall(
            f"SELECT {self._columns} FROM pipeline_runs{clause} ORDER BY queued_at DESC, id LIMIT ? OFFSET ?",
            [*params, page.limit, page.offset],
            "list pipeline runs",
        )
        return tuple(self._from_row(row) for row in rows)


@dataclass(frozen=True, slots=True)
class SQLiteRepositories:
    """Repository set sharing one caller-owned connection and transaction boundary."""

    sources: SQLiteSourceRepository
    source_records: SQLiteSourceRecordRepository
    works: SQLiteWorkRepository
    catalog_identities: SQLiteCatalogIdentityRepository
    topics: SQLiteTopicRepository
    work_versions: SQLiteWorkVersionRepository
    documents: SQLiteDocumentRepository
    rankings: SQLiteRankingRepository
    analyses: SQLiteAnalysisRepository
    pipeline_runs: SQLitePipelineRunRepository

    @classmethod
    def for_connection(cls, connection: sqlite3.Connection) -> SQLiteRepositories:
        return cls(
            sources=SQLiteSourceRepository(connection),
            source_records=SQLiteSourceRecordRepository(connection),
            works=SQLiteWorkRepository(connection),
            catalog_identities=SQLiteCatalogIdentityRepository(connection),
            topics=SQLiteTopicRepository(connection),
            work_versions=SQLiteWorkVersionRepository(connection),
            documents=SQLiteDocumentRepository(connection),
            rankings=SQLiteRankingRepository(connection),
            analyses=SQLiteAnalysisRepository(connection),
            pipeline_runs=SQLitePipelineRunRepository(connection),
        )
