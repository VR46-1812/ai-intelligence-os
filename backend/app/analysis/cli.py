"""Run one bounded evidence-grounded Scout analysis locally."""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Sequence

import httpx

from app.analysis.ollama import OllamaClient
from app.analysis.service import ScoutAnalysisService
from app.config import initialize_directories, load_settings
from app.db import MigrationRunner, SQLiteDatabase
from app.domain.models import AnalysisType
from app.intelligence.service import IntelligenceOutputService
from app.repositories import SQLiteRepositories


async def _run(work_id: str | None, analysis_type: AnalysisType) -> str:
    settings = load_settings()
    initialize_directories(settings.paths)
    database = SQLiteDatabase(settings.paths.database_path, settings.database.busy_timeout_ms)
    MigrationRunner(database).migrate()
    timeout = httpx.Timeout(
        connect=settings.http.connect_timeout_seconds,
        read=settings.ollama.request_timeout_seconds,
        write=settings.ollama.request_timeout_seconds,
        pool=settings.http.connect_timeout_seconds,
    )
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
        connection = database.connect()
        try:
            generator = OllamaClient(
                client,
                base_url=str(settings.ollama.base_url),
                generation_semaphore=asyncio.Semaphore(1),
                resources=settings.resources,
            )
            service = ScoutAnalysisService(
                connection,
                SQLiteRepositories.for_connection(connection),
                generator,
                settings,
            )
            selected = work_id
            if selected is None:
                ranked = service.ranked_today(1)
                if not ranked:
                    raise RuntimeError("No ranked paper is available for local analysis.")
                selected = ranked[0][0]
            if analysis_type is AnalysisType.DEEP_DIVE:
                result, progress = await IntelligenceOutputService(
                    connection, service
                ).run_deep_dive(selected)
                return json.dumps(
                    {
                        "result": result.model_dump(mode="json"),
                        "progress": progress.model_dump(mode="json"),
                    },
                    indent=2,
                )
            result = await service.analyze(selected, analysis_type)
            return result.model_dump_json(indent=2)
        finally:
            connection.close()


def main(arguments: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("analysis_type", choices=("brief", "deep-dive"))
    parser.add_argument("--work-id")
    options = parser.parse_args(arguments)
    analysis_type = (
        AnalysisType.FAST_BRIEF if options.analysis_type == "brief" else AnalysisType.DEEP_DIVE
    )
    print(asyncio.run(_run(options.work_id, analysis_type)))


if __name__ == "__main__":
    main()
