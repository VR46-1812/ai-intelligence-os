"""FastAPI application entry point for the M0.1 scaffold."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from enum import StrEnum

from fastapi import FastAPI
from pydantic import BaseModel, ConfigDict

from app.catalog.taxonomy import TopicTaxonomyService, load_default_taxonomy
from app.config import AppSettings, initialize_directories, load_settings
from app.db import MigrationRunner, SQLiteDatabase, transaction
from app.repositories import SQLiteRepositories


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

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncGenerator[None]:
        initialize_directories(resolved_settings.paths)
        database = SQLiteDatabase(
            resolved_settings.paths.database_path,
            resolved_settings.database.busy_timeout_ms,
        )
        MigrationRunner(database).migrate()
        connection = database.connect()
        try:
            with transaction(connection):
                TopicTaxonomyService(
                    SQLiteRepositories.for_connection(connection).topics,
                    load_default_taxonomy(),
                ).seed()
        finally:
            connection.close()
        yield

    application = FastAPI(
        title="AI Intelligence OS API",
        description="Local-first AI research intelligence API.",
        version="0.1.0",
        lifespan=lifespan,
    )

    application.add_api_route(
        "/health",
        health,
        methods=["GET"],
        response_model=HealthResponse,
        tags=["system"],
    )

    return application


app = create_app()
