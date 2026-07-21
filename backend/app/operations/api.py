"""Manual daily-run, retention, and System workspace endpoints."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Annotated

import httpx
from fastapi import APIRouter, HTTPException, Query, Request, status

from app.analysis.ollama import OllamaClient
from app.config import AppSettings
from app.db import SQLiteDatabase
from app.domain.models import PipelineTriggerType
from app.operations.cleanup import RetentionCleaner
from app.operations.models import (
    CleanupResult,
    DailyRunResult,
    DailyRunStatus,
    PublicFailure,
    ResourceBudgetStatus,
    SourceOperationalStatus,
    SystemStatus,
)
from app.operations.scheduler import DailyScheduler
from app.operations.service import DailyRunBusyError, ProductionDailyRunner

router = APIRouter(tags=["operations"])


def _runner(request: Request) -> ProductionDailyRunner:
    return request.app.state.daily_runner


def _status(request: Request) -> DailyRunStatus:
    runner = _runner(request)
    value = runner.status()
    scheduler: DailyScheduler = request.app.state.daily_scheduler
    return value.model_copy(
        update={"next_run_at": scheduler.next_run_at() if value.scheduler_enabled else None}
    )


@router.get("/operations/status", response_model=DailyRunStatus)
async def daily_status(request: Request) -> DailyRunStatus:
    return _status(request)


@router.post("/operations/run-now", response_model=DailyRunResult)
async def run_now(request: Request) -> DailyRunResult:
    try:
        return await _runner(request).run(PipelineTriggerType.MANUAL)
    except DailyRunBusyError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error


@router.post("/operations/cleanup", response_model=CleanupResult)
async def cleanup(
    request: Request,
    dry_run: Annotated[bool, Query()] = True,
) -> CleanupResult:
    if _runner(request).status().running:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cleanup waits until the active daily pipeline finishes.",
        )
    return _runner(request).cleanup(dry_run=dry_run)


@router.get("/system/status", response_model=SystemStatus)
async def system_status(request: Request) -> SystemStatus:
    settings: AppSettings = request.app.state.settings
    database: SQLiteDatabase = request.app.state.database
    connection = database.connect()
    try:
        source_row = connection.execute(
            """SELECT source_key,health_status,cursor_json,last_attempt_at,last_success_at
            FROM sources WHERE source_key='arxiv'"""
        ).fetchone()
        failure_rows = connection.execute(
            """SELECT 'pipeline' kind,id,COALESCE(completed_at,queued_at) occurred_at,
            error_summary FROM pipeline_runs WHERE run_type='daily' AND status='failed'
            UNION ALL
            SELECT 'analysis' kind,id,COALESCE(completed_at,created_at),error_code
            FROM analysis_runs WHERE status IN ('failed','rejected')
            ORDER BY occurred_at DESC LIMIT 10"""
        ).fetchall()
        storage = RetentionCleaner(connection, settings.paths, settings.retention).storage_bytes()
    finally:
        connection.close()
    timeout = httpx.Timeout(
        connect=settings.http.connect_timeout_seconds,
        read=min(10.0, settings.ollama.request_timeout_seconds),
        write=min(10.0, settings.ollama.request_timeout_seconds),
        pool=settings.http.connect_timeout_seconds,
    )
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
        model = await OllamaClient(
            client,
            base_url=str(settings.ollama.base_url),
            generation_semaphore=request.app.state.llm_generation_semaphore,
            resources=settings.resources,
        ).status(settings.models.scout.model)
    source = SourceOperationalStatus(
        source_key="arxiv",
        health="unknown" if source_row is None else str(source_row["health_status"]),
        checkpoint=(
            None
            if source_row is None or source_row["cursor_json"] is None
            else json.loads(str(source_row["cursor_json"]))
        ),
        last_attempt_at=None if source_row is None else source_row["last_attempt_at"],
        last_success_at=None if source_row is None else source_row["last_success_at"],
    )
    failures = tuple(
        PublicFailure(
            kind=str(row["kind"]),
            run_id=str(row["id"]),
            occurred_at=row["occurred_at"] or datetime.now(UTC),
            safe_detail=(
                str(row["error_summary"])
                if row["kind"] == "pipeline" and row["error_summary"]
                else "A local Scout report failed safely and can be retried from its report page."
            ),
            retryable=True,
        )
        for row in failure_rows
    )
    resources = settings.resources
    return SystemStatus(
        daily=_status(request),
        source=source,
        model=model,
        resources=ResourceBudgetStatus(
            non_llm_ram_mb=resources.non_llm_application_ram_mb,
            normal_total_ram_mb=resources.normal_project_ram_mb,
            temporary_peak_ram_mb=resources.absolute_project_peak_ram_mb,
            reserved_windows_ram_mb=resources.windows_reserved_ram_mb,
            vram_target_mb=resources.vram_target_mb,
            download_concurrency=resources.source_download_concurrency,
            generation_concurrency=resources.llm_generation_concurrency,
            maximum_storage_gib=settings.retention.maximum_storage_gib,
        ),
        storage_bytes=storage,
        failures=failures,
    )
