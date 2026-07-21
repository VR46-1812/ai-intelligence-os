"""Bounded production arXiv sync composition for discovery entry points."""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

from app.catalog.identity import CatalogIdentityService, new_ulid
from app.catalog.taxonomy import TopicTaxonomyService, load_default_taxonomy
from app.config import AppSettings
from app.db import transaction
from app.discovery.models import DiscoverySyncRequest
from app.domain.models import PipelineTriggerType
from app.ingestion.http import BoundedHttpClient
from app.ingestion.registry import SourceRegistry
from app.ingestion.runner import IngestionRunner
from app.ingestion.storage import RawPayloadStore
from app.repositories import SQLiteRepositories
from app.sources.arxiv import ArxivConnector
from app.sources.arxiv_ingestion import ArxivIngestionService, ArxivSyncResult

Sleep = Callable[[float], Awaitable[None]]


class ArxivDiscoverySyncExecutor:
    """Run one bounded arXiv page through raw capture and catalog normalization."""

    def __init__(
        self,
        settings: AppSettings,
        connection: sqlite3.Connection,
        repositories: SQLiteRepositories,
        *,
        id_factory: Callable[[], str] = new_ulid,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        sleep: Sleep = asyncio.sleep,
        sync_lock: asyncio.Lock | None = None,
    ) -> None:
        self._settings = settings
        self._connection = connection
        self._repositories = repositories
        self._id_factory = id_factory
        self._clock = clock
        self._sleep = sleep
        self._sync_lock = sync_lock if sync_lock is not None else asyncio.Lock()

    async def sync(
        self,
        request: DiscoverySyncRequest,
        *,
        trigger: PipelineTriggerType = PipelineTriggerType.MANUAL,
    ) -> ArxivSyncResult:
        async with self._sync_lock:
            return await self._sync_once(request, trigger)

    async def _sync_once(
        self, request: DiscoverySyncRequest, trigger: PipelineTriggerType
    ) -> ArxivSyncResult:
        source = self._repositories.sources.get_by_key(request.source_key)
        if source is None:
            raise RuntimeError("arXiv source registration is missing")
        if source.last_attempt_at is not None:
            elapsed = max(0.0, (self._clock() - source.last_attempt_at).total_seconds())
            delay = source.minimum_request_interval_ms / 1000 - elapsed
            if delay > 0:
                await self._sleep(delay)
        http = BoundedHttpClient.from_settings(
            self._settings.http,
            self._settings.downloads,
            self._settings.resources,
        )
        payload_store = RawPayloadStore(
            self._settings.paths.data_root,
            self._settings.paths.raw_documents_root,
            self._settings.downloads.maximum_document_bytes,
        )
        connector = ArxivConnector(
            http,
            self._settings.sources.arxiv_categories,
            minimum_request_interval_ms=source.minimum_request_interval_ms,
            maximum_pages_per_run=1,
            clock=self._clock,
        )
        runner = IngestionRunner(
            SourceRegistry(self._repositories.sources, (connector,)),
            self._repositories.sources,
            self._repositories.source_records,
            self._repositories.pipeline_runs,
            payload_store,
            lambda: transaction(self._connection),
            source_concurrency=self._settings.resources.source_download_concurrency,
            maximum_pages=1,
            id_factory=self._id_factory,
            clock=self._clock,
        )
        taxonomy = TopicTaxonomyService(self._repositories.topics, load_default_taxonomy())
        service = ArxivIngestionService(
            runner,
            connector,
            self._repositories.sources,
            self._repositories.source_records,
            CatalogIdentityService(
                self._repositories.works,
                self._repositories.work_versions,
                self._repositories.catalog_identities,
                id_factory=self._id_factory,
                clock=self._clock,
            ),
            self._repositories.catalog_identities,
            taxonomy,
            self._repositories.topics,
            payload_store,
            lambda: transaction(self._connection),
            id_factory=self._id_factory,
            clock=self._clock,
        )
        until = self._clock()
        try:
            return await service.sync(
                since=until - timedelta(hours=request.lookback_hours),
                until=until,
                page_size=request.maximum_records,
                trigger=trigger,
            )
        finally:
            await http.aclose()
