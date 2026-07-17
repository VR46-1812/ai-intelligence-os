"""Typed repository boundaries for M1.2 persistence."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.domain.models import (
    AnalysisRun,
    AnalysisRunFilter,
    Document,
    DocumentFilter,
    PageRequest,
    PipelineRun,
    PipelineRunFilter,
    RankingProfile,
    RankingProfileFilter,
    RankingResult,
    RankingResultFilter,
    Source,
    SourceFilter,
    SourceRecord,
    SourceRecordFilter,
    Work,
    WorkFilter,
    WorkVersion,
    WorkVersionFilter,
)

_SOURCE_FILTER = SourceFilter()
_SOURCE_RECORD_FILTER = SourceRecordFilter()
_WORK_FILTER = WorkFilter()
_WORK_VERSION_FILTER = WorkVersionFilter()
_DOCUMENT_FILTER = DocumentFilter()
_RANKING_PROFILE_FILTER = RankingProfileFilter()
_RANKING_RESULT_FILTER = RankingResultFilter()
_ANALYSIS_FILTER = AnalysisRunFilter()
_PIPELINE_FILTER = PipelineRunFilter()


@dataclass(frozen=True, slots=True)
class CreateResult[T]:
    entity: T
    created: bool


class SourceRepository(Protocol):
    def create(self, source: Source) -> Source: ...
    def get(self, source_id: str) -> Source | None: ...
    def update(self, source: Source) -> Source: ...
    def list(
        self, page: PageRequest, filters: SourceFilter = _SOURCE_FILTER
    ) -> tuple[Source, ...]: ...


class SourceRecordRepository(Protocol):
    def create_or_get(self, record: SourceRecord) -> CreateResult[SourceRecord]: ...
    def get(self, record_id: str) -> SourceRecord | None: ...
    def update(self, record: SourceRecord) -> SourceRecord: ...
    def list(
        self, page: PageRequest, filters: SourceRecordFilter = _SOURCE_RECORD_FILTER
    ) -> tuple[SourceRecord, ...]: ...


class WorkRepository(Protocol):
    def create(self, work: Work) -> Work: ...
    def get(self, work_id: str) -> Work | None: ...
    def update(self, work: Work) -> Work: ...
    def list(self, page: PageRequest, filters: WorkFilter = _WORK_FILTER) -> tuple[Work, ...]: ...


class WorkVersionRepository(Protocol):
    def create_or_get(self, version: WorkVersion) -> CreateResult[WorkVersion]: ...
    def get(self, version_id: str) -> WorkVersion | None: ...
    def update(self, version: WorkVersion) -> WorkVersion: ...
    def list(
        self, page: PageRequest, filters: WorkVersionFilter = _WORK_VERSION_FILTER
    ) -> tuple[WorkVersion, ...]: ...


class DocumentRepository(Protocol):
    def create_or_get(self, document: Document) -> CreateResult[Document]: ...
    def get(self, document_id: str) -> Document | None: ...
    def update(self, document: Document) -> Document: ...
    def list(
        self, page: PageRequest, filters: DocumentFilter = _DOCUMENT_FILTER
    ) -> tuple[Document, ...]: ...


class RankingRepository(Protocol):
    def create_profile(self, profile: RankingProfile) -> RankingProfile: ...
    def get_profile(self, profile_id: str) -> RankingProfile | None: ...
    def update_profile(self, profile: RankingProfile) -> RankingProfile: ...
    def list_profiles(
        self, page: PageRequest, filters: RankingProfileFilter = _RANKING_PROFILE_FILTER
    ) -> tuple[RankingProfile, ...]: ...
    def create_result_or_get(self, result: RankingResult) -> CreateResult[RankingResult]: ...
    def get_result(self, result_id: str) -> RankingResult | None: ...
    def update_result(self, result: RankingResult) -> RankingResult: ...
    def list_results(
        self, page: PageRequest, filters: RankingResultFilter = _RANKING_RESULT_FILTER
    ) -> tuple[RankingResult, ...]: ...


class AnalysisRepository(Protocol):
    def create_or_get(self, run: AnalysisRun) -> CreateResult[AnalysisRun]: ...
    def get(self, run_id: str) -> AnalysisRun | None: ...
    def update(self, run: AnalysisRun) -> AnalysisRun: ...
    def list(
        self, page: PageRequest, filters: AnalysisRunFilter = _ANALYSIS_FILTER
    ) -> tuple[AnalysisRun, ...]: ...


class PipelineRunRepository(Protocol):
    def create(self, run: PipelineRun) -> PipelineRun: ...
    def get(self, run_id: str) -> PipelineRun | None: ...
    def update(self, run: PipelineRun) -> PipelineRun: ...
    def list(
        self, page: PageRequest, filters: PipelineRunFilter = _PIPELINE_FILTER
    ) -> tuple[PipelineRun, ...]: ...
