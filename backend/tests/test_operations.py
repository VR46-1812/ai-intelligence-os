"""Deterministic daily orchestration, scheduling, failure, and retention tests."""

from __future__ import annotations

import asyncio
import os
import shutil
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from app.config import (
    REPOSITORY_ROOT,
    AppSettings,
    PathSettings,
    SchedulerSettings,
    initialize_directories,
)
from app.db import MigrationRunner, SQLiteDatabase
from app.domain.models import PipelineStatus, PipelineTriggerType
from app.operations.cleanup import RetentionCleaner
from app.operations.models import DailyCounts, DailyRunResult, DailyRunStatus
from app.operations.scheduler import DailyScheduler
from app.operations.service import DailyRunBusyError, ProductionDailyRunner

NOW = datetime(2026, 7, 21, 2, 0, tzinfo=UTC)  # 07:30 Asia/Kolkata


@pytest.fixture
def operations_store() -> Iterator[tuple[AppSettings, SQLiteDatabase]]:
    relative = Path(f"data/.test-operations-{uuid4().hex}")
    root = REPOSITORY_ROOT / relative
    settings = AppSettings(
        paths=PathSettings(data_root=relative),
        scheduler=SchedulerSettings(enabled=False),
    )
    initialize_directories(settings.paths)
    database = SQLiteDatabase(settings.paths.database_path)
    MigrationRunner(database).migrate()
    try:
        yield settings, database
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_daily_runner_persists_counts_and_safe_failure(
    operations_store: tuple[AppSettings, SQLiteDatabase],
) -> None:
    settings, database = operations_store

    async def success(*_: object) -> DailyCounts:
        return DailyCounts(fetched=5, normalized=4, works_ranked=4, briefs_generated=1)

    runner = ProductionDailyRunner(
        settings,
        database,
        asyncio.Lock(),
        asyncio.Lock(),
        asyncio.Semaphore(1),
        clock=lambda: NOW,
        execute_steps=success,
    )
    result = asyncio.run(runner.run(PipelineTriggerType.MANUAL))
    status = runner.status()

    assert result.status is PipelineStatus.SUCCEEDED
    assert result.counts == DailyCounts(fetched=5, normalized=4, works_ranked=4, briefs_generated=1)
    assert status.latest_success_at == NOW and status.latest_run == result

    async def failure(*_: object) -> DailyCounts:
        raise RuntimeError("private path D:/secret and SQL SELECT")

    failed_runner = ProductionDailyRunner(
        settings,
        database,
        asyncio.Lock(),
        asyncio.Lock(),
        asyncio.Semaphore(1),
        clock=lambda: NOW + timedelta(minutes=1),
        execute_steps=failure,
    )
    failed = asyncio.run(failed_runner.run(PipelineTriggerType.RETRY))
    assert failed.status is PipelineStatus.FAILED
    assert failed.safe_detail == "The local daily pipeline stopped safely. Review System and retry."
    assert "secret" not in failed.model_dump_json()


def test_daily_runner_rejects_overlap(
    operations_store: tuple[AppSettings, SQLiteDatabase],
) -> None:
    settings, database = operations_store

    async def exercise() -> None:
        started = asyncio.Event()
        release = asyncio.Event()

        async def blocking(*_: object) -> DailyCounts:
            started.set()
            await release.wait()
            return DailyCounts()

        runner = ProductionDailyRunner(
            settings,
            database,
            asyncio.Lock(),
            asyncio.Lock(),
            asyncio.Semaphore(1),
            execute_steps=blocking,
        )
        task = asyncio.create_task(runner.run(PipelineTriggerType.SCHEDULE))
        await started.wait()
        with pytest.raises(DailyRunBusyError, match="already running"):
            await runner.run(PipelineTriggerType.MANUAL)
        release.set()
        assert (await task).status is PipelineStatus.SUCCEEDED

    asyncio.run(exercise())


class _SchedulerRunner:
    def __init__(self, status: DailyRunStatus) -> None:
        self.value = status
        self.triggers: list[PipelineTriggerType] = []
        self.recovered = 0

    async def run(self, trigger: PipelineTriggerType) -> DailyRunResult:
        self.triggers.append(trigger)
        return DailyRunResult(
            run_id="recovery",
            status=PipelineStatus.SUCCEEDED,
            trigger=trigger,
            counts=DailyCounts(),
            started_at=NOW,
            completed_at=NOW,
        )

    def status(self) -> DailyRunStatus:
        return self.value

    def recover_stale_runs(self) -> int:
        self.recovered += 1
        return 1


def test_scheduler_recovers_only_an_established_missed_run() -> None:
    previous = DailyRunResult(
        run_id="previous",
        status=PipelineStatus.FAILED,
        trigger=PipelineTriggerType.SCHEDULE,
        counts=DailyCounts(),
        started_at=NOW - timedelta(days=1),
        completed_at=NOW - timedelta(days=1),
        safe_detail="Safe failure.",
    )
    runner = _SchedulerRunner(
        DailyRunStatus(
            scheduler_enabled=True,
            schedule="06:00 Asia/Kolkata",
            running=False,
            latest_run=previous,
        )
    )
    scheduler = DailyScheduler(runner, AppSettings().scheduler, clock=lambda: NOW)

    assert asyncio.run(scheduler.recover_missed_run()) is True
    assert runner.triggers == [PipelineTriggerType.RETRY]
    assert scheduler.next_run_at() == datetime(2026, 7, 22, 0, 30, tzinfo=UTC)

    first_install = _SchedulerRunner(
        DailyRunStatus(
            scheduler_enabled=True,
            schedule="06:00 Asia/Kolkata",
            running=False,
        )
    )
    assert (
        asyncio.run(
            DailyScheduler(
                first_install, AppSettings().scheduler, clock=lambda: NOW
            ).recover_missed_run()
        )
        is False
    )


def test_retention_dry_run_and_delete_stay_inside_data_root(
    operations_store: tuple[AppSettings, SQLiteDatabase],
) -> None:
    settings, database = operations_store
    expired = settings.paths.temporary_root / "expired.tmp"
    expired.write_bytes(b"old")
    old = (NOW - timedelta(hours=48)).timestamp()
    os.utime(expired, (old, old))
    fresh = settings.paths.temporary_root / "fresh.tmp"
    fresh.write_bytes(b"new")
    connection = database.connect()
    try:
        cleaner = RetentionCleaner(
            connection, settings.paths, settings.retention, clock=lambda: NOW
        )
        preview = cleaner.run(dry_run=True)
        existed_after_preview = expired.exists()
        applied = cleaner.run(dry_run=False)
    finally:
        connection.close()

    assert preview.files_selected == 1 and preview.files_deleted == 0 and existed_after_preview
    assert applied.files_deleted == 1 and not expired.exists() and fresh.exists()
