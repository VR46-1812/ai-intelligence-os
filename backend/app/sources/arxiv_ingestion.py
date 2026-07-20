"""arXiv raw capture followed by transactional catalog normalization."""

from __future__ import annotations

import re
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.catalog.identity import (
    CatalogIdentityService,
    CatalogRecord,
    IdentityInput,
    IdentityResolutionStatus,
    new_ulid,
)
from app.catalog.taxonomy import TopicTaxonomyService
from app.domain.models import (
    Author,
    NormalizationStatus,
    PageRequest,
    PipelineTriggerType,
    SourceRecord,
    SourceRecordFilter,
    TopicAssignmentMethod,
    WorkAuthor,
    WorkTopic,
)
from app.domain.repositories import (
    CatalogIdentityRepository,
    SourceRecordRepository,
    SourceRepository,
    TopicRepository,
)
from app.ingestion.contracts import ConnectorException, NormalizedAuthor, RawSourceRecord
from app.ingestion.runner import IngestionResult, IngestionRunner
from app.ingestion.storage import RawPayloadError, RawPayloadStore
from app.sources.arxiv import ArxivConnector, ArxivFetchedEntry

TransactionFactory = Callable[[], AbstractContextManager[object]]
_VERSION_NUMBER = re.compile(r"^v(?P<number>\d+)$", re.IGNORECASE)


class ArxivSyncResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    ingestion: IngestionResult
    records_normalized: int = Field(ge=0)
    records_rejected: int = Field(ge=0)
    works_created: int = Field(ge=0)
    revisions_created: int = Field(ge=0)
    already_known: int = Field(ge=0)
    manual_review: int = Field(ge=0)
    fetched_entries: tuple[ArxivFetchedEntry, ...] = ()


