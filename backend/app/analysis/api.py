"""Public local-model, analysis, Today, and report endpoints."""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Annotated

import httpx
from fastapi import APIRouter, HTTPException, Path, Query, Request, status

from app.analysis.models import AnalysisResult, ModelStatus, RankedBrief, TodayReport
from app.analysis.ollama import OllamaClient
from app.analysis.service import AnalysisServiceError, ScoutAnalysisService
from app.config import AppSettings
from app.db import SQLiteDatabase
from app.domain.models import AnalysisType
from app.repositories import SQLiteRepositories

logger = logging.getLogger(__name__)
router = APIRouter(tags=["local-analysis"])
EntityId = Annotated[str, Path(min_length=1, max_length=255)]


@asynccontextmanager
async def _service(request: Request) -> AsyncGenerator[ScoutAnalysisService]:
    settings: AppSettings = request.app.state.settings
    database: SQLiteDatabase = request.app.state.database
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
                generation_semaphore=request.app.state.llm_generation_semaphore,
                resources=settings.resources,
            )
            yield ScoutAnalysisService(
                connection,
                SQLiteRepositories.for_connection(connection),
                generator,
                settings,
            )
        finally:
            connection.close()


def _safe_failure(error: AnalysisServiceError) -> HTTPException:
    return HTTPException(status_code=error.status_code, detail=error.safe_detail)


@router.get("/models/scout/status", response_model=ModelStatus)
async def scout_status(request: Request) -> ModelStatus:
    settings: AppSettings = request.app.state.settings
    timeout = httpx.Timeout(
        connect=settings.http.connect_timeout_seconds,
        read=min(10.0, settings.ollama.request_timeout_seconds),
        write=min(10.0, settings.ollama.request_timeout_seconds),
        pool=settings.http.connect_timeout_seconds,
    )
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
        generator = OllamaClient(
            client,
            base_url=str(settings.ollama.base_url),
            generation_semaphore=request.app.state.llm_generation_semaphore,
            resources=settings.resources,
        )
        return await generator.status(settings.models.scout.model)


@router.post("/items/{work_id}/brief", response_model=AnalysisResult)
async def generate_brief(work_id: EntityId, request: Request) -> AnalysisResult:
    try:
        async with _service(request) as service:
            return await service.analyze(work_id, AnalysisType.FAST_BRIEF)
    except AnalysisServiceError as error:
        raise _safe_failure(error) from error
    except Exception as error:
        logger.exception("brief_generation_failed", extra={"operation": "generate_brief"})
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="The local Scout analysis could not be completed.",
        ) from error


@router.post("/items/{work_id}/deep-dive", response_model=AnalysisResult)
async def generate_deep_dive(work_id: EntityId, request: Request) -> AnalysisResult:
    try:
        async with _service(request) as service:
            return await service.analyze(work_id, AnalysisType.DEEP_DIVE)
    except AnalysisServiceError as error:
        raise _safe_failure(error) from error
    except Exception as error:
        logger.exception("deep_dive_generation_failed", extra={"operation": "generate_deep_dive"})
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="The local Scout deep dive could not be completed.",
        ) from error


@router.get("/analyses/{analysis_id}", response_model=AnalysisResult)
async def analysis_detail(analysis_id: EntityId, request: Request) -> AnalysisResult:
    async with _service(request) as service:
        result = service.get_analysis(analysis_id)
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Analysis not found.")
    return result


@router.post("/analyses/{analysis_id}/retry", response_model=AnalysisResult)
async def retry_analysis(analysis_id: EntityId, request: Request) -> AnalysisResult:
    try:
        async with _service(request) as service:
            return await service.retry_analysis(analysis_id)
    except AnalysisServiceError as error:
        raise _safe_failure(error) from error
    except Exception as error:
        logger.exception("analysis_retry_failed", extra={"operation": "retry_analysis"})
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="The failed local report could not be retried.",
        ) from error


async def _today_report(request: Request, *, generate_limit: int = 0) -> TodayReport:
    settings: AppSettings = request.app.state.settings
    async with _service(request) as service:
        rows = service.ranked_today(10)
        for work_id, _, _ in rows[:generate_limit]:
            await service.analyze(work_id, AnalysisType.FAST_BRIEF)
        ranked = tuple(
            RankedBrief(
                work_id=work_id,
                title=title,
                technical_score=score,
                brief=service.latest_for_work(work_id, AnalysisType.FAST_BRIEF),
            )
            for work_id, title, score in rows
        )
        counts = service.daily_counts(datetime.now(UTC))
        model = await service.model_status()
    return TodayReport(
        report_date=datetime.now(UTC).date().isoformat(),
        model=model,
        ranked=ranked,
        generated_count=counts["fast_brief"],
        remaining_fast_briefs=max(
            0, settings.daily_work.maximum_fast_briefs - counts["fast_brief"]
        ),
        remaining_deep_dives=max(
            0,
            settings.daily_work.maximum_automatic_deep_dives - counts["deep_dive"],
        ),
    )


@router.get("/reports/today", response_model=TodayReport)
async def today_report(request: Request) -> TodayReport:
    return await _today_report(request)


@router.post("/reports/today/generate", response_model=TodayReport)
async def generate_today_report(
    request: Request,
    limit: Annotated[int, Query(ge=1, le=3)] = 1,
) -> TodayReport:
    try:
        return await _today_report(request, generate_limit=limit)
    except AnalysisServiceError as error:
        raise _safe_failure(error) from error
    except Exception as error:
        logger.exception("today_generation_failed", extra={"operation": "generate_today_report"})
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Today's local briefing could not be generated.",
        ) from error
