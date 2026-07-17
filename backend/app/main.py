"""FastAPI application entry point for the M0.1 scaffold."""

from enum import StrEnum

from fastapi import FastAPI
from pydantic import BaseModel, ConfigDict


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


def create_app() -> FastAPI:
    """Create the API application without starting external services."""
    application = FastAPI(
        title="AI Intelligence OS API",
        description="Local-first AI research intelligence API.",
        version="0.1.0",
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
