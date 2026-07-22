"""Public verified intelligence-output endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Request

from app.analysis.api import analysis_service
from app.analysis.service import AnalysisServiceError
from app.intelligence.evaluation import evaluate_golden_set, load_human_review_set
from app.intelligence.models import (
    DailyIntelligenceReport,
    DeepDiveProgress,
    EvaluationScores,
    HumanReviewSet,
    ModelRankingSignal,
    Opportunity,
    TopicOverview,
)
from app.intelligence.service import IntelligenceOutputService

router = APIRouter(tags=["intelligence"])
EntityId = Annotated[str, Path(min_length=1, max_length=255)]


@router.get("/ranking/model-signals", response_model=tuple[ModelRankingSignal, ...])
async def model_ranking_signals(request: Request) -> tuple[ModelRankingSignal, ...]:
    async with analysis_service(request) as scout:
        return IntelligenceOutputService(scout.connection, scout).ranking_signals()


@router.get("/deep-dives/{job_id}/progress", response_model=DeepDiveProgress)
async def deep_dive_progress(job_id: EntityId, request: Request) -> DeepDiveProgress:
    try:
        async with analysis_service(request) as scout:
            return IntelligenceOutputService(scout.connection, scout).progress(job_id)
    except AnalysisServiceError as error:
        raise HTTPException(error.status_code, error.safe_detail) from error


@router.get("/analyses/{analysis_id}/progress", response_model=DeepDiveProgress)
async def analysis_progress(analysis_id: EntityId, request: Request) -> DeepDiveProgress:
    try:
        async with analysis_service(request) as scout:
            return IntelligenceOutputService(scout.connection, scout).progress_for_analysis(
                analysis_id
            )
    except AnalysisServiceError as error:
        raise HTTPException(error.status_code, error.safe_detail) from error


@router.get("/reports/daily/complete", response_model=DailyIntelligenceReport)
async def complete_daily_report(request: Request) -> DailyIntelligenceReport:
    try:
        async with analysis_service(request) as scout:
            return IntelligenceOutputService(scout.connection, scout).latest_daily_report()
    except AnalysisServiceError as error:
        raise HTTPException(error.status_code, error.safe_detail) from error


@router.get("/topics/overview", response_model=tuple[TopicOverview, ...])
async def topic_overview(request: Request) -> tuple[TopicOverview, ...]:
    async with analysis_service(request) as scout:
        return IntelligenceOutputService(scout.connection, scout).topics()


@router.get("/opportunities", response_model=tuple[Opportunity, ...])
async def opportunities(request: Request) -> tuple[Opportunity, ...]:
    async with analysis_service(request) as scout:
        return IntelligenceOutputService(scout.connection, scout).opportunities()


@router.get("/evaluations/golden/v1", response_model=EvaluationScores)
async def golden_evaluation() -> EvaluationScores:
    return evaluate_golden_set()


@router.get("/evaluations/human-review/v1", response_model=HumanReviewSet)
async def human_review_cases() -> HumanReviewSet:
    return load_human_review_set()
