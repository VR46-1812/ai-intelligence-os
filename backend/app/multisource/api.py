"""Public-safe V1.1 source registry, sync, and linked-event endpoints."""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field

from app.domain.models import PipelineTriggerType
from app.multisource.models import LinkedEventPage, MultiSourceSyncResult
from app.multisource.service import LinkedEventReader, MultiSourceDiscoveryService
from app.repositories import SQLiteRepositories

logger = logging.getLogger(__name__)
router = APIRouter(tags=["multi-source"])


class MultiSourceSyncRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    maximum_records: int = Field(default=5, ge=1, le=5)
    lookback_hours: int = Field(default=168, ge=1, le=168)


class RegistrySource(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    key: str
    name: str
    connector_version: str
    trust_tier: str
    enabled: bool
    health: str
    checkpoint: dict[str, object] | None
    minimum_request_interval_ms: int


@router.get("/sources/registry", response_model=tuple[RegistrySource, ...])
async def source_registry(request: Request) -> tuple[RegistrySource, ...]:
    connection = request.app.state.database.connect()
    try:
        rows = connection.execute(
            """SELECT source_key,display_name,connector_version,trust_tier,enabled,
            health_status,cursor_json,minimum_request_interval_ms
            FROM sources ORDER BY source_key"""
        ).fetchall()
        import json

        return tuple(
            RegistrySource(
                key=str(row["source_key"]),
                name=str(row["display_name"]),
                connector_version=str(row["connector_version"]),
                trust_tier=str(row["trust_tier"]),
                enabled=bool(row["enabled"]),
                health=str(row["health_status"]),
                checkpoint=None
                if row["cursor_json"] is None
                else json.loads(str(row["cursor_json"])),
                minimum_request_interval_ms=int(row["minimum_request_interval_ms"]),
            )
            for row in rows
        )
    finally:
        connection.close()


@router.post("/multi-source/sync", response_model=MultiSourceSyncResult)
async def sync_multi_sources(
    body: MultiSourceSyncRequest, request: Request
) -> MultiSourceSyncResult:
    lock = request.app.state.discovery_sync_lock
    if lock.locked():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A bounded discovery sync is already running.",
        )
    async with lock:
        connection = request.app.state.database.connect()
        try:
            return await MultiSourceDiscoveryService(
                request.app.state.settings,
                connection,
                SQLiteRepositories.for_connection(connection),
            ).sync(
                maximum_records=body.maximum_records,
                lookback_hours=body.lookback_hours,
                trigger=PipelineTriggerType.MANUAL,
            )
        except Exception as error:
            logger.exception("multi_source_sync_failed")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Multi-source discovery stopped safely; source checkpoints are preserved.",
            ) from error
        finally:
            connection.close()


@router.get("/events", response_model=LinkedEventPage)
async def linked_events(
    request: Request,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
    source: Annotated[str | None, Query(min_length=1, max_length=255)] = None,
) -> LinkedEventPage:
    connection = request.app.state.database.connect()
    try:
        return LinkedEventReader(connection).list(limit=limit, offset=offset, source=source)
    finally:
        connection.close()
