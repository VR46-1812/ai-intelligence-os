"""Persisted, idempotent and strictly sequential logical-agent execution."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime

from app.agents.models import AgentExecution, AgentInput, AgentOutput, AgentRunView, AgentStatus
from app.agents.registry import AGENT_SPECS
from app.catalog.identity import new_ulid
from app.db import transaction
from app.domain.models import JsonObject

AgentHandler = Callable[[AgentInput], Awaitable[AgentOutput]]


class AgentStageError(RuntimeError):
    """Safe failure raised when a logical agent cannot complete."""

    def __init__(self, agent_id: str, safe_reason: str) -> None:
        self.agent_id = agent_id
        self.safe_reason = safe_reason
        super().__init__(safe_reason)


class AgentRuntime:
    """Execute registered agents one by one and retain resumable checkpoints."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        id_factory: Callable[[], str] = new_ulid,
    ) -> None:
        self._connection = connection
        self._clock = clock
        self._id_factory = id_factory

    async def execute(
        self,
        run_id: str,
        report_date: str,
        handlers: Mapping[str, AgentHandler],
    ) -> tuple[AgentExecution, ...]:
        self._seed(run_id, report_date)
        checkpoint: JsonObject = {}
        completed: list[AgentExecution] = []
        for spec in AGENT_SPECS:
            handler = handlers[spec.agent_id]
            key = hashlib.sha256(
                f"{run_id}|{spec.agent_id}|{spec.version}|{report_date}".encode()
            ).hexdigest()
            existing = self._by_key(key)
            if existing is not None and existing.status is AgentStatus.SUCCEEDED:
                completed.append(existing)
                if existing.output:
                    values = existing.output.get("values", {})
                    checkpoint[spec.agent_id] = values if isinstance(values, dict) else {}
                continue
            execution_id = existing.id if existing else self._id_factory()
            attempt = 1 if existing is None else existing.attempt + 1
            if attempt > spec.retry.maximum_attempts:
                raise AgentStageError(
                    spec.agent_id, "This agent exhausted its bounded retry policy."
                )
            input_value = AgentInput(
                pipeline_run_id=run_id,
                report_date=report_date,
                checkpoint=checkpoint,
            )
            self._start(execution_id, run_id, spec, key, attempt, input_value)
            started = self._clock()
            try:
                output = await handler(input_value)
                if not output.summary.strip():
                    raise ValueError("agent output summary is empty")
            except Exception as error:
                safe_reason = (
                    error.safe_reason
                    if isinstance(error, AgentStageError)
                    else f"{spec.name} stopped safely; retry from this stage."
                )
                self._finish(execution_id, AgentStatus.FAILED, None, safe_reason, started)
                self._skip_after(run_id, spec.order)
                raise AgentStageError(spec.agent_id, safe_reason) from error
            self._finish(execution_id, AgentStatus.SUCCEEDED, output, None, started)
            row = self._by_key(key)
            if row is None:
                raise AgentStageError(spec.agent_id, "Agent checkpoint could not be persisted.")
            completed.append(row)
            checkpoint[spec.agent_id] = output.values
        return tuple(completed)

    def _seed(self, run_id: str, report_date: str) -> None:
        """Persist the complete graph before work starts so interruptions remain observable."""
        now = self._clock().isoformat()
        with transaction(self._connection):
            for spec in AGENT_SPECS:
                key = hashlib.sha256(
                    f"{run_id}|{spec.agent_id}|{spec.version}|{report_date}".encode()
                ).hexdigest()
                input_value = AgentInput(pipeline_run_id=run_id, report_date=report_date)
                self._connection.execute(
                    """INSERT OR IGNORE INTO agent_executions(
                    id,pipeline_run_id,agent_id,agent_version,stage_order,responsibility,status,
                    idempotency_key,attempt,input_json,created_at,updated_at)
                    VALUES(?,?,?,?,?,?,'queued',?,0,?,?,?)""",
                    (
                        self._id_factory(),
                        run_id,
                        spec.agent_id,
                        spec.version,
                        spec.order,
                        spec.responsibility,
                        key,
                        input_value.model_dump_json(),
                        now,
                        now,
                    ),
                )

    def _skip_after(self, run_id: str, stage_order: int) -> None:
        now = self._clock().isoformat()
        with transaction(self._connection):
            self._connection.execute(
                """UPDATE agent_executions SET status='skipped',
                safe_failure_reason='Waiting for the failed prerequisite agent to succeed.',
                completed_at=?,updated_at=?
                WHERE pipeline_run_id=? AND stage_order>? AND status='queued'""",
                (now, now, run_id, stage_order),
            )

    def list_for_run(self, run_id: str) -> tuple[AgentExecution, ...]:
        rows = self._connection.execute(
            "SELECT * FROM agent_executions WHERE pipeline_run_id=? ORDER BY stage_order", (run_id,)
        ).fetchall()
        return tuple(self._row(row) for row in rows)

    def latest_view(self) -> AgentRunView:
        latest = self._connection.execute(
            """SELECT id FROM pipeline_runs WHERE run_type='daily'
            ORDER BY queued_at DESC,id DESC LIMIT 1"""
        ).fetchone()
        run_id = None if latest is None else str(latest[0])
        executions = () if run_id is None else self.list_for_run(run_id)
        current = next(
            (item.agent_id for item in executions if item.status is AgentStatus.RUNNING), None
        )
        success = self._connection.execute(
            "SELECT MAX(completed_at) FROM agent_executions WHERE status='succeeded'"
        ).fetchone()
        degraded = tuple(
            str(row[0])
            for row in self._connection.execute(
                """SELECT source_key FROM sources
                WHERE enabled=1 AND health_status!='healthy' ORDER BY source_key"""
            )
        )
        return AgentRunView(
            pipeline_run_id=run_id,
            current_agent=current,
            latest_success_at=None if success is None else success[0],
            executions=executions,
            degraded_sources=degraded,
        )

    def _start(
        self,
        execution_id: str,
        run_id: str,
        spec: object,
        key: str,
        attempt: int,
        input_value: AgentInput,
    ) -> None:
        from app.agents.models import AgentSpec

        typed = AgentSpec.model_validate(spec)
        now = self._clock().isoformat()
        with transaction(self._connection):
            self._connection.execute(
                """INSERT INTO agent_executions(id,pipeline_run_id,agent_id,agent_version,
                stage_order,responsibility,status,idempotency_key,attempt,input_json,started_at,
                created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(idempotency_key) DO UPDATE SET
                status='running',attempt=excluded.attempt,
                input_json=excluded.input_json,safe_failure_reason=NULL,started_at=excluded.started_at,
                completed_at=NULL,updated_at=excluded.updated_at""",
                (
                    execution_id,
                    run_id,
                    typed.agent_id,
                    typed.version,
                    typed.order,
                    typed.responsibility,
                    AgentStatus.RUNNING.value,
                    key,
                    attempt,
                    input_value.model_dump_json(),
                    now,
                    now,
                    now,
                ),
            )

    def _finish(
        self,
        execution_id: str,
        status: AgentStatus,
        output: AgentOutput | None,
        safe_reason: str | None,
        started: datetime,
    ) -> None:
        completed = self._clock()
        duration_ms = max(0.0, (completed - started).total_seconds() * 1000)
        metrics = {} if output is None else dict(output.metrics)
        metrics["duration_ms"] = duration_ms
        with transaction(self._connection):
            self._connection.execute(
                """UPDATE agent_executions SET status=?,output_json=?,evidence_refs_json=?,
                provenance_refs_json=?,metrics_json=?,safe_failure_reason=?,completed_at=?,updated_at=?
                WHERE id=?""",
                (
                    status.value,
                    None if output is None else output.model_dump_json(),
                    json.dumps(()) if output is None else json.dumps(output.evidence_refs),
                    json.dumps(()) if output is None else json.dumps(output.provenance_refs),
                    json.dumps(metrics),
                    safe_reason,
                    completed.isoformat(),
                    completed.isoformat(),
                    execution_id,
                ),
            )

    def _by_key(self, key: str) -> AgentExecution | None:
        row = self._connection.execute(
            "SELECT * FROM agent_executions WHERE idempotency_key=?", (key,)
        ).fetchone()
        return None if row is None else self._row(row)

    @staticmethod
    def _row(row: sqlite3.Row) -> AgentExecution:
        output = None if row["output_json"] is None else json.loads(str(row["output_json"]))
        return AgentExecution(
            id=str(row["id"]),
            pipeline_run_id=str(row["pipeline_run_id"]),
            agent_id=str(row["agent_id"]),
            agent_version=str(row["agent_version"]),
            stage_order=int(row["stage_order"]),
            responsibility=str(row["responsibility"]),
            status=AgentStatus(str(row["status"])),
            idempotency_key=str(row["idempotency_key"]),
            attempt=int(row["attempt"]),
            input=json.loads(str(row["input_json"])),
            output=output,
            evidence_refs=tuple(json.loads(str(row["evidence_refs_json"]))),
            provenance_refs=tuple(json.loads(str(row["provenance_refs_json"]))),
            metrics=json.loads(str(row["metrics_json"])),
            safe_failure_reason=row["safe_failure_reason"],
            started_at=row["started_at"],
            completed_at=row["completed_at"],
        )
