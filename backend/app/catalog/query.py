"""Parameterized SQLite catalog queries optimized for the Explore read path."""

from __future__ import annotations

import re
import sqlite3
from collections import defaultdict
from datetime import UTC, date, datetime, time, timedelta
from typing import Protocol
from urllib.parse import quote

from app.catalog.read_models import (
    CatalogAuthor,
    CatalogFilterOptions,
    CatalogIdentity,
    CatalogPaper,
    CatalogPaperPage,
    CatalogPaperQuery,
    CatalogSort,
    CatalogSourceOption,
    CatalogTopic,
)
from app.domain.models import ExternalIdType
from app.repositories.sqlite import RepositoryDataError, RepositoryError

_TOKEN = re.compile(r"[^\W_]+", re.UNICODE)
_BASE_FROM = """
FROM works AS w
JOIN work_versions AS v ON v.id = w.current_version_id AND v.is_current = 1
JOIN source_records AS sr ON sr.id = v.source_record_id
JOIN sources AS s ON s.id = sr.source_id
"""
_BASE_WHERE = """
w.work_type = 'paper'
AND w.lifecycle_state NOT IN ('failed', 'rejected', 'superseded')
"""
_SORT_SQL = {
    CatalogSort.NEWEST: "publication_date IS NULL, publication_date DESC, w.id ASC",
    CatalogSort.OLDEST: "publication_date IS NULL, publication_date ASC, w.id ASC",
    CatalogSort.TITLE: "w.normalized_title ASC, w.id ASC",
    CatalogSort.UPDATED: "w.updated_at DESC, w.id ASC",
}


class CatalogReadRepository(Protocol):
    def list_papers(self, query: CatalogPaperQuery) -> CatalogPaperPage: ...
    def get_paper(self, paper_id: str) -> CatalogPaper | None: ...
    def filter_options(self) -> CatalogFilterOptions: ...


