"""Typed M2.3 discovery CLI/API contracts."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.domain.models import JsonObject, Source, SourceHealth, TrustTier, UtcDateTime


class DiscoveryModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class DiscoverySyncRequest(DiscoveryModel):
    source_key: Literal["arxiv"] = "arxiv"
    maximum_records: int = Field(default=5, ge=1, le=25)
    lookback_hours: int = Field(default=168, ge=1, le=168)


class SourceSummary(DiscoveryModel):
    source_key: str
    display_name: str
    trust_tier: TrustTier
    enabled: bool
    connector_version: str
    health_status: SourceHealth
    last_attempt_at: UtcDateTime | None
    last_success_at: UtcDateTime | None

    @classmethod
    def from_source(cls, source: Source) -> SourceSummary:
        return cls(
            source_key=source.source_key,
            display_name=source.display_name,
            trust_tier=source.trust_tier,
            enabled=source.enabled,
            connector_version=source.connector_version,
            health_status=(source.health_status if source.enabled else SourceHealth.DISABLED),
            last_attempt_at=source.last_attempt_at,
            last_success_at=source.last_success_at,
        )


class ConnectorHealth(DiscoveryModel):
    source_key: str
    enabled: bool
    health_status: SourceHealth
    connector_version: str
    minimum_request_interval_ms: int = Field(ge=0)
    last_attempt_at: UtcDateTime | None
    last_success_at: UtcDateTime | None
    checkpoint: JsonObject | None

    @classmethod
    def from_source(cls, source: Source) -> ConnectorHealth:
        return cls(
            source_key=source.source_key,
            enabled=source.enabled,
            health_status=(source.health_status if source.enabled else SourceHealth.DISABLED),
            connector_version=source.connector_version,
            minimum_request_interval_ms=source.minimum_request_interval_ms,
            last_attempt_at=source.last_attempt_at,
            last_success_at=source.last_success_at,
            checkpoint=source.cursor,
        )
