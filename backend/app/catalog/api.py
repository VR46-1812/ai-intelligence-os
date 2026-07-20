"""Public catalog endpoints for the Explore research interface."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, status

from app.catalog.query import CatalogReadRepository, SQLiteCatalogReadRepository
from app.catalog.read_models import (
    CatalogFilterOptions,
    CatalogPaper,
    CatalogPaperPage,
    CatalogPaperQuery,
)
from app.db import SQLiteDatabase
from app.repositories.sqlite import RepositoryError

logger = logging.getLogger(__name__)
router = APIRouter(tags=["catalog"])


async def get_catalog_repository(request: Request) -> AsyncIterator[CatalogReadRepository]:
    database: SQLiteDatabase = request.app.state.database
    connection = database.connect()
    try:
        yield SQLiteCatalogReadRepository(connection)
    finally:
        connection.close()


CatalogRepositoryDependency = Annotated[CatalogReadRepository, Depends(get_catalog_repository)]
CatalogQueryDependency = Annotated[CatalogPaperQuery, Query()]
PaperId = Annotated[str, Path(min_length=1, max_length=255)]


def _catalog_unavailable(operation: str, error: RepositoryError) -> HTTPException:
    logger.exception("catalog_query_failed", extra={"operation": operation})
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="The local catalog is temporarily unavailable.",
    )


@router.get("/items", response_model=CatalogPaperPage)
async def list_papers(
    query: CatalogQueryDependency,
    repository: CatalogRepositoryDependency,
) -> CatalogPaperPage:
    try:
        return repository.list_papers(query)
    except RepositoryError as error:
        raise _catalog_unavailable("list_papers", error) from error


@router.get("/items/{paper_id}", response_model=CatalogPaper)
async def paper_detail(
    paper_id: PaperId,
    repository: CatalogRepositoryDependency,
) -> CatalogPaper:
    try:
        paper = repository.get_paper(paper_id)
    except RepositoryError as error:
        raise _catalog_unavailable("paper_detail", error) from error
    if paper is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Paper not found.")
    return paper


@router.get("/catalog/filters", response_model=CatalogFilterOptions)
async def catalog_filters(
    repository: CatalogRepositoryDependency,
) -> CatalogFilterOptions:
    try:
        return repository.filter_options()
    except RepositoryError as error:
        raise _catalog_unavailable("catalog_filters", error) from error
