"""Process selected stored papers into local page-citable evidence and rankings."""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Sequence

import httpx

from app.catalog.taxonomy import load_default_taxonomy
from app.config import initialize_directories, load_settings
from app.db import MigrationRunner, SQLiteDatabase
from app.documents.download import SafePdfDownloader
from app.documents.ocr import TesseractAdapter
from app.documents.parser import PdfTextExtractor
from app.documents.service import DocumentProcessingService
from app.ranking.engine import DeterministicRankingEngine
from app.repositories import SQLiteRepositories


async def _process(limit: int) -> str:
    settings = load_settings()
    initialize_directories(settings.paths)
    database = SQLiteDatabase(settings.paths.database_path, settings.database.busy_timeout_ms)
    MigrationRunner(database).migrate()
    timeout = httpx.Timeout(
        connect=settings.http.connect_timeout_seconds,
        read=settings.http.read_timeout_seconds,
        write=settings.http.read_timeout_seconds,
        pool=settings.http.connect_timeout_seconds,
    )
    async with httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=False,
        headers={"User-Agent": settings.http.user_agent},
        limits=httpx.Limits(max_connections=settings.resources.source_download_concurrency),
    ) as client:
        connection = database.connect()
        try:
            downloader = SafePdfDownloader(
                client,
                destination=settings.paths.raw_documents_root / "pdf",
                temporary=settings.paths.temporary_root,
                quarantine=settings.paths.quarantine_root,
                maximum_bytes=settings.downloads.maximum_document_bytes,
                chunk_bytes=settings.downloads.chunk_bytes,
                concurrency=settings.resources.source_download_concurrency,
                maximum_retries=settings.http.maximum_retries,
            )
            summary = await DocumentProcessingService(
                connection,
                SQLiteRepositories.for_connection(connection),
                settings.paths,
                downloader,
                PdfTextExtractor(
                    suspicious_native_characters=settings.ocr.suspicious_native_characters,
                    ocr=(
                        TesseractAdapter(
                            executable=settings.ocr.tesseract_executable,
                            temporary_root=settings.paths.temporary_root,
                            language=settings.ocr.language,
                            timeout_seconds=settings.ocr.page_timeout_seconds,
                        )
                        if settings.ocr.enabled
                        else None
                    ),
                ),
            ).process(limit=limit)
            ranking = DeterministicRankingEngine(connection, load_default_taxonomy()).rank_catalog(
                limit=100
            )
            return (
                '{\n  "documents": '
                + summary.model_dump_json(indent=2)
                + ',\n  "ranking": '
                + ranking.model_dump_json(indent=2)
                + "\n}"
            )
        finally:
            connection.close()


def main(arguments: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, choices=range(1, 26), default=5)
    options = parser.parse_args(arguments)
    print(asyncio.run(_process(options.limit)))


if __name__ == "__main__":
    main()
