"""FastAPI routes for M2.3 discovery control and inspection."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from app.config import AppSettings
from app.db import SQLiteDatabase
from app.discovery.arxiv import ArxivDiscoverySyncExecutor
from app.discovery.models import (
    BoundedSyncRequest,
    ConnectorHealth,
    DiscoverySyncRequest,
    SourceSummary,
)
from app.discovery.service import (
    DiscoveryDisabledError,
    DiscoveryNotFoundError,
    DiscoveryService,
)
from app.domain.models import PipelineRun
from app.repositories import SQLiteRepositories
from app.sources.arxiv_ingestion import ArxivSyncResult

router = APIRouter(prefix="/api/discovery", tags=["discovery"])
public_router = APIRouter(tags=["discovery"])


async def get_discovery_service(request: Request) -> AsyncIterator[DiscoveryService]:
    settings: AppSettings = request.app.state.settings
    database: SQLiteDatabase = request.app.state.database
    sync_lock: asyncio.Lock = request.app.state.discovery_sync_lock
    connection = database.connect()
    repositories = SQLiteRepositories.for_connection(connection)
    try:
        yield DiscoveryService(
            repositories.sources,
            repositories.pipeline_runs,
            ArxivDiscoverySyncExecutor(
                settings,
                connection,
                repositories,
                sync_lock=sync_lock,
            ),
        )
    finally:
        connection.close()


DiscoveryServiceDependency = Annotated[DiscoveryService, Depends(get_discovery_service)]


def _not_found(error: DiscoveryNotFoundError) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error))


@router.get("/sources", response_model=list[SourceSummary])
async def list_sources(
    service: DiscoveryServiceDependency,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    enabled: bool | None = None,
) -> tuple[SourceSummary, ...]:
    return service.list_sources(limit=limit, offset=offset, enabled=enabled)


@router.get("/sources/{source_key}/health", response_model=ConnectorHealth)
async def connector_health(
    source_key: str,
    service: DiscoveryServiceDependency,
) -> ConnectorHealth:
    try:
        return service.connector_health(source_key)
    except DiscoveryNotFoundError as error:
        raise _not_found(error) from error


@router.get("/runs/{run_id}", response_model=PipelineRun)
async def inspect_run(
    run_id: str,
    service: DiscoveryServiceDependency,
) -> PipelineRun:
    try:
        return service.inspect_run(run_id)
    except DiscoveryNotFoundError as error:
        raise _not_found(error) from error


@router.post("/sync", response_model=ArxivSyncResult)
async def start_sync(
    request: DiscoverySyncRequest,
    service: DiscoveryServiceDependency,
) -> ArxivSyncResult:
    try:
        return await service.start_sync(request)
    except DiscoveryNotFoundError as error:
        raise _not_found(error) from error
    except DiscoveryDisabledError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error


@public_router.post("/sources/{source_key}/sync", response_model=ArxivSyncResult)
async def start_source_sync(
    source_key: str,
    request: BoundedSyncRequest,
    service: DiscoveryServiceDependency,
) -> ArxivSyncResult:
    if source_key != "arxiv":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source not found.")
    return await start_sync(
        DiscoverySyncRequest(
            source_key="arxiv",
            maximum_records=request.maximum_records,
            lookback_hours=request.lookback_hours,
        ),
        service,
    )
