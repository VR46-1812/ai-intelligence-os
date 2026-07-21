"""Startup-safe single-process daily scheduler."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.config import SchedulerSettings
from app.domain.models import PipelineTriggerType
from app.operations.service import DailyRunBusyError, DailyRunner

logger = logging.getLogger(__name__)


class DailyScheduler:
    """Trigger at most one daily run and recover a previously established missed schedule."""

    def __init__(
        self,
        runner: DailyRunner,
        settings: SchedulerSettings,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if settings.timezone == "Asia/Kolkata":
            self._timezone = timezone(timedelta(hours=5, minutes=30), "Asia/Kolkata")
        else:
            try:
                self._timezone = ZoneInfo(settings.timezone)
            except ZoneInfoNotFoundError as error:
                raise ValueError(
                    f"scheduler timezone is unavailable: {settings.timezone}"
                ) from error
        self._runner = runner
        self._settings = settings
        self._clock = clock
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if not self._settings.enabled or self._task is not None:
            return
        self._runner.recover_stale_runs()
        self._task = asyncio.create_task(self._serve(), name="aios-daily-scheduler")

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def recover_missed_run(self) -> bool:
        status = self._runner.status()
        if status.latest_run is None:
            return False
        now = self._clock().astimezone(self._timezone)
        scheduled = now.replace(
            hour=self._settings.hour, minute=self._settings.minute, second=0, microsecond=0
        )
        latest = status.latest_success_at
        if now < scheduled or (latest is not None and latest >= scheduled.astimezone(UTC)):
            return False
        if now - scheduled > timedelta(hours=self._settings.missed_run_grace_hours):
            return False
        try:
            await self._runner.run(PipelineTriggerType.RETRY)
        except DailyRunBusyError:
            return False
        return True

    def next_run_at(self) -> datetime:
        now = self._clock().astimezone(self._timezone)
        scheduled = now.replace(
            hour=self._settings.hour, minute=self._settings.minute, second=0, microsecond=0
        )
        if scheduled <= now:
            scheduled += timedelta(days=1)
        return scheduled.astimezone(UTC)

    async def _loop(self) -> None:
        while not self._stop.is_set():
            delay = max(0.0, (self.next_run_at() - self._clock().astimezone(UTC)).total_seconds())
            wait_seconds = min(delay, float(self._settings.poll_seconds))
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=wait_seconds)
                return
            except TimeoutError:
                pass
            if delay > wait_seconds:
                continue
            try:
                await self._runner.run(PipelineTriggerType.SCHEDULE)
            except DailyRunBusyError:
                logger.info("scheduled_daily_run_deferred", extra={"reason": "run_in_progress"})
            except Exception:
                logger.exception("scheduled_daily_run_failed")

    async def _serve(self) -> None:
        try:
            await self.recover_missed_run()
            await self._loop()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("daily_scheduler_failed")