class SQLiteCatalogReadRepository:
    """Read-only catalog projection; every user value remains a SQL parameter."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def list_papers(self, query: CatalogPaperQuery) -> CatalogPaperPage:
        filters, parameters = self._filters(query)
        where_sql = f"WHERE {_BASE_WHERE}{filters}"
        try:
            total_row = self._connection.execute(
                f"SELECT COUNT(*) {_BASE_FROM} {where_sql}", parameters
            ).fetchone()
            rows = self._connection.execute(
                f"""SELECT w.id, v.title, v.abstract, w.publication_status,
                COALESCE(v.published_at, w.first_published_at) AS publication_date,
                w.updated_at, v.version_label, s.source_key, s.display_name
                {_BASE_FROM} {where_sql}
                ORDER BY {_SORT_SQL[query.sort]} LIMIT ? OFFSET ?""",
                (*parameters, query.limit, query.offset),
            ).fetchall()
        except sqlite3.Error as error:
            raise RepositoryError("query catalog papers failed") from error
        total = 0 if total_row is None else int(total_row[0])
        papers = self._hydrate(rows, self._match_reason(query))
        return CatalogPaperPage(
            items=papers,
            total=total,
            limit=query.limit,
            offset=query.offset,
            has_more=query.offset + len(papers) < total,
        )

    def get_paper(self, paper_id: str) -> CatalogPaper | None:
        try:
            rows = self._connection.execute(
                f"""SELECT w.id, v.title, v.abstract, w.publication_status,
                COALESCE(v.published_at, w.first_published_at) AS publication_date,
                w.updated_at, v.version_label, s.source_key, s.display_name
                {_BASE_FROM} WHERE {_BASE_WHERE} AND w.id = ?""",
                (paper_id,),
            ).fetchall()
        except sqlite3.Error as error:
            raise RepositoryError("get catalog paper failed") from error
        papers = self._hydrate(rows, None)
        return None if not papers else papers[0]

    def filter_options(self) -> CatalogFilterOptions:
        try:
            topic_rows = self._connection.execute(
                """SELECT DISTINCT t.topic_key, t.display_name FROM topics AS t
                JOIN work_topics AS wt ON wt.topic_id = t.id
                JOIN works AS w ON w.id = wt.work_id
                WHERE t.active = 1 AND w.work_type = 'paper'
                ORDER BY t.display_name, t.topic_key"""
            ).fetchall()
            source_rows = self._connection.execute(
                f"""SELECT DISTINCT s.source_key, s.display_name {_BASE_FROM}
                WHERE {_BASE_WHERE} ORDER BY s.display_name, s.source_key"""
            ).fetchall()
        except sqlite3.Error as error:
            raise RepositoryError("query catalog filter options failed") from error
        return CatalogFilterOptions(
            topics=tuple(
                CatalogTopic(key=str(row["topic_key"]), name=str(row["display_name"]))
                for row in topic_rows
            ),
            sources=tuple(
                CatalogSourceOption(key=str(row["source_key"]), name=str(row["display_name"]))
                for row in source_rows
            ),
        )

    @staticmethod
    def _filters(query: CatalogPaperQuery) -> tuple[str, tuple[object, ...]]:
        clauses: list[str] = []
        parameters: list[object] = []
        if query.q is not None:
            clauses.append(
                "AND w.id IN (SELECT entity_id FROM knowledge_fts "
                "WHERE entity_type = 'work' AND knowledge_fts MATCH ?)"
            )
            parameters.append(SQLiteCatalogReadRepository._fts_query(query.q))
        if query.topic is not None:
            clauses.append(
                "AND EXISTS (SELECT 1 FROM work_topics AS wt "
                "JOIN topics AS t ON t.id = wt.topic_id "
                "WHERE wt.work_id = w.id AND t.topic_key = ?)"
            )
            parameters.append(query.topic)
        if query.source is not None:
            clauses.append("AND s.source_key = ?")
            parameters.append(query.source)
        if query.published_from is not None:
            clauses.append("AND COALESCE(v.published_at, w.first_published_at) >= ?")
            parameters.append(SQLiteCatalogReadRepository._start_of_day(query.published_from))
        if query.published_to is not None:
            clauses.append("AND COALESCE(v.published_at, w.first_published_at) < ?")
            parameters.append(
                SQLiteCatalogReadRepository._start_of_day(query.published_to + timedelta(days=1))
            )
        return " " + " ".join(clauses) if clauses else "", tuple(parameters)

    @staticmethod
    def _fts_query(value: str) -> str:
        tokens = _TOKEN.findall(value.casefold())
        if not tokens:
            raise RepositoryDataError("catalog search contains no searchable tokens")
        return " AND ".join(f'"{token}"*' for token in tokens)

    @staticmethod
    def _start_of_day(value: date) -> str:
        return datetime.combine(value, time.min, tzinfo=UTC).isoformat().replace("+00:00", "Z")

    @staticmethod
    def _match_reason(query: CatalogPaperQuery) -> str | None:
        reasons: list[str] = []
        if query.q:
            reasons.append("keyword match")
        if query.topic:
            reasons.append("topic filter")
        if query.source:
            reasons.append("source filter")
        if query.published_from or query.published_to:
            reasons.append("publication date filter")
        return None if not reasons else ", ".join(reasons)

    def _hydrate(
        self, rows: list[sqlite3.Row], match_reason: str | None
    ) -> tuple[CatalogPaper, ...]:
        if not rows:
            return ()
        work_ids = tuple(str(row["id"]) for row in rows)
        placeholders = ",".join("?" for _ in work_ids)
        try:
            author_rows = self._connection.execute(
                f"""SELECT wa.work_id, a.display_name, wa.author_order, a.orcid
                FROM work_authors AS wa JOIN authors AS a ON a.id = wa.author_id
                WHERE wa.work_id IN ({placeholders})
                ORDER BY wa.work_id, wa.author_order, a.id""",
                work_ids,
            ).fetchall()
            identity_rows = self._connection.execute(
                f"""SELECT work_id, id_type, normalized_value FROM external_ids
                WHERE work_id IN ({placeholders})
                ORDER BY work_id, id_type, normalized_value""",
                work_ids,
            ).fetchall()
            topic_rows = self._connection.execute(
                f"""SELECT DISTINCT wt.work_id, t.topic_key, t.display_name
                FROM work_topics AS wt JOIN topics AS t ON t.id = wt.topic_id
                WHERE wt.work_id IN ({placeholders}) AND t.active = 1
                ORDER BY wt.work_id, t.display_name, t.topic_key""",
                work_ids,
            ).fetchall()
        except sqlite3.Error as error:
            raise RepositoryError("hydrate catalog papers failed") from error

        authors: defaultdict[str, list[CatalogAuthor]] = defaultdict(list)
        identities: defaultdict[str, list[CatalogIdentity]] = defaultdict(list)
        topics: defaultdict[str, list[CatalogTopic]] = defaultdict(list)
        for row in author_rows:
            authors[str(row["work_id"])].append(
                CatalogAuthor(
                    display_name=str(row["display_name"]),
                    order=int(row["author_order"]),
                    orcid=None if row["orcid"] is None else str(row["orcid"]),
                )
            )
        for row in identity_rows:
            id_type = ExternalIdType(str(row["id_type"]))
            value = str(row["normalized_value"])
            identities[str(row["work_id"])].append(
                CatalogIdentity(
                    id_type=id_type,
                    value=value,
                    external_url=self._identity_url(id_type, value),
                )
            )
        for row in topic_rows:
            topics[str(row["work_id"])].append(
                CatalogTopic(key=str(row["topic_key"]), name=str(row["display_name"]))
            )

        papers: list[CatalogPaper] = []
        for row in rows:
            work_id = str(row["id"])
            paper_identities = tuple(identities[work_id])
            primary_url = next(
                (
                    identity.external_url
                    for identity in paper_identities
                    if identity.id_type is ExternalIdType.ARXIV
                ),
                next(
                    (
                        identity.external_url
                        for identity in paper_identities
                        if identity.external_url is not None
                    ),
                    None,
                ),
            )
            papers.append(
                CatalogPaper.model_validate(
                    {
                        "id": work_id,
                        "title": row["title"],
                        "abstract": row["abstract"],
                        "publication_status": row["publication_status"],
                        "published_at": row["publication_date"],
                        "updated_at": row["updated_at"],
                        "current_version": row["version_label"],
                        "authors": tuple(authors[work_id]),
                        "identities": paper_identities,
                        "topics": tuple(topics[work_id]),
                        "source_key": row["source_key"],
                        "source_name": row["display_name"],
                        "external_url": primary_url,
                        "match_reason": match_reason,
                    }
                )
            )
        return tuple(papers)

    @staticmethod
    def _identity_url(id_type: ExternalIdType, value: str) -> str | None:
        if id_type is ExternalIdType.ARXIV:
            return f"https://arxiv.org/abs/{quote(value, safe='.')}"
        if id_type is ExternalIdType.DOI:
            return f"https://doi.org/{quote(value, safe='/._-():')}"
        return None
