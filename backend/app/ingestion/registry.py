"""Typed loading of persisted source registrations and connector implementations."""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.models import Source
from app.domain.repositories import SourceRepository
from app.ingestion.contracts import (
    ConnectorErrorCode,
    ConnectorException,
    ConnectorFailure,
    SourceConnector,
)


@dataclass(frozen=True, slots=True)
class RegisteredSource:
    source: Source
    connector: SourceConnector


class SourceRegistry:
    """Resolve enabled persisted source settings to one matching connector."""

    def __init__(
        self, repository: SourceRepository, connectors: tuple[SourceConnector, ...]
    ) -> None:
        by_key: dict[str, SourceConnector] = {}
        for connector in connectors:
            if connector.key in by_key:
                raise ValueError(f"duplicate connector key: {connector.key}")
            by_key[connector.key] = connector
        self._repository = repository
        self._connectors = by_key

    def load(self, source_key: str) -> RegisteredSource:
        source = self._repository.get_by_key(source_key)
        if source is None:
            raise self._failure("source is not registered")
        if not source.enabled:
            raise self._failure("source is disabled")
        connector = self._connectors.get(source_key)
        if connector is None:
            raise self._failure("source connector is not installed")
        if connector.connector_version != source.connector_version:
            raise self._failure("source connector version does not match the registry")
        if connector.trust_tier is not source.trust_tier:
            raise self._failure("source connector trust tier does not match the registry")
        return RegisteredSource(source=source, connector=connector)

    @staticmethod
    def _failure(message: str) -> ConnectorException:
        return ConnectorException(
            ConnectorFailure(
                code=ConnectorErrorCode.SCHEMA_DRIFT,
                retryable=False,
                safe_message=message,
                attempts=1,
            )
        )
