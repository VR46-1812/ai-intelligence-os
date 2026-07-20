"""Local command-line control for M2.3 source discovery."""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Generator, Sequence
from contextlib import AbstractContextManager, contextmanager
from datetime import UTC, datetime
from typing import Protocol

from app.catalog.taxonomy import TopicTaxonomyService, load_default_taxonomy
from app.config import initialize_directories, load_settings
from app.db import MigrationRunner, SQLiteDatabase, transaction
from app.discovery.arxiv import ArxivDiscoverySyncExecutor
from app.discovery.models import DiscoverySyncRequest
from app.discovery.service import (
    DiscoveryDisabledError,
    DiscoveryNotFoundError,
    DiscoveryService,
)
from app.repositories import SQLiteRepositories
from app.sources.catalog import upsert_arxiv_source


class ServiceContextFactory(Protocol):
    def __call__(self) -> AbstractContextManager[DiscoveryService]: ...


@contextmanager
def discovery_service_context() -> Generator[DiscoveryService]:
    settings = load_settings()
    initialize_directories(settings.paths)
    database = SQLiteDatabase(settings.paths.database_path, settings.database.busy_timeout_ms)
    MigrationRunner(database).migrate()
    connection = database.connect()
    repositories = SQLiteRepositories.for_connection(connection)
    try:
        with transaction(connection):
            upsert_arxiv_source(
                repositories.sources,
                settings.sources,
                now=datetime.now(UTC),
            )
            TopicTaxonomyService(repositories.topics, load_default_taxonomy()).seed()
        yield DiscoveryService(
            repositories.sources,
            repositories.pipeline_runs,
            ArxivDiscoverySyncExecutor(settings, connection, repositories),
        )
    finally:
        connection.close()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    list_parser = commands.add_parser("list-sources", help="list registered source connectors")
    list_parser.add_argument("--limit", type=int, choices=range(1, 101), default=50)
    list_parser.add_argument("--offset", type=int, default=0)
    list_parser.add_argument("--enabled", action=argparse.BooleanOptionalAction, default=None)
    sync_parser = commands.add_parser("sync", help="start one bounded source sync")
    sync_parser.add_argument("source_key", choices=("arxiv",))
    sync_parser.add_argument("--maximum-records", type=int, choices=range(1, 26), default=5)
    sync_parser.add_argument("--lookback-hours", type=int, choices=range(1, 169), default=168)
    run_parser = commands.add_parser("show-run", help="inspect one persisted pipeline run")
    run_parser.add_argument("run_id")
    health_parser = commands.add_parser("source-health", help="view connector health")
    health_parser.add_argument("source_key")
    return parser


async def _execute(options: argparse.Namespace, service: DiscoveryService) -> str:
    if options.command == "list-sources":
        result = service.list_sources(
            limit=options.limit,
            offset=options.offset,
            enabled=options.enabled,
        )
        return "[" + ",".join(item.model_dump_json() for item in result) + "]"
    if options.command == "sync":
        result = await service.start_sync(
            DiscoverySyncRequest(
                source_key=options.source_key,
                maximum_records=options.maximum_records,
                lookback_hours=options.lookback_hours,
            )
        )
        return result.model_dump_json(indent=2)
    if options.command == "show-run":
        return service.inspect_run(options.run_id).model_dump_json(indent=2)
    if options.command == "source-health":
        return service.connector_health(options.source_key).model_dump_json(indent=2)
    raise RuntimeError(f"unsupported discovery command: {options.command}")


def main(
    arguments: Sequence[str] | None = None,
    *,
    context_factory: ServiceContextFactory = discovery_service_context,
) -> None:
    parser = _parser()
    options = parser.parse_args(arguments)
    try:
        with context_factory() as service:
            print(asyncio.run(_execute(options, service)))
    except (DiscoveryDisabledError, DiscoveryNotFoundError) as error:
        parser.exit(2, f"discovery command failed: {error}\n")


if __name__ == "__main__":
    main()
