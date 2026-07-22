"""Run or inspect the bounded local daily pipeline."""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Sequence

from app.config import initialize_directories, load_settings
from app.db import MigrationRunner, SQLiteDatabase
from app.domain.models import PipelineStatus, PipelineTriggerType
from app.operations.service import ProductionDailyRunner


def _runner() -> ProductionDailyRunner:
    settings = load_settings()
    initialize_directories(settings.paths)
    database = SQLiteDatabase(settings.paths.database_path, settings.database.busy_timeout_ms)
    MigrationRunner(database).migrate()
    return ProductionDailyRunner(
        settings,
        database,
        asyncio.Lock(),
        asyncio.Lock(),
        asyncio.Semaphore(settings.resources.llm_generation_concurrency),
    )


def main(arguments: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("run-now")
    subcommands.add_parser("status")
    cleanup = subcommands.add_parser("cleanup")
    cleanup.add_argument("--apply", action="store_true")
    options = parser.parse_args(arguments)
    runner = _runner()
    if options.command == "run-now":
        result = asyncio.run(runner.run(PipelineTriggerType.MANUAL))
        print(result.model_dump_json(indent=2))
        if result.status is not PipelineStatus.SUCCEEDED:
            raise SystemExit(1)
    elif options.command == "status":
        print(runner.status().model_dump_json(indent=2))
    else:
        print(runner.cleanup(dry_run=not options.apply).model_dump_json(indent=2))


if __name__ == "__main__":
    main()
