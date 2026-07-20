"""Deterministic persisted registration for implemented source connectors."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from app.catalog.identity import new_ulid
from app.config import SourceSettings
from app.domain.models import JsonObject, Source, SourceHealth, TrustTier
from app.domain.repositories import SourceRepository
from app.sources.arxiv import ARXIV_MINIMUM_REQUEST_INTERVAL_MS, ArxivConnector


def upsert_arxiv_source(
    repository: SourceRepository,
    settings: SourceSettings,
    *,
    now: datetime,
    id_factory: Callable[[], str] = new_ulid,
) -> Source:
    """Register configured arXiv discovery without performing network I/O."""
    config: JsonObject = {"categories": list(settings.arxiv_categories)}
    existing = repository.get_by_key("arxiv")
    if existing is None:
        return repository.create(
            Source(
                id=id_factory(),
                source_key="arxiv",
                display_name="arXiv",
                trust_tier=TrustTier.A,
                base_url="https://export.arxiv.org",
                enabled=settings.arxiv_enabled,
                poll_interval_minutes=60,
                minimum_request_interval_ms=ARXIV_MINIMUM_REQUEST_INTERVAL_MS,
                connector_version=ArxivConnector.connector_version,
                config=config,
                health_status=(
                    SourceHealth.UNKNOWN if settings.arxiv_enabled else SourceHealth.DISABLED
                ),
                created_at=now.astimezone(UTC),
                updated_at=now.astimezone(UTC),
            )
        )
    health = existing.health_status
    if not settings.arxiv_enabled:
        health = SourceHealth.DISABLED
    elif health is SourceHealth.DISABLED:
        health = SourceHealth.UNKNOWN
    return repository.update(
        existing.model_copy(
            update={
                "display_name": "arXiv",
                "trust_tier": TrustTier.A,
                "base_url": "https://export.arxiv.org",
                "enabled": settings.arxiv_enabled,
                "poll_interval_minutes": 60,
                "minimum_request_interval_ms": ARXIV_MINIMUM_REQUEST_INTERVAL_MS,
                "connector_version": ArxivConnector.connector_version,
                "config": config,
                "health_status": health,
                "updated_at": now.astimezone(UTC),
            }
        )
    )
