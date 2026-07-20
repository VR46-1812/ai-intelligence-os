"""API trigger for bounded deterministic catalog ranking."""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request, status

from app.catalog.taxonomy import load_default_taxonomy
from app.db import SQLiteDatabase
from app.ranking.engine import DeterministicRankingEngine, RankingSummary

router = APIRouter(tags=["ranking"])
logger = logging.getLogger(__name__)


@router.post("/pipeline/rank", response_model=RankingSummary)
async def rank_catalog(
    request: Request,
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
) -> RankingSummary:
    database: SQLiteDatabase = request.app.state.database
    connection = database.connect()
    try:
        return DeterministicRankingEngine(connection, load_default_taxonomy()).rank_catalog(
            limit=limit
        )
    except Exception as error:
        logger.exception("deterministic_ranking_failed", extra={"operation": "rank_catalog"})
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Deterministic ranking could not be completed.",
        ) from error
    finally:
        connection.close()
