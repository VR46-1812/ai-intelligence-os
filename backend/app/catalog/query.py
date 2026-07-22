"""Parameterized SQLite catalog queries optimized for the Explore read path."""

from __future__ import annotations

import json
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
    CatalogRanking,
    CatalogSort,
    CatalogSourceOption,
    CatalogTopic,
)
from app.domain.models import ExternalIdType
from app.multisource.models import LinkedSourceEvidence
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
    CatalogSort.TECHNICAL: (
        "technical_score IS NULL, technical_score DESC, publication_date DESC, w.id ASC"
    ),
    CatalogSort.COMMERCIAL: (
        "commercial_score IS NULL, commercial_score DESC, publication_date DESC, w.id ASC"
    ),
    CatalogSort.DEEP_DIVE: "deep_score IS NULL, deep_score DESC, publication_date DESC, w.id ASC",
}

_CATALOG_SELECT = """w.id, v.title, v.abstract, w.publication_status,
COALESCE(v.published_at, w.first_published_at) AS publication_date,
COALESCE(v.published_at, w.first_published_at) AS submitted_date,
sr.updated_at_upstream AS arxiv_announced_date,
sr.observed_at AS locally_ingested_date,
w.updated_at, v.version_label, s.source_key, s.display_name,
(SELECT d.parse_status FROM documents d WHERE d.work_version_id=v.id AND d.document_role='paper_pdf'
 ORDER BY d.acquired_at DESC,d.id DESC LIMIT 1) document_parse_status,
(SELECT a.status FROM document_acquisition_attempts a WHERE a.work_version_id=v.id
 ORDER BY a.attempted_at DESC,a.id DESC LIMIT 1) acquisition_status,
(SELECT COUNT(*) FROM evidence_spans e JOIN documents ed ON ed.id=e.document_id
 WHERE ed.work_version_id=v.id) evidence_count,
(SELECT rr.total_score FROM ranking_results rr JOIN ranking_profiles rp ON rp.id=rr.profile_id
 WHERE rr.work_id=w.id AND rp.active=1 AND rr.score_kind='technical' LIMIT 1) technical_score,
(SELECT rr.components_json FROM ranking_results rr JOIN ranking_profiles rp ON rp.id=rr.profile_id
 WHERE rr.work_id=w.id AND rp.active=1 AND rr.score_kind='technical' LIMIT 1) technical_components,
(SELECT rr.calculated_at FROM ranking_results rr JOIN ranking_profiles rp ON rp.id=rr.profile_id
 WHERE rr.work_id=w.id AND rp.active=1 AND rr.score_kind='technical' LIMIT 1) ranking_calculated_at,
(SELECT rr.total_score FROM ranking_results rr JOIN ranking_profiles rp ON rp.id=rr.profile_id
 WHERE rr.work_id=w.id AND rp.active=1 AND rr.score_kind='commercial' LIMIT 1) commercial_score,
(SELECT rr.total_score FROM ranking_results rr JOIN ranking_profiles rp ON rp.id=rr.profile_id
 WHERE rr.work_id=w.id AND rp.active=1 AND rr.score_kind='deep_dive_priority' LIMIT 1) deep_score"""


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
                f"""SELECT {_CATALOG_SELECT}
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
                f"""SELECT {_CATALOG_SELECT}
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
                f"""SELECT source_key,display_name FROM (
                SELECT DISTINCT s.source_key, s.display_name {_BASE_FROM} WHERE {_BASE_WHERE}
                UNION SELECT DISTINCT s.source_key,s.display_name FROM sources s
                JOIN source_artifacts a ON a.source_key=s.source_key)
                ORDER BY display_name,source_key"""
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
            clauses.append(
                "AND (s.source_key = ? OR EXISTS (SELECT 1 FROM linked_events le "
                "JOIN linked_event_artifacts lea ON lea.event_id=le.id "
                "JOIN source_artifacts sa ON sa.id=lea.artifact_id "
                "WHERE le.primary_work_id=w.id AND sa.source_key=?))"
            )
            parameters.extend((query.source, query.source))
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
            source_artifact_rows = self._connection.execute(
                f"""SELECT e.primary_work_id work_id,a.*,l.relationship
                FROM linked_events e JOIN linked_event_artifacts l ON l.event_id=e.id
                JOIN source_artifacts a ON a.id=l.artifact_id
                WHERE e.primary_work_id IN ({placeholders})
                ORDER BY e.primary_work_id,a.authority DESC,a.source_key,a.id""",
                work_ids,
            ).fetchall()
        except sqlite3.Error as error:
            raise RepositoryError("hydrate catalog papers failed") from error

        authors: defaultdict[str, list[CatalogAuthor]] = defaultdict(list)
        identities: defaultdict[str, list[CatalogIdentity]] = defaultdict(list)
        topics: defaultdict[str, list[CatalogTopic]] = defaultdict(list)
        linked_sources: defaultdict[str, list[LinkedSourceEvidence]] = defaultdict(list)
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
        for row in source_artifact_rows:
            linked_sources[str(row["work_id"])].append(
                LinkedSourceEvidence(
                    artifact_id=str(row["id"]),
                    source_key=str(row["source_key"]),
                    artifact_type=str(row["artifact_type"]),
                    title=str(row["title"]),
                    canonical_url=str(row["canonical_url"]),
                    relationship=str(row["relationship"]),
                    content_class=str(row["content_class"]),
                    authority=float(row["authority"]),
                    freshness=float(row["freshness"]),
                    novelty=float(row["novelty"]),
                    published_at=row["published_at"],
                )
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
                        "submitted_at": row["submitted_date"],
                        "arxiv_announced_at": row["arxiv_announced_date"],
                        "locally_ingested_at": row["locally_ingested_date"],
                        "updated_at": row["updated_at"],
                        "current_version": row["version_label"],
                        "authors": tuple(authors[work_id]),
                        "identities": paper_identities,
                        "topics": tuple(topics[work_id]),
                        "source_key": row["source_key"],
                        "source_name": row["display_name"],
                        "external_url": primary_url,
                        "match_reason": match_reason,
                        "document_status": self._document_status(row),
                        "evidence_count": row["evidence_count"],
                        "ranking": CatalogRanking(
                            technical=row["technical_score"],
                            commercial=row["commercial_score"],
                            deep_dive_priority=row["deep_score"],
                            technical_components=(
                                {}
                                if row["technical_components"] is None
                                else json.loads(str(row["technical_components"]))
                            ),
                            calculated_at=row["ranking_calculated_at"],
                        ),
                        "linked_sources": tuple(linked_sources[work_id]),
                    }
                )
            )
        return tuple(papers)

    @staticmethod
    def _document_status(row: sqlite3.Row) -> str:
        if row["document_parse_status"] is not None:
            return str(row["document_parse_status"])
        if row["acquisition_status"] is not None:
            return str(row["acquisition_status"])
        return "not_acquired"

    @staticmethod
    def _identity_url(id_type: ExternalIdType, value: str) -> str | None:
        if id_type is ExternalIdType.ARXIV:
            return f"https://arxiv.org/abs/{quote(value, safe='.')}"
        if id_type is ExternalIdType.DOI:
            return f"https://doi.org/{quote(value, safe='/._-():')}"
        return None
