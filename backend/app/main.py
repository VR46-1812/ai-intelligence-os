"""FastAPI application entry point for the local modular monolith."""

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from enum import StrEnum

from fastapi import FastAPI
from pydantic import BaseModel, ConfigDict

from app.analysis.api import router as analysis_router
from app.catalog.api import router as catalog_router
from app.catalog.taxonomy import TopicTaxonomyService, load_default_taxonomy
from app.config import AppSettings, initialize_directories, load_settings
from app.db import MigrationRunner, SQLiteDatabase, transaction
from app.discovery.api import public_router as public_discovery_router
from app.discovery.api import router as discovery_router
from app.documents.api import router as documents_router
from app.intelligence.api import router as intelligence_router
from app.operations.api import router as operations_router
from app.operations.scheduler import DailyScheduler
from app.operations.service import ProductionDailyRunner
from app.ranking.api import router as ranking_router
from app.repositories import SQLiteRepositories
from app.sources.catalog import upsert_arxiv_source


class HealthStatus(StrEnum):
    """Public health states exposed by the application."""

    OK = "ok"


class HealthResponse(BaseModel):
    """Stable response contract for the lightweight health endpoint."""

    model_config = ConfigDict(frozen=True)

    service: str
    status: HealthStatus


async def health() -> HealthResponse:
    """Report that the API process is available."""
    return HealthResponse(service="ai-intelligence-os", status=HealthStatus.OK)


def create_app(settings: AppSettings | None = None) -> FastAPI:
    """Create the API application and validate its local configuration."""
    resolved_settings = settings if settings is not None else load_settings()
    database = SQLiteDatabase(
        resolved_settings.paths.database_path,
        resolved_settings.database.busy_timeout_ms,
    )
    discovery_lock = asyncio.Lock()
    daily_lock = asyncio.Lock()
    generation_semaphore = asyncio.Semaphore(resolved_settings.resources.llm_generation_concurrency)
    daily_runner = ProductionDailyRunner(
        resolved_settings,
        database,
        daily_lock,
        discovery_lock,
        generation_semaphore,
    )
    daily_scheduler = DailyScheduler(daily_runner, resolved_settings.scheduler)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncGenerator[None]:
        initialize_directories(resolved_settings.paths)
        MigrationRunner(database).migrate()
        connection = database.connect()
        try:
            with transaction(connection):
                repositories = SQLiteRepositories.for_connection(connection)
                TopicTaxonomyService(repositories.topics, load_default_taxonomy()).seed()
                upsert_arxiv_source(
                    repositories.sources,
                    resolved_settings.sources,
                    now=datetime.now(UTC),
                )
        finally:
            connection.close()
        await daily_scheduler.start()
        try:
            yield
        finally:
            await daily_scheduler.stop()

    application = FastAPI(
        title="AI Intelligence OS API",
        description="Local-first AI research intelligence API.",
        version="0.1.0",
        lifespan=lifespan,
    )
    application.state.settings = resolved_settings
    application.state.database = database
    application.state.discovery_sync_lock = discovery_lock
    application.state.llm_generation_semaphore = generation_semaphore
    application.state.daily_runner = daily_runner
    application.state.daily_scheduler = daily_scheduler

    application.add_api_route(
        "/health",
        health,
        methods=["GET"],
        response_model=HealthResponse,
        tags=["system"],
    )
    application.include_router(discovery_router)
    application.include_router(public_discovery_router)
    application.include_router(catalog_router)
    application.include_router(documents_router)
    application.include_router(ranking_router)
    application.include_router(analysis_router)
    application.include_router(intelligence_router)
    application.include_router(operations_router)

    return application


app = create_app()
