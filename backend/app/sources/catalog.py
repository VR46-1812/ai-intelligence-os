"""Deterministic persisted registration for implemented source connectors."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from app.catalog.identity import new_ulid
from app.config import SourceSettings
from app.domain.models import JsonObject, Source, SourceHealth, TrustTier
from app.domain.repositories import SourceRepository
from app.sources.arxiv import ARXIV_MINIMUM_REQUEST_INTERVAL_MS, ArxivConnector
from app.sources.multisource import (
    GitHubConnector,
    HuggingFaceConnector,
    OpenReviewConnector,
    RssAtomConnector,
)


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


def upsert_multisource_registry(
    repository: SourceRepository,
    settings: SourceSettings,
    *,
    now: datetime,
    id_factory: Callable[[], str] = new_ulid,
) -> tuple[Source, ...]:
    """Persist the V1.1 connector registry without source-specific orchestration branches."""
    definitions: tuple[tuple[str, str, str | None, bool, str, int, JsonObject], ...] = (
        (
            "openreview",
            "OpenReview",
            "https://api2.openreview.net",
            settings.openreview_enabled,
            OpenReviewConnector.connector_version,
            1000,
            {"venues": list(settings.openreview_venues)},
        ),
        (
            "github",
            "GitHub",
            "https://api.github.com",
            settings.github_enrichment_enabled,
            GitHubConnector.connector_version,
            250,
            {"enrichment_only": True},
        ),
        (
            "huggingface",
            "Hugging Face Hub",
            "https://huggingface.co",
            settings.huggingface_enabled,
            HuggingFaceConnector.connector_version,
            250,
            {"kinds": ["models", "datasets", "spaces"]},
        ),
        (
            "official-rss",
            "Official AI research feeds",
            "https://huggingface.co/blog",
            settings.rss_enabled,
            RssAtomConnector.connector_version,
            500,
            {"feeds": list(settings.rss_feeds)},
        ),
    )
    registered: list[Source] = []
    for key, name, base_url, enabled, version, interval, config in definitions:
        existing = repository.get_by_key(key)
        health = SourceHealth.UNKNOWN if enabled else SourceHealth.DISABLED
        if existing is None:
            registered.append(
                repository.create(
                    Source(
                        id=id_factory(),
                        source_key=key,
                        display_name=name,
                        trust_tier=TrustTier.A,
                        base_url=base_url,
                        enabled=enabled,
                        poll_interval_minutes=60,
                        minimum_request_interval_ms=interval,
                        connector_version=version,
                        config=config,
                        health_status=health,
                        created_at=now.astimezone(UTC),
                        updated_at=now.astimezone(UTC),
                    )
                )
            )
            continue
        if enabled and existing.health_status is not SourceHealth.DISABLED:
            health = existing.health_status
        registered.append(
            repository.update(
                existing.model_copy(
                    update={
                        "display_name": name,
                        "trust_tier": TrustTier.A,
                        "base_url": base_url,
                        "enabled": enabled,
                        "minimum_request_interval_ms": interval,
                        "connector_version": version,
                        "config": config,
                        "health_status": health,
                        "updated_at": now.astimezone(UTC),
                    }
                )
            )
        )
    return tuple(registered)
