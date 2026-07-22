"""Public-safe agent graph, execution and retry endpoints."""

from fastapi import APIRouter, HTTPException, Request, status

from app.agents.models import AgentRunView, AgentSpec
from app.agents.registry import AGENT_SPECS
from app.agents.runtime import AgentRuntime
from app.domain.models import PipelineTriggerType
from app.operations.models import DailyRunResult
from app.operations.service import DailyRunBusyError

router = APIRouter(prefix="/agents", tags=["agents"])


@router.get("/graph", response_model=tuple[AgentSpec, ...])
async def graph() -> tuple[AgentSpec, ...]:
    return AGENT_SPECS


@router.get("/status", response_model=AgentRunView)
async def agent_status(request: Request) -> AgentRunView:
    connection = request.app.state.database.connect()
    try:
        return AgentRuntime(connection).latest_view()
    finally:
        connection.close()


@router.post("/retry", response_model=DailyRunResult)
async def retry_failed_agent(request: Request) -> DailyRunResult:
    try:
        return await request.app.state.daily_runner.run(PipelineTriggerType.RETRY)
    except DailyRunBusyError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The active sequential agent run must finish before retry.",
        ) from error
