from __future__ import annotations

import asyncio
import shutil
from collections.abc import Awaitable, Callable
from pathlib import Path
from uuid import uuid4

from app.agents.models import AgentInput, AgentOutput, AgentStatus
from app.agents.registry import AGENT_SPECS
from app.agents.runtime import AgentRuntime, AgentStageError
from app.config import AppSettings, PathSettings, initialize_directories
from app.db import MigrationRunner, SQLiteDatabase


def _database() -> tuple[AppSettings, SQLiteDatabase]:
    root = Path(f"data/.test-agents/{uuid4().hex}")
    settings = AppSettings(paths=PathSettings(data_root=root, database_path=Path("state/test.db")))
    initialize_directories(settings.paths)
    database = SQLiteDatabase(settings.paths.database_path)
    MigrationRunner(database).migrate()
    connection = database.connect()
    try:
        connection.execute(
            """INSERT INTO pipeline_runs(id,run_type,trigger_type,status,config_snapshot_json,
            queued_at,started_at) VALUES('run-1','daily','manual','running','{}',
            '2026-07-22T00:00:00Z','2026-07-22T00:00:00Z')"""
        )
        connection.commit()
    finally:
        connection.close()
    return settings, database


def test_agent_registry_has_fourteen_unique_bounded_typed_stages() -> None:
    assert len(AGENT_SPECS) == 14
    assert [spec.order for spec in AGENT_SPECS] == list(range(1, 15))
    assert len({spec.agent_id for spec in AGENT_SPECS}) == 14
    assert all(spec.input_schema == "AgentInput@1.0" for spec in AGENT_SPECS)
    assert all(spec.output_schema == "AgentOutput@1.0" for spec in AGENT_SPECS)
    assert all(spec.budget.maximum_ram_mb <= 2048 for spec in AGENT_SPECS)
    assert all(spec.budget.maximum_vram_mb <= 6500 for spec in AGENT_SPECS)
    assert all(spec.prompt_version for spec in AGENT_SPECS if spec.model_assisted)


def test_agent_runtime_is_sequential_persisted_and_idempotent() -> None:
    settings, database = _database()
    connection = database.connect()
    calls: list[str] = []

    def handler(agent_id: str) -> Callable[[AgentInput], Awaitable[AgentOutput]]:
        async def execute(_: AgentInput) -> AgentOutput:
            calls.append(agent_id)
            return AgentOutput(
                summary=f"{agent_id} completed",
                evidence_refs=(f"evidence:{agent_id}",),
                provenance_refs=(f"source:{agent_id}",),
                metrics={"items": 1},
            )

        return execute

    handlers = {spec.agent_id: handler(spec.agent_id) for spec in AGENT_SPECS}
    try:
        runtime = AgentRuntime(connection)
        first = asyncio.run(runtime.execute("run-1", "2026-07-22", handlers))
        second = asyncio.run(runtime.execute("run-1", "2026-07-22", handlers))
        assert calls == [spec.agent_id for spec in AGENT_SPECS]
        assert len(first) == len(second) == 14
        assert all(item.status is AgentStatus.SUCCEEDED for item in first)
        assert [item.stage_order for item in runtime.list_for_run("run-1")] == list(range(1, 15))
    finally:
        connection.close()
        shutil.rmtree(settings.paths.data_root, ignore_errors=True)


def test_agent_runtime_retries_from_failed_checkpoint_once() -> None:
    settings, database = _database()
    connection = database.connect()
    curator_attempts = 0

    def handler(agent_id: str) -> Callable[[AgentInput], Awaitable[AgentOutput]]:
        async def execute(_: AgentInput) -> AgentOutput:
            nonlocal curator_attempts
            if agent_id == "curator":
                curator_attempts += 1
                if curator_attempts == 1:
                    raise AgentStageError(agent_id, "Curator fixture failed safely.")
            return AgentOutput(summary=f"{agent_id} completed")

        return execute

    handlers = {spec.agent_id: handler(spec.agent_id) for spec in AGENT_SPECS}
    try:
        runtime = AgentRuntime(connection)
        try:
            asyncio.run(runtime.execute("run-1", "2026-07-22", handlers))
        except AgentStageError as error:
            assert error.agent_id == "curator"
        else:
            raise AssertionError("the first curator attempt must fail")
        interrupted = runtime.list_for_run("run-1")
        assert len(interrupted) == 14
        assert interrupted[2].status is AgentStatus.FAILED
        assert all(item.status is AgentStatus.SKIPPED for item in interrupted[3:])
        completed = asyncio.run(runtime.execute("run-1", "2026-07-22", handlers))
        assert curator_attempts == 2
        assert len(completed) == 14
        assert runtime.list_for_run("run-1")[2].attempt == 2
    finally:
        connection.close()
        shutil.rmtree(settings.paths.data_root, ignore_errors=True)
