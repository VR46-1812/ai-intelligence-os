"""Bounded multi-source runner and deterministic cross-source entity resolution."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import cast

from app.catalog.identity import CatalogIdentityService, CatalogRecord, IdentityInput, new_ulid
from app.config import AppSettings
from app.db import transaction
from app.domain.models import (
    ExternalIdType,
    NormalizationStatus,
    PageRequest,
    PipelineStatus,
    PipelineTriggerType,
    SourceHealth,
    SourceRecord,
    SourceRecordFilter,
    TrustTier,
)
from app.ingestion.contracts import (
    ConnectorException,
    NormalizedRecord,
    RawSourceRecord,
    SourceConnector,
)
from app.ingestion.http import BoundedHttpClient
from app.ingestion.registry import SourceRegistry
from app.ingestion.runner import IngestionRunner
from app.ingestion.storage import RawPayloadError, RawPayloadStore
from app.multisource.models import (
    LinkedEvent,
    LinkedEventPage,
    LinkedSourceEvidence,
    MultiSourceSyncResult,
    SourceSyncCount,
)
from app.repositories import SQLiteRepositories
from app.sources.multisource import (
    GitHubConnector,
    HuggingFaceConnector,
    OpenReviewConnector,
    RssAtomConnector,
    XExportConnector,
    stable_artifact_key,
)


def _metadata_object(value: str) -> dict[str, object]:
    parsed = cast(object, json.loads(value))
    if not isinstance(parsed, dict):
        return {}
    return cast(dict[str, object], parsed)


class MultiSourceDiscoveryService:
    """Run enabled connectors independently and preserve every raw response before linking."""

    def __init__(
        self,
        settings: AppSettings,
        connection: sqlite3.Connection,
        repositories: SQLiteRepositories,
        *,
        id_factory: Callable[[], str] = new_ulid,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._settings = settings
        self._connection = connection
        self._repositories = repositories
        self._id_factory = id_factory
        self._clock = clock
        self._payloads = RawPayloadStore(
            settings.paths.data_root,
            settings.paths.raw_documents_root,
            settings.downloads.maximum_document_bytes,
        )

    async def sync(
        self,
        *,
        maximum_records: int = 5,
        lookback_hours: int = 168,
        trigger: PipelineTriggerType = PipelineTriggerType.MANUAL,
    ) -> MultiSourceSyncResult:
        if not 1 <= maximum_records <= 5:
            raise ValueError("multi-source maximum_records must be between 1 and 5")
        http = BoundedHttpClient.from_settings(
            self._settings.http, self._settings.downloads, self._settings.resources
        )
        counts: list[SourceSyncCount] = []
        try:
            connectors: list[SourceConnector] = []
            if self._settings.sources.openreview_enabled:
                connectors.append(
                    OpenReviewConnector(
                        http, self._settings.sources.openreview_venues, clock=self._clock
                    )
                )
            if self._settings.sources.huggingface_enabled:
                connectors.append(HuggingFaceConnector(http, clock=self._clock))
            if self._settings.sources.rss_enabled:
                connectors.append(
                    RssAtomConnector(http, self._settings.sources.rss_feeds, clock=self._clock)
                )
            feed_definitions = (
                ("youtube", self._settings.sources.youtube_feeds, TrustTier.C, "video"),
                (
                    "reddit",
                    self._settings.sources.reddit_feeds,
                    TrustTier.D,
                    "community_discussion",
                ),
                ("medium", self._settings.sources.medium_feeds, TrustTier.C, "article"),
                ("substack", self._settings.sources.substack_feeds, TrustTier.C, "article"),
                (
                    "watchlist",
                    self._settings.sources.watchlist_feeds,
                    TrustTier.B,
                    "watchlist_post",
                ),
            )
            connectors.extend(
                RssAtomConnector(
                    http,
                    feeds,
                    source_key=source_key,
                    trust_tier=trust_tier,
                    artifact_kind=artifact_kind,
                    clock=self._clock,
                )
                for source_key, feeds, trust_tier, artifact_kind in feed_definitions
                if feeds
            )
            for connector in connectors:
                counts.append(
                    await self._sync_connector(connector, maximum_records, lookback_hours, trigger)
                )
            if self._settings.sources.github_enrichment_enabled:
                repositories = tuple(
                    dict.fromkeys(
                        (*self._repository_urls(), *self._settings.sources.github_watchlist)
                    )
                )
                github = GitHubConnector(
                    http,
                    repositories[:maximum_records],
                    search_queries=self._settings.sources.github_search_queries,
                    token=None
                    if self._settings.sources.github_token is None
                    else self._settings.sources.github_token.get_secret_value(),
                    clock=self._clock,
                )
                counts.append(
                    await self._sync_connector(github, maximum_records, lookback_hours, trigger)
                )
        finally:
            await http.aclose()
        return MultiSourceSyncResult(
            sources=tuple(counts),
            total_fetched=sum(item.fetched for item in counts),
            total_normalized=sum(item.normalized for item in counts),
            events_updated=sum(item.linked for item in counts),
        )

    async def import_x_export(self, items: tuple[dict[str, object], ...]) -> SourceSyncCount:
        """Persist a bounded user-supplied export without network access."""
        if not 1 <= len(items) <= 5:
            raise ValueError("X export imports must contain between 1 and 5 records")
        source = self._repositories.sources.get_by_key("x-watchlist")
        if source is None:
            raise ValueError("X export source is not registered")
        if not source.enabled:
            with transaction(self._connection):
                self._repositories.sources.update(
                    source.model_copy(
                        update={"enabled": True, "health_status": SourceHealth.UNKNOWN}
                    )
                )
        return await self._sync_connector(
            XExportConnector(items, clock=self._clock),
            len(items),
            168,
            PipelineTriggerType.MANUAL,
        )

    async def _sync_connector(
        self,
        connector: SourceConnector,
        maximum_records: int,
        lookback_hours: int,
        trigger: PipelineTriggerType,
    ) -> SourceSyncCount:
        runner = IngestionRunner(
            SourceRegistry(self._repositories.sources, (connector,)),
            self._repositories.sources,
            self._repositories.source_records,
            self._repositories.pipeline_runs,
            self._payloads,
            lambda: transaction(self._connection),
            source_concurrency=self._settings.resources.source_download_concurrency,
            maximum_pages=1,
            id_factory=self._id_factory,
            clock=self._clock,
        )
        until = self._clock()
        result = await runner.run(
            connector.key,
            since=until - timedelta(hours=lookback_hours),
            until=until,
            page_size=maximum_records,
            trigger=trigger,
        )
        normalized = linked = 0
        if result.status is PipelineStatus.SUCCEEDED:
            normalized, linked = self._normalize_pending(connector)
        return SourceSyncCount(
            source_key=connector.key,
            status=result.status,
            fetched=result.records_seen,
            created=result.records_created,
            normalized=normalized,
            linked=linked,
            safe_message=result.safe_message,
        )

    def _normalize_pending(self, connector: SourceConnector) -> tuple[int, int]:
        source = self._repositories.sources.get_by_key(connector.key)
        if source is None:
            return 0, 0
        pending = self._repositories.source_records.list(
            PageRequest(limit=100),
            SourceRecordFilter(
                source_id=source.id, normalization_status=NormalizationStatus.PENDING
            ),
        )
        normalized_count = linked = 0
        for record in pending:
            try:
                normalized = connector.normalize(self._raw(connector.key, record))
                errors = connector.validate(normalized)
                if errors:
                    raise ValueError("; ".join(errors))
                with transaction(self._connection):
                    work_id = self.persist_normalized_artifact(normalized, record)
                    self._repositories.source_records.update(
                        record.model_copy(
                            update={
                                "normalization_status": NormalizationStatus.NORMALIZED,
                                "error_code": None,
                                "error_detail": None,
                            }
                        )
                    )
                normalized_count += 1
                linked += int(work_id is not None)
            except (ConnectorException, RawPayloadError, ValueError, sqlite3.Error) as error:
                with transaction(self._connection):
                    self._repositories.source_records.update(
                        record.model_copy(
                            update={
                                "normalization_status": NormalizationStatus.REJECTED,
                                "error_code": "NORMALIZATION_FAILED",
                                "error_detail": str(error)[:500],
                            }
                        )
                    )
        return normalized_count, linked

    def persist_normalized_artifact(
        self, normalized: NormalizedRecord, record: SourceRecord
    ) -> str | None:
        """Persist and link one validated record inside the caller's transaction."""
        work_id = self._resolve_work(normalized, record)
        artifact_id = self._upsert_artifact(normalized, record)
        self._upsert_event(artifact_id, normalized, work_id)
        return work_id

    def _raw(self, source_key: str, record: SourceRecord) -> RawSourceRecord:
        media = "application/atom+xml" if source_key == "official-rss" else "application/json"
        return RawSourceRecord(
            source_key=source_key,
            upstream_id=record.upstream_id,
            upstream_version=record.upstream_version,
            canonical_url=record.canonical_url,
            observed_at=record.observed_at,
            published_at=record.published_at,
            updated_at=record.updated_at_upstream,
            media_type=media,
            payload=self._payloads.load(record.raw_payload_path, record.payload_sha256),
            response_metadata={},
        )

    def _resolve_work(self, normalized: NormalizedRecord, record: SourceRecord) -> str | None:
        for identity in normalized.identities:
            if identity.id_type not in {
                ExternalIdType.ARXIV,
                ExternalIdType.OPENREVIEW,
                ExternalIdType.DOI,
            }:
                continue
            row = self._connection.execute(
                "SELECT work_id FROM external_ids WHERE id_type=? AND normalized_value=?",
                (identity.id_type.value, identity.normalized_value),
            ).fetchone()
            if row is not None:
                return str(row["work_id"])
        if normalized.source_key != "openreview":
            return self._work_for_explicit_repository(normalized.repository_urls)
        resolution = CatalogIdentityService(
            self._repositories.works,
            self._repositories.work_versions,
            self._repositories.catalog_identities,
            id_factory=self._id_factory,
            clock=self._clock,
        ).resolve(
            CatalogRecord(
                source_record_id=record.id,
                work_type=normalized.work_type,
                title=normalized.title,
                abstract=normalized.abstract,
                publication_status=normalized.publication_status,
                published_at=normalized.published_at,
                observed_at=record.observed_at,
                upstream_version=normalized.upstream_version,
                content_sha256=record.payload_sha256,
                first_author=None if not normalized.authors else normalized.authors[0].display_name,
                identities=tuple(
                    IdentityInput(id_type=item.id_type, raw_value=item.raw_value)
                    for item in normalized.identities
                    if item.id_type
                    in {ExternalIdType.ARXIV, ExternalIdType.OPENREVIEW, ExternalIdType.DOI}
                ),
                metadata={
                    "source_key": normalized.source_key,
                    "repository_urls": list(normalized.repository_urls),
                    "extra": normalized.extra,
                },
            )
        )
        return resolution.work_id

    def _work_for_explicit_repository(self, urls: tuple[str, ...]) -> str | None:
        if not urls:
            return None
        wanted = {url.casefold().removesuffix(".git").rstrip("/") for url in urls}
        rows = self._connection.execute(
            "SELECT work_id,metadata_json FROM work_versions WHERE is_current=1"
        ).fetchall()
        for row in rows:
            try:
                metadata = _metadata_object(str(row["metadata_json"]))
            except json.JSONDecodeError:
                continue
            stored_value = metadata.get("repository_urls", [])
            stored = cast(list[object], stored_value) if isinstance(stored_value, list) else []
            if any(
                isinstance(url, str) and url.casefold().removesuffix(".git").rstrip("/") in wanted
                for url in stored
            ):
                return str(row["work_id"])
        return None

    def _upsert_artifact(self, normalized: NormalizedRecord, record: SourceRecord) -> str:
        existing = self._connection.execute(
            "SELECT id FROM source_artifacts WHERE source_key=? AND upstream_id=?",
            (normalized.source_key, normalized.upstream_id),
        ).fetchone()
        artifact_id = (
            str(existing["id"])
            if existing
            else stable_artifact_key(normalized.source_key, normalized.upstream_id)
        )
        artifact_type = self._artifact_type(normalized)
        now = self._clock()
        moment = normalized.updated_at or normalized.published_at or record.observed_at
        age_days = max(0.0, (now - moment).total_seconds() / 86400)
        freshness = max(0.0, 1.0 - age_days / 30)
        metadata = {
            "identities": [
                {"type": item.id_type.value, "value": item.normalized_value}
                for item in normalized.identities
            ],
            "repository_urls": list(normalized.repository_urls),
            "topics": list(normalized.source_topics),
            "extra": normalized.extra,
            "license": normalized.license_hint,
        }
        self._connection.execute(
            """INSERT INTO source_artifacts(
            id,source_record_id,source_key,upstream_id,artifact_type,source_type,title,summary,
            canonical_url,content_class,authority,freshness,novelty,published_at,
            updated_at,metadata_json,created_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(source_key,upstream_id) DO UPDATE SET
            source_record_id=excluded.source_record_id,title=excluded.title,
            summary=excluded.summary,canonical_url=excluded.canonical_url,
            source_type=excluded.source_type,
            content_class=excluded.content_class,
            authority=excluded.authority,freshness=excluded.freshness,novelty=0,
            updated_at=excluded.updated_at,metadata_json=excluded.metadata_json""",
            (
                artifact_id,
                record.id,
                normalized.source_key,
                normalized.upstream_id,
                artifact_type,
                self._source_type(normalized, artifact_type),
                normalized.title,
                normalized.abstract,
                normalized.canonical_url,
                (
                    "community_reaction"
                    if normalized.source_key in {"reddit", "x-watchlist"}
                    else "interpretation"
                    if normalized.source_key
                    in {"official-rss", "youtube", "medium", "substack", "watchlist"}
                    else "fact"
                ),
                self._authority(normalized),
                freshness,
                1.0 if existing is None else 0.0,
                self._iso(normalized.published_at),
                self._iso(normalized.updated_at),
                json.dumps(metadata, separators=(",", ":"), sort_keys=True),
                self._iso(now),
            ),
        )
        return artifact_id

    def _upsert_event(
        self, artifact_id: str, normalized: NormalizedRecord, work_id: str | None
    ) -> None:
        canonical_key = (
            f"work:{work_id}"
            if work_id
            else f"artifact:{normalized.source_key}:{normalized.upstream_id}"
        )
        existing = self._connection.execute(
            "SELECT id FROM linked_events WHERE canonical_key=?", (canonical_key,)
        ).fetchone()
        event_id = str(existing["id"]) if existing else self._id_factory()
        now = self._clock()
        self._connection.execute(
            """INSERT INTO linked_events(
            id,canonical_key,title,primary_work_id,occurred_at,corroboration,
            created_at,updated_at)
            VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(canonical_key) DO UPDATE SET
            title=excluded.title,
            occurred_at=COALESCE(excluded.occurred_at,linked_events.occurred_at),
            updated_at=excluded.updated_at""",
            (
                event_id,
                canonical_key,
                normalized.title,
                work_id,
                self._iso(normalized.updated_at or normalized.published_at),
                0.0,
                self._iso(now),
                self._iso(now),
            ),
        )
        relationship = self._relationship(normalized, work_id)
        basis = (
            "external_id"
            if work_id
            and any(
                item.id_type
                in {ExternalIdType.ARXIV, ExternalIdType.OPENREVIEW, ExternalIdType.DOI}
                for item in normalized.identities
            )
            else "explicit_url"
            if work_id
            else "canonical_work"
        )
        confidence = 1.0 if basis == "external_id" else 0.95 if basis == "explicit_url" else 1.0
        matching_evidence = (
            [
                f"{item.id_type.value}:{item.normalized_value}"
                for item in normalized.identities
                if item.id_type
                in {ExternalIdType.ARXIV, ExternalIdType.OPENREVIEW, ExternalIdType.DOI}
            ]
            if basis == "external_id"
            else list(normalized.repository_urls)
            if basis == "explicit_url"
            else [normalized.canonical_url]
        )
        self._connection.execute(
            """INSERT INTO linked_event_artifacts(
            event_id,artifact_id,relationship,resolution_basis,confidence,matching_evidence_json)
            VALUES(?,?,?,?,?,?) ON CONFLICT(event_id,artifact_id) DO UPDATE SET
            relationship=excluded.relationship,resolution_basis=excluded.resolution_basis,
            confidence=excluded.confidence,matching_evidence_json=excluded.matching_evidence_json,
            active=1,corrected_at=NULL,correction_reason=NULL""",
            (event_id, artifact_id, relationship, basis, confidence, json.dumps(matching_evidence)),
        )
        row = self._connection.execute(
            """SELECT COUNT(DISTINCT a.source_key) count
            FROM linked_event_artifacts l JOIN source_artifacts a ON a.id=l.artifact_id
            WHERE l.event_id=? AND l.active=1""",
            (event_id,),
        ).fetchone()
        corroboration = min(1.0, max(0, int(row["count"]) - 1) / 2) if row else 0.0
        self._connection.execute(
            "UPDATE linked_events SET corroboration=?,updated_at=? WHERE id=?",
            (corroboration, self._iso(now), event_id),
        )

    def _repository_urls(self) -> tuple[str, ...]:
        urls: set[str] = set()
        for row in self._connection.execute(
            """SELECT metadata_json FROM work_versions WHERE is_current=1
            UNION ALL SELECT metadata_json FROM source_artifacts"""
        ):
            try:
                metadata = _metadata_object(str(row["metadata_json"]))
            except json.JSONDecodeError:
                continue
            repository_values = metadata.get("repository_urls", [])
            repositories = (
                cast(list[object], repository_values) if isinstance(repository_values, list) else []
            )
            for value in repositories:
                if isinstance(value, str) and value.startswith("https://github.com/"):
                    urls.add(value)
        return tuple(sorted(urls))

    @staticmethod
    def _artifact_type(record: NormalizedRecord) -> str:
        if record.source_key in {
            "official-rss",
            "youtube",
            "reddit",
            "medium",
            "substack",
            "watchlist",
            "x-watchlist",
        }:
            return "official_post"
        if record.source_key == "github":
            return "release" if record.work_type.value == "release" else "repository"
        if record.source_key == "huggingface":
            return str(record.extra.get("kind", "model")).removesuffix("s")
        return "paper"

    @staticmethod
    def _source_type(record: NormalizedRecord, artifact_type: str) -> str:
        return {
            "youtube": "video",
            "reddit": "community_discussion",
            "medium": "article",
            "substack": "article",
            "watchlist": "watchlist_post",
            "x-watchlist": "x_post",
        }.get(record.source_key, artifact_type)

    @staticmethod
    def _relationship(record: NormalizedRecord, work_id: str | None) -> str:
        if record.source_key == "openreview":
            return "primary_research"
        if record.source_key == "github" and record.work_type.value == "release":
            return "release"
        if record.source_key == "github":
            return "official_repository" if work_id else "community_reference"
        if record.source_key == "huggingface":
            return "official_model" if work_id else "community_reference"
        if record.source_key == "official-rss":
            return "official_announcement" if work_id else "community_reference"
        return "community_reference"

    @staticmethod
    def _authority(record: NormalizedRecord) -> float:
        if record.source_key == "openreview":
            return 1.0
        if record.source_key == "official-rss":
            return 0.75
        if record.source_key in {"github", "huggingface"}:
            return 0.85
        if record.source_key == "watchlist":
            return 0.7
        if record.source_key in {"youtube", "medium", "substack"}:
            return 0.55
        if record.source_key in {"reddit", "x-watchlist"}:
            return 0.25
        return 0.5

    @staticmethod
    def _iso(value: datetime | None) -> str | None:
        return None if value is None else value.astimezone(UTC).isoformat().replace("+00:00", "Z")