class ArxivIngestionService:
    """Run bounded arXiv capture and normalize every pending durable record."""

    def __init__(
        self,
        runner: IngestionRunner,
        connector: ArxivConnector,
        sources: SourceRepository,
        records: SourceRecordRepository,
        catalog: CatalogIdentityService,
        catalog_repository: CatalogIdentityRepository,
        taxonomy: TopicTaxonomyService,
        topics: TopicRepository,
        payload_store: RawPayloadStore,
        transaction_factory: TransactionFactory,
        *,
        id_factory: Callable[[], str] = new_ulid,
        clock: Callable[[], datetime],
    ) -> None:
        self._runner = runner
        self._connector = connector
        self._sources = sources
        self._records = records
        self._catalog = catalog
        self._catalog_repository = catalog_repository
        self._taxonomy = taxonomy
        self._topics = topics
        self._payload_store = payload_store
        self._transaction_factory = transaction_factory
        self._id_factory = id_factory
        self._clock = clock

    async def sync(
        self,
        *,
        since: datetime,
        until: datetime,
        page_size: int,
        trigger: PipelineTriggerType = PipelineTriggerType.MANUAL,
    ) -> ArxivSyncResult:
        ingestion = await self._runner.run(
            "arxiv",
            since=since,
            until=until,
            page_size=page_size,
            trigger=trigger,
        )
        pending = self._pending_records()
        normalized_count = 0
        rejected_count = 0
        works_created = 0
        revisions_created = 0
        already_known = 0
        manual_review = 0
        for record in pending:
            try:
                raw = self._raw_record(record)
                normalized = self._connector.normalize(raw)
                validation_errors = self._connector.validate(normalized)
            except (ConnectorException, RawPayloadError) as error:
                self._mark_failed(record, error)
                rejected_count += 1
                continue
            if validation_errors:
                self._mark_rejected(record, "; ".join(validation_errors))
                rejected_count += 1
                continue

            catalog_record = CatalogRecord(
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
                    IdentityInput(id_type=identity.id_type, raw_value=identity.raw_value)
                    for identity in normalized.identities
                ),
            )
            with self._transaction_factory():
                resolution = self._catalog.resolve(catalog_record)
                if resolution.status is IdentityResolutionStatus.MANUAL_REVIEW:
                    self._records.update(
                        record.model_copy(
                            update={
                                "normalization_status": NormalizationStatus.REJECTED,
                                "error_code": "MANUAL_REVIEW",
                                "error_detail": ",".join(resolution.candidate_work_ids),
                            }
                        )
                    )
                    manual_review += 1
                    rejected_count += 1
                    continue
                if resolution.work_id is None:
                    raise RuntimeError("catalog resolution omitted work_id")
                self._attach_authors(resolution.work_id, normalized.authors)
                self._assign_topics(
                    resolution.work_id,
                    normalized.source_topics,
                    record.observed_at,
                )
                self._records.update(
                    record.model_copy(
                        update={
                            "normalization_status": NormalizationStatus.NORMALIZED,
                            "error_code": None,
                            "error_detail": None,
                        }
                    )
                )
            normalized_count += 1
            works_created += int(resolution.status is IdentityResolutionStatus.CREATED)
            revisions_created += int(resolution.status is IdentityResolutionStatus.REVISION_CREATED)
            already_known += int(resolution.status is IdentityResolutionStatus.ALREADY_KNOWN)

        return ArxivSyncResult(
            ingestion=ingestion,
            records_normalized=normalized_count,
            records_rejected=rejected_count,
            works_created=works_created,
            revisions_created=revisions_created,
            already_known=already_known,
            manual_review=manual_review,
            fetched_entries=self._connector.fetched_entries,
        )

    def _pending_records(self) -> tuple[SourceRecord, ...]:
        source = self._sources.get_by_key("arxiv")
        if source is None:
            return ()
        records: list[SourceRecord] = []
        offset = 0
        while True:
            page = self._records.list(
                PageRequest(limit=100, offset=offset),
                SourceRecordFilter(
                    source_id=source.id,
                    normalization_status=NormalizationStatus.PENDING,
                ),
            )
            records.extend(page)
            if len(page) < 100:
                break
            offset += 100
        return tuple(sorted(records, key=self._revision_order))

    def _raw_record(self, record: SourceRecord) -> RawSourceRecord:
        return RawSourceRecord(
            source_key="arxiv",
            upstream_id=record.upstream_id,
            upstream_version=record.upstream_version,
            canonical_url=record.canonical_url,
            observed_at=record.observed_at,
            published_at=record.published_at,
            updated_at=record.updated_at_upstream,
            media_type="application/atom+xml",
            payload=self._payload_store.load(record.raw_payload_path, record.payload_sha256),
            response_metadata={},
        )

    def _attach_authors(self, work_id: str, authors: tuple[NormalizedAuthor, ...]) -> None:
        now = self._clock()
        for normalized in authors:
            if self._catalog_repository.get_work_author_by_order(work_id, normalized.order):
                continue
            author = self._catalog_repository.create_author(
                Author(
                    id=self._id_factory(),
                    normalized_name=normalized.normalized_name,
                    display_name=normalized.display_name,
                    orcid=normalized.orcid,
                    affiliation_text=normalized.affiliation,
                    created_at=now,
                    updated_at=now,
                )
            )
            self._catalog_repository.create_work_author(
                WorkAuthor(
                    work_id=work_id,
                    author_id=author.id,
                    author_order=normalized.order,
                )
            )

    def _assign_topics(
        self, work_id: str, source_categories: tuple[str, ...], created_at: datetime
    ) -> None:
        matched_keys = {
            match.topic_key
            for category in source_categories
            for match in self._taxonomy.map_source_category("arxiv", category)
        }
        if matched_keys - {"unknown"}:
            matched_keys.discard("unknown")
        if not matched_keys:
            matched_keys.add("unknown")
        explanation = f"arXiv categories: {', '.join(source_categories) or 'none'}"
        for topic_key in sorted(matched_keys):
            topic = self._topics.get_by_key(topic_key)
            if topic is None:
                raise RuntimeError(f"controlled topic is not seeded: {topic_key}")
            self._topics.assign_or_get(
                WorkTopic(
                    work_id=work_id,
                    topic_id=topic.id,
                    assignment_method=TopicAssignmentMethod.RULE,
                    confidence=1.0,
                    explanation=explanation,
                    created_at=created_at,
                )
            )

    def _mark_failed(self, record: SourceRecord, error: Exception) -> None:
        code = (
            error.failure.code.value
            if isinstance(error, ConnectorException)
            else "RAW_PAYLOAD_FAILED"
        )
        detail = (
            error.failure.safe_message
            if isinstance(error, ConnectorException)
            else "stored raw payload could not be verified"
        )
        with self._transaction_factory():
            self._records.update(
                record.model_copy(
                    update={
                        "normalization_status": NormalizationStatus.FAILED,
                        "error_code": code,
                        "error_detail": detail,
                    }
                )
            )

    def _mark_rejected(self, record: SourceRecord, detail: str) -> None:
        with self._transaction_factory():
            self._records.update(
                record.model_copy(
                    update={
                        "normalization_status": NormalizationStatus.REJECTED,
                        "error_code": "VALIDATION_FAILED",
                        "error_detail": detail[:1000],
                    }
                )
            )

    @staticmethod
    def _revision_order(record: SourceRecord) -> tuple[str, int, datetime]:
        match = (
            None
            if record.upstream_version is None
            else _VERSION_NUMBER.fullmatch(record.upstream_version)
        )
        version = 0 if match is None else int(match.group("number"))
        return (record.upstream_id, version, record.observed_at)
