"""Typed contracts for document processing and evidence reads."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class DocumentModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ProcessingStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    QUARANTINED = "quarantined"


class ProcessedPaper(DocumentModel):
    work_id: str
    document_id: str | None = None
    status: ProcessingStatus
    pages: int = Field(default=0, ge=0)
    evidence_spans: int = Field(default=0, ge=0)
    error_code: str | None = None
    safe_detail: str | None = None


class ProcessingSummary(DocumentModel):
    requested: int = Field(ge=0)
    succeeded: int = Field(ge=0)
    failed: int = Field(ge=0)
    quarantined: int = Field(ge=0)
    results: tuple[ProcessedPaper, ...]


class EvidenceItem(DocumentModel):
    id: str
    document_id: str
    source_url: str
    media_type: str
    document_sha256: str
    section_path: str | None
    page_start: int | None
    page_end: int | None
    span_text: str
    created_at: datetime


class EvidencePage(DocumentModel):
    items: tuple[EvidenceItem, ...]
    total: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0)
    has_more: bool
