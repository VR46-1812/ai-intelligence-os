"""Public-safe paper evidence reader API."""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, status

from app.db import SQLiteDatabase
from app.documents.models import EvidenceItem, EvidencePage

logger = logging.getLogger(__name__)
router = APIRouter(tags=["evidence"])
PaperId = Annotated[str, Path(min_length=1, max_length=255)]


async def get_connection(request: Request) -> AsyncIterator[sqlite3.Connection]:
    database: SQLiteDatabase = request.app.state.database
    connection = database.connect()
    try:
        yield connection
    finally:
        connection.close()


ConnectionDependency = Annotated[sqlite3.Connection, Depends(get_connection)]


@router.get("/items/{paper_id}/evidence", response_model=EvidencePage)
async def paper_evidence(
    paper_id: PaperId,
    connection: ConnectionDependency,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
) -> EvidencePage:
    try:
        exists = connection.execute("SELECT 1 FROM works WHERE id=?", (paper_id,)).fetchone()
        if exists is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Paper not found.")
        total_row = connection.execute(
            """SELECT COUNT(*) FROM evidence_spans e JOIN documents d ON d.id=e.document_id
            JOIN work_versions v ON v.id=d.work_version_id WHERE v.work_id=?""",
            (paper_id,),
        ).fetchone()
        rows = connection.execute(
            """SELECT e.id,e.document_id,d.source_url,d.media_type,d.sha256,e.section_path,
            e.page_start,e.page_end,e.span_text,e.created_at
            FROM evidence_spans e JOIN documents d ON d.id=e.document_id
            JOIN work_versions v ON v.id=d.work_version_id
            WHERE v.work_id=? ORDER BY e.page_start,e.char_start,e.id LIMIT ? OFFSET ?""",
            (paper_id, limit, offset),
        ).fetchall()
    except HTTPException:
        raise
    except sqlite3.Error as error:
        logger.exception("evidence_query_failed", extra={"operation": "paper_evidence"})
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="The local evidence store is temporarily unavailable.",
        ) from error
    total = 0 if total_row is None else int(total_row[0])
    items = tuple(
        EvidenceItem.model_validate(
            {
                "id": row["id"],
                "document_id": row["document_id"],
                "source_url": row["source_url"],
                "media_type": row["media_type"],
                "document_sha256": row["sha256"],
                "section_path": row["section_path"],
                "page_start": row["page_start"],
                "page_end": row["page_end"],
                "span_text": row["span_text"],
                "created_at": row["created_at"],
            }
        )
        for row in rows
    )
    return EvidencePage(
        items=items, total=total, limit=limit, offset=offset, has_more=offset + len(items) < total
    )
