"""Application service for bounded discovery control and inspection."""

from __future__ import annotations

from typing import Protocol

from app.discovery.models import ConnectorHealth, DiscoverySyncRequest, SourceSummary
from app.domain.models import PageRequest, PipelineRun, Source, SourceFilter
from app.domain.repositories import PipelineRunRepository, SourceRepository
from app.sources.arxiv_ingestion import ArxivSyncResult


class DiscoveryNotFoundError(LookupError):
    """Raised when a requested source or pipeline run does not exist."""


class DiscoveryDisabledError(RuntimeError):
    """Raised when a disabled source is explicitly started."""


class DiscoverySyncExecutor(Protocol):
    async def sync(self, request: DiscoverySyncRequest) -> ArxivSyncResult: ...


class DiscoveryService:
    def __init__(
        self,
        sources: SourceRepository,
        runs: PipelineRunRepository,
        sync_executor: DiscoverySyncExecutor,
    ) -> None:
        self._sources = sources
        self._runs = runs
        self._sync_executor = sync_executor

    def list_sources(
        self, *, limit: int = 50, offset: int = 0, enabled: bool | None = None
    ) -> tuple[SourceSummary, ...]:
        sources = self._sources.list(
            PageRequest(limit=limit, offset=offset),
            SourceFilter(enabled=enabled),
        )
        return tuple(SourceSummary.from_source(source) for source in sources)

    def connector_health(self, source_key: str) -> ConnectorHealth:
        source = self._source(source_key)
        return ConnectorHealth.from_source(source)

    def inspect_run(self, run_id: str) -> PipelineRun:
        run = self._runs.get(run_id)
        if run is None:
            raise DiscoveryNotFoundError(f"pipeline run not found: {run_id}")
        return run

    async def start_sync(self, request: DiscoverySyncRequest) -> ArxivSyncResult:
        source = self._source(request.source_key)
        if not source.enabled:
            raise DiscoveryDisabledError(f"source is disabled: {request.source_key}")
        return await self._sync_executor.sync(request)

    def _source(self, source_key: str) -> Source:
        source = self._sources.get_by_key(source_key)
        if source is None:
            raise DiscoveryNotFoundError(f"source not found: {source_key}")
        return source