class LinkedEventReader:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def list(
        self, *, limit: int = 20, offset: int = 0, source: str | None = None
    ) -> LinkedEventPage:
        if not 1 <= limit <= 50 or not 0 <= offset <= 100_000:
            raise ValueError("invalid event pagination")
        clause = ""
        if source is not None:
            clause = """WHERE EXISTS(
            SELECT 1 FROM linked_event_artifacts lx
            JOIN source_artifacts ax ON ax.id=lx.artifact_id
            WHERE lx.event_id=e.id AND lx.active=1 AND ax.source_key=?)"""
        params: tuple[object, ...] = () if source is None else (source,)
        total = int(
            self._connection.execute(
                f"SELECT COUNT(*) FROM linked_events e {clause}", params
            ).fetchone()[0]
        )
        rows = self._connection.execute(
            f"""SELECT * FROM linked_events e {clause}
            ORDER BY occurred_at IS NULL,occurred_at DESC,id LIMIT ? OFFSET ?""",
            (*params, limit, offset),
        ).fetchall()
        items = tuple(self._event(row) for row in rows)
        return LinkedEventPage(
            items=items,
            total=total,
            limit=limit,
            offset=offset,
            has_more=offset + len(items) < total,
        )

    def for_work(self, work_id: str) -> tuple[LinkedSourceEvidence, ...]:
        row = self._connection.execute(
            "SELECT * FROM linked_events WHERE primary_work_id=? ORDER BY updated_at DESC LIMIT 1",
            (work_id,),
        ).fetchone()
        return () if row is None else self._sources(str(row["id"]))

    def unlink(
        self,
        event_id: str,
        artifact_id: str,
        reason: str,
        *,
        corrected_at: datetime | None = None,
    ) -> bool:
        """Deactivate one mistaken association and deterministically refresh corroboration."""
        when = corrected_at or datetime.now(UTC)
        cursor = self._connection.execute(
            """UPDATE linked_event_artifacts SET active=0,corrected_at=?,correction_reason=?
            WHERE event_id=? AND artifact_id=? AND active=1""",
            (self._iso(when), reason, event_id, artifact_id),
        )
        if cursor.rowcount != 1:
            return False
        row = self._connection.execute(
            """SELECT COUNT(DISTINCT a.source_key)
            FROM linked_event_artifacts l JOIN source_artifacts a ON a.id=l.artifact_id
            WHERE l.event_id=? AND l.active=1""",
            (event_id,),
        ).fetchone()
        sources = 0 if row is None else int(row[0])
        corroboration = min(1.0, max(0, sources - 1) / 2)
        self._connection.execute(
            "UPDATE linked_events SET corroboration=?,updated_at=? WHERE id=?",
            (corroboration, self._iso(when), event_id),
        )
        return True

    @staticmethod
    def _iso(value: datetime) -> str:
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")

    def _event(self, row: sqlite3.Row) -> LinkedEvent:
        sources = self._sources(str(row["id"]))
        source_count = len({source.source_key for source in sources})
        authoritative = len(
            {
                source.source_key
                for source in sources
                if source.authority >= 0.7 and source.content_class == "fact"
            }
        )
        if source_count <= 1:
            classification = "artifact"
            status = "single_source"
        elif authoritative >= 2:
            classification = "corroborated_event"
            status = "corroborated"
        else:
            classification = "associated_event"
            status = "associated"
        confidence = sum(source.confidence for source in sources) / max(1, len(sources))
        linkage_reasons = tuple(
            dict.fromkeys(
                reason
                for source in sources
                for reason in (
                    *source.matching_evidence,
                    source.relationship.replace("_", " "),
                )
                if reason
            )
        )
        return LinkedEvent(
            id=str(row["id"]),
            title=str(row["title"]),
            primary_work_id=None if row["primary_work_id"] is None else str(row["primary_work_id"]),
            occurred_at=row["occurred_at"],
            corroboration=float(row["corroboration"]),
            source_count=source_count,
            classification=classification,
            corroboration_status=status,
            association_confidence=confidence,
            linkage_reason=(
                "; ".join(linkage_reasons[:3])
                if linkage_reasons
                else "No cross-source association has been established."
            ),
            sources=sources,
        )

    def _sources(self, event_id: str) -> tuple[LinkedSourceEvidence, ...]:
        rows = self._connection.execute(
            """SELECT a.*,l.relationship,l.confidence,l.matching_evidence_json
            FROM linked_event_artifacts l
            JOIN source_artifacts a ON a.id=l.artifact_id WHERE l.event_id=? AND l.active=1
            ORDER BY a.authority DESC,a.source_key,a.id""",
            (event_id,),
        ).fetchall()
        return tuple(
            LinkedSourceEvidence(
                artifact_id=str(row["id"]),
                source_key=str(row["source_key"]),
                artifact_type=str(row["artifact_type"]),
                source_type=str(row["source_type"]),
                title=str(row["title"]),
                canonical_url=str(row["canonical_url"]),
                relationship=str(row["relationship"]),
                confidence=float(row["confidence"]),
                matching_evidence=tuple(json.loads(str(row["matching_evidence_json"]))),
                content_class=str(row["content_class"]),
                authority=float(row["authority"]),
                freshness=float(row["freshness"]),
                novelty=float(row["novelty"]),
                published_at=row["published_at"],
            )
            for row in rows
        )
