"""Evidence selection, structured Scout analysis, verification, and persistence."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.resources import files
from typing import TypeVar, cast

from pydantic import ValidationError

from app.analysis.models import (
    AnalysisResult,
    ClaimType,
    DeepClaim,
    DeepDiveReport,
    FastBrief,
    ModelStatus,
)
from app.analysis.ollama import OllamaError, ScoutGenerator
from app.catalog.identity import new_ulid
from app.config import AppSettings, GenerationModelProfile
from app.db import transaction
from app.domain.models import (
    AnalysisRun,
    AnalysisRunFilter,
    AnalysisStatus,
    AnalysisType,
    JsonObject,
    PageRequest,
)
from app.repositories import SQLiteRepositories

ReportT = TypeVar("ReportT", FastBrief, DeepDiveReport)


class AnalysisServiceError(RuntimeError):
    def __init__(self, code: str, safe_detail: str, *, status_code: int = 422) -> None:
        super().__init__(safe_detail)
        self.code = code
        self.safe_detail = safe_detail
        self.status_code = status_code


class CitationVerificationError(ValueError):
    """Raised when model output cites absent evidence or asserts unsupported facts."""


class OutputQualityError(ValueError):
    """Raised when structured output is valid but repetitive or vacuous."""


@dataclass(frozen=True, slots=True)
class EvidenceInput:
    id: str
    page: int
    section: str | None
    text: str
    sha256: str


@dataclass(frozen=True, slots=True)
class WorkInput:
    id: str
    version_id: str
    title: str
    publication_status: str
    evidence: tuple[EvidenceInput, ...]


class ScoutAnalysisService:
    """Generate only citation-verified reports from bounded persisted evidence."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        repositories: SQLiteRepositories,
        generator: ScoutGenerator,
        settings: AppSettings,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._connection = connection
        self._repositories = repositories
        self._generator = generator
        self._settings = settings
        self._clock = clock

    @property
    def connection(self) -> sqlite3.Connection:
        """Expose the request-scoped connection to composed local services."""
        return self._connection

    async def analyze(self, work_id: str, analysis_type: AnalysisType) -> AnalysisResult:
        if analysis_type not in {AnalysisType.FAST_BRIEF, AnalysisType.DEEP_DIVE}:
            raise AnalysisServiceError("UNSUPPORTED_ANALYSIS", "This analysis type is unavailable.")
        work = self._load_work(work_id)
        prompt_key = analysis_type.value
        template = self._load_prompt(prompt_key)
        profile = self._profile(analysis_type)
        prompt_hash = hashlib.sha256(template.encode()).hexdigest()
        evidence_fingerprint = "|".join(item.sha256 for item in work.evidence)
        fingerprint = hashlib.sha256(
            (
                f"{analysis_type.value}|{work.version_id}|{evidence_fingerprint}|{prompt_hash}|"
                f"{profile.model}|{profile.maximum_context_tokens}|"
                f"{profile.maximum_output_tokens}"
            ).encode()
        ).hexdigest()
        now = self._clock()
        model_profile_id, prompt_version_id = self._register_runtime(
            "ollama-scout-v1",
            f"prompt-{prompt_key}-v1",
            prompt_key,
            prompt_hash,
            now,
            profile,
        )
        existing = self._find_existing(
            analysis_type, fingerprint, model_profile_id, prompt_version_id
        )
        if existing is not None:
            if existing.status is AnalysisStatus.SUCCEEDED:
                return self._public_result(existing, cached=True)
            if existing.status in {AnalysisStatus.QUEUED, AnalysisStatus.RUNNING}:
                raise AnalysisServiceError(
                    "ANALYSIS_BUSY",
                    "This paper already has a local analysis in progress.",
                    status_code=409,
                )
            return self._public_result(existing, cached=True)
        self._enforce_daily_budget(analysis_type, now)
        queued = AnalysisRun(
            id=new_ulid(),
            work_id=work.id,
            work_version_id=work.version_id,
            analysis_type=analysis_type,
            status=AnalysisStatus.QUEUED,
            model_profile_id=model_profile_id,
            prompt_version_id=prompt_version_id,
            input_fingerprint=fingerprint,
            created_at=now,
        )
        with transaction(self._connection):
            self._repositories.analyses.create_or_get(queued)
            running = queued.model_copy(
                update={"status": AnalysisStatus.RUNNING, "started_at": now}
            )
            self._repositories.analyses.update(running)
        started = time.monotonic()
        try:
            report = await self._generate_validated(work, analysis_type, template, profile)
            coverage, verified = self._verify_report(report, work)
            duration_ms = max(0, int((time.monotonic() - started) * 1000))
            output = cast(JsonObject, report.model_dump(mode="json"))
            output["_verification"] = {
                "citation_coverage": coverage,
                "citations_verified": verified,
            }
            completed = running.model_copy(
                update={
                    "status": AnalysisStatus.SUCCEEDED,
                    "completed_at": self._clock(),
                    "duration_ms": duration_ms,
                    "output": output,
                }
            )
            with transaction(self._connection):
                self._repositories.analyses.update(completed)
                self._persist_claims(completed, report, work)
            return self._public_result(completed, cached=False)
        except (OllamaError, AnalysisServiceError) as error:
            code = error.code
            detail = error.safe_detail
        except OutputQualityError:
            code = "OUTPUT_QUALITY_INVALID"
            detail = "Scout repeated or omitted required analysis fields. Retry the report once."
        except CitationVerificationError:
            code = "CITATION_VERIFICATION_FAILED"
            detail = (
                "Scout citations did not pass stored-evidence verification. Retry the report once."
            )
        except (ValidationError, json.JSONDecodeError):
            code = "STRUCTURED_OUTPUT_INVALID"
            detail = "The Scout could not produce a citation-safe structured report."
        failed = running.model_copy(
            update={
                "status": AnalysisStatus.FAILED,
                "completed_at": self._clock(),
                "duration_ms": max(0, int((time.monotonic() - started) * 1000)),
                "error_code": code,
                "error_detail": detail,
            }
        )
        with transaction(self._connection):
            self._repositories.analyses.update(failed)
        return self._public_result(failed, cached=False)

    def get_analysis(self, analysis_id: str) -> AnalysisResult | None:
        run = self._repositories.analyses.get(analysis_id)
        return None if run is None else self._public_result(run, cached=True)

    async def retry_analysis(self, analysis_id: str) -> AnalysisResult:
        run = self._repositories.analyses.get(analysis_id)
        if run is None:
            raise AnalysisServiceError("ANALYSIS_NOT_FOUND", "Analysis not found.", status_code=404)
        public = self._public_result(run, cached=True)
        if public.status not in {AnalysisStatus.FAILED, AnalysisStatus.REJECTED}:
            raise AnalysisServiceError(
                "ANALYSIS_NOT_RETRYABLE",
                "Only a failed report can be retried.",
                status_code=409,
            )
        with transaction(self._connection):
            self._repositories.analyses.update(
                run.model_copy(
                    update={"input_fingerprint": f"{run.input_fingerprint}:failed:{run.id}"}
                )
            )
        return await self.analyze(run.work_id, run.analysis_type)

    def latest_for_work(self, work_id: str, analysis_type: AnalysisType) -> AnalysisResult | None:
        rows = self._repositories.analyses.list(
            PageRequest(limit=100),
            AnalysisRunFilter(work_id=work_id, analysis_type=analysis_type),
        )
        if not rows:
            return None
        results = tuple(self._public_result(row, cached=True) for row in rows)
        return next(
            (
                result
                for result in results
                if result.status is AnalysisStatus.SUCCEEDED and result.output is not None
            ),
            results[0],
        )

    def ranked_today(self, limit: int = 10) -> tuple[tuple[str, str, float], ...]:
        rows = self._connection.execute(
            """SELECT w.id,v.title,rr.total_score FROM ranking_results rr
            JOIN ranking_profiles rp ON rp.id=rr.profile_id AND rp.active=1
            JOIN works w ON w.id=rr.work_id JOIN work_versions v ON v.id=w.current_version_id
            WHERE rr.score_kind='technical' ORDER BY rr.total_score DESC,w.id LIMIT ?""",
            (limit,),
        ).fetchall()
        return tuple((str(row["id"]), str(row["title"]), float(row["total_score"])) for row in rows)

    def daily_counts(self, now: datetime) -> dict[str, int]:
        day_start = f"{now.astimezone(UTC).date().isoformat()}T00:00:00+00:00"
        return {
            kind: int(
                self._connection.execute(
                    """SELECT COUNT(*) FROM analysis_runs WHERE analysis_type=?
                    AND status='succeeded' AND created_at>=?""",
                    (kind, day_start),
                ).fetchone()[0]
            )
            for kind in ("fast_brief", "deep_dive")
        }

    async def model_status(self) -> ModelStatus:
        return await self._generator.status(self._settings.models.scout.model)

    async def _generate_validated(
        self,
        work: WorkInput,
        analysis_type: AnalysisType,
        template: str,
        profile: GenerationModelProfile,
    ) -> FastBrief | DeepDiveReport:
        model_type: type[FastBrief] | type[DeepDiveReport] = (
            FastBrief if analysis_type is AnalysisType.FAST_BRIEF else DeepDiveReport
        )
        prompt = self._render_prompt(template, work)
        first = await self._generator.generate(
            prompt=prompt,
            schema=cast(dict[str, object], model_type.model_json_schema()),
            profile=profile,
        )
        try:
            report = self._validate_response(model_type, first.response_text)
            self._validate_output_quality(report, work)
            self._verify_report(report, work)
            return report
        except (
            ValidationError,
            CitationVerificationError,
            OutputQualityError,
            json.JSONDecodeError,
        ) as validation_error:
            if isinstance(validation_error, ValidationError):
                error_summary = json.dumps(
                    validation_error.errors(
                        include_url=False, include_context=False, include_input=False
                    ),
                    separators=(",", ":"),
                )[:2000]
            else:
                error_summary = str(validation_error)[:2000]
            repair_prompt = (
                prompt
                + "\nThe prior JSON failed schema or citation verification. Correct it once. "
                + "Remove every unsupported claim and use only supplied evidence IDs."
                + " Every required field must be specific and non-repetitive. "
                + "Narrative fields must be different from the title."
                + " Every claim must contain the exact required claim keys."
                + "\nVALIDATION_ERRORS:\n"
                + error_summary
                + "\nPRIOR_JSON:\n"
                + first.response_text[:12_000]
            )
            repaired = await self._generator.generate(
                prompt=repair_prompt,
                schema=cast(dict[str, object], model_type.model_json_schema()),
                profile=profile,
            )
            report = self._validate_response(model_type, repaired.response_text)
            self._validate_output_quality(report, work)
            self._verify_report(report, work)
            return report

    @staticmethod
    def _validate_response(
        model_type: type[FastBrief] | type[DeepDiveReport], response_text: str
    ) -> FastBrief | DeepDiveReport:
        raw_payload: object = json.loads(response_text)
        if not isinstance(raw_payload, dict):
            raise ValueError("structured output must be an object")
        payload = cast(JsonObject, raw_payload)

        def normalize(container: JsonObject | None, key: str, allowed: tuple[str, ...]) -> None:
            if container is None or not isinstance(container.get(key), str):
                return
            value = str(container[key]).strip().casefold()
            for option in allowed:
                if value == option or any(
                    value.startswith(option + suffix) for suffix in (" ", ":", " -")
                ):
                    container[key] = option
                    return

        if model_type is FastBrief:
            normalize(payload, "evidence_state", ("strong", "moderate", "weak", "unknown"))
            normalize(
                payload,
                "code_state",
                ("official", "author_linked", "community", "none_found", "unknown"),
            )
            normalize(
                payload,
                "recommended_action",
                ("deep_dive", "track", "read_source", "ignore", "manual_review"),
            )
            return FastBrief.model_validate(payload)
        normalize(
            payload,
            "publication_status",
            ("unknown", "preprint", "submitted", "accepted", "published", "withdrawn"),
        )
        raw_reproducibility = payload.get("reproducibility")
        reproducibility = (
            cast(JsonObject, raw_reproducibility) if isinstance(raw_reproducibility, dict) else None
        )
        normalize(
            reproducibility,
            "status",
            ("unknown", "insufficient", "partial", "promising", "reproduced"),
        )
        normalize(
            reproducibility,
            "hardware_fit",
            ("fits", "fits_with_reduction", "does_not_fit", "unknown"),
        )
        raw_findings = payload.get("skeptic_findings")
        if isinstance(raw_findings, list):
            for raw_finding in raw_findings:
                if isinstance(raw_finding, dict):
                    finding = cast(JsonObject, raw_finding)
                    normalize(finding, "severity", ("info", "warning", "critical"))
                    normalize(
                        finding,
                        "resolution",
                        ("accepted", "qualified", "rejected", "unresolved"),
                    )
        raw_claims = payload.get("claims")
        if isinstance(raw_claims, list):
            for raw_claim in raw_claims:
                if isinstance(raw_claim, dict):
                    claim = cast(JsonObject, raw_claim)
                    normalize(
                        claim,
                        "type",
                        ("fact", "interpretation", "recommendation", "hypothesis"),
                    )
                    normalize(claim, "importance", ("minor", "major", "critical"))
                    normalize(
                        claim,
                        "verification_status",
                        ("unsupported", "supported", "conflicted", "rejected"),
                    )
        return DeepDiveReport.model_validate(payload)

    @staticmethod
    def _validate_output_quality(report: FastBrief | DeepDiveReport, work: WorkInput) -> None:
        def normalized(value: str) -> str:
            return " ".join(value.casefold().split()).strip(" .,:;-")

        if isinstance(report, FastBrief):
            narrative = (
                report.change,
                report.problem,
                report.contribution,
                report.technical_relevance,
                report.commercial_relevance,
            )
            values = tuple(normalized(value) for value in narrative)
            if any(not value for value in values) or len(set(values)) != len(values):
                raise OutputQualityError("brief fields are empty or repetitive")
            if normalized(work.title) in set(values):
                raise OutputQualityError("brief repeats the title instead of analysis")
            if len({normalized(item) for item in report.limitations}) != len(report.limitations):
                raise OutputQualityError("brief limitations are repetitive")
            claim_texts = tuple(normalized(claim.text) for claim in report.claims)
            if len(set(claim_texts)) != len(claim_texts):
                raise OutputQualityError("brief claims are repetitive")
            return
        sections = (
            report.executive_significance.markdown,
            report.problem_context.markdown,
            report.method.markdown,
            report.evaluation.markdown,
            report.limitations.markdown,
        )
        if len({normalized(value) for value in sections}) != len(sections):
            raise OutputQualityError("deep-dive sections are repetitive")

    @staticmethod
    def _render_prompt(template: str, work: WorkInput) -> str:
        payload = {
            "work_id": work.id,
            "title": work.title,
            "publication_status": work.publication_status,
            "evidence": [
                {
                    "evidence_id": item.id,
                    "page": item.page,
                    "section": item.section,
                    "text": item.text,
                }
                for item in work.evidence
            ],
        }
        return (
            template
            + "\nINPUT_DATA:\n"
            + json.dumps(payload, ensure_ascii=True, separators=(",", ":"))
        )

    def _load_work(self, work_id: str) -> WorkInput:
        row = self._connection.execute(
            """SELECT w.id,v.id version_id,v.title,w.publication_status
            FROM works w JOIN work_versions v ON v.id=w.current_version_id
            WHERE w.id=? AND w.work_type='paper'""",
            (work_id,),
        ).fetchone()
        if row is None:
            raise AnalysisServiceError("PAPER_NOT_FOUND", "Paper not found.", status_code=404)
        evidence_rows = self._connection.execute(
            """SELECT e.id,e.page_start,e.section_path,e.span_text,e.normalized_text_sha256
            FROM evidence_spans e JOIN documents d ON d.id=e.document_id
            WHERE d.work_version_id=? AND d.parse_status IN ('parsed','partial')
            ORDER BY e.page_start,e.char_start,e.id LIMIT 600""",
            (row["version_id"],),
        ).fetchall()
        selected: list[EvidenceInput] = []
        characters = 0
        if evidence_rows:
            step = max(1, len(evidence_rows) // 28)
            for evidence_row in evidence_rows[::step]:
                text = str(evidence_row["span_text"])[:1800]
                if characters + len(text) > 20_000:
                    break
                selected.append(
                    EvidenceInput(
                        id=str(evidence_row["id"]),
                        page=int(evidence_row["page_start"]),
                        section=(
                            None
                            if evidence_row["section_path"] is None
                            else str(evidence_row["section_path"])
                        ),
                        text=text,
                        sha256=str(evidence_row["normalized_text_sha256"]),
                    )
                )
                characters += len(text)
                if len(selected) == 28:
                    break
        if not selected:
            raise AnalysisServiceError(
                "EVIDENCE_REQUIRED", "This paper has no parsed evidence to analyze."
            )
        return WorkInput(
            id=str(row["id"]),
            version_id=str(row["version_id"]),
            title=str(row["title"]),
            publication_status=str(row["publication_status"]),
            evidence=tuple(selected),
        )

    @staticmethod
    def _verify_report(report: FastBrief | DeepDiveReport, work: WorkInput) -> tuple[float, int]:
        if report.work_id != work.id:
            raise CitationVerificationError("report work identity does not match")
        allowed = {item.id for item in work.evidence}
        if isinstance(report, FastBrief):
            claims = report.claims
            required = [
                claim
                for claim in claims
                if claim.type in {ClaimType.FACT, ClaimType.INTERPRETATION}
            ]
        else:
            if (
                report.title != work.title
                or report.publication_status.value != work.publication_status
            ):
                raise CitationVerificationError("report metadata does not match")
            claim_ids = {claim.id for claim in report.claims}
            referenced = {
                claim_id
                for section in (
                    report.executive_significance,
                    report.problem_context,
                    report.method,
                    report.evaluation,
                    report.limitations,
                )
                for claim_id in section.claim_ids
            }
            if not referenced <= claim_ids:
                raise CitationVerificationError("section references an unknown claim")
            claims = report.claims
            required = [
                claim
                for claim in claims
                if claim.type in {ClaimType.FACT, ClaimType.INTERPRETATION}
                and claim.importance in {"major", "critical"}
            ]
        verified = 0
        for claim in claims:
            if not set(claim.evidence_ids) <= allowed:
                raise CitationVerificationError("claim references unavailable evidence")
            if claim.type in {ClaimType.FACT, ClaimType.INTERPRETATION} and not claim.evidence_ids:
                raise CitationVerificationError("factual claim is unsupported")
            if (
                isinstance(claim, DeepClaim)
                and claim.type in {ClaimType.FACT, ClaimType.INTERPRETATION}
                and claim.verification_status == "unsupported"
            ):
                raise CitationVerificationError("unsupported factual claim was not refused")
            verified += len(set(claim.evidence_ids))
        supported_required = sum(bool(claim.evidence_ids) for claim in required)
        coverage = 1.0 if not required else supported_required / len(required)
        if coverage < 0.9:
            raise CitationVerificationError("major claim citation coverage is below 90 percent")
        return coverage, verified

    def _register_runtime(
        self,
        model_id: str,
        prompt_id: str,
        prompt_key: str,
        prompt_hash: str,
        now: datetime,
        profile: GenerationModelProfile,
    ) -> tuple[str, str]:
        generation_config = json.dumps(
            {
                "temperature": profile.temperature,
                "maximum_output_tokens": profile.maximum_output_tokens,
                "keep_alive_seconds": 0,
            },
            sort_keys=True,
        )
        with transaction(self._connection):
            self._connection.execute(
                """INSERT INTO model_profiles
                (id,profile_key,runtime,model_name,quantization,context_limit,
                generation_config_json,enabled,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(profile_key) DO UPDATE SET model_name=excluded.model_name,
                context_limit=excluded.context_limit,generation_config_json=excluded.generation_config_json,
                enabled=1,updated_at=excluded.updated_at""",
                (
                    model_id,
                    "scout",
                    "ollama",
                    profile.model,
                    "runtime-reported",
                    profile.maximum_context_tokens,
                    generation_config,
                    1,
                    now.isoformat(),
                    now.isoformat(),
                ),
            )
            self._connection.execute(
                """INSERT OR IGNORE INTO prompt_versions
                (id,prompt_key,version,template_sha256,schema_version,template_path,created_at)
                VALUES (?,?,1,?,'1.0',?,?)""",
                (
                    prompt_id,
                    prompt_key,
                    prompt_hash,
                    f"app.analysis.prompts/{prompt_key}.v1.txt",
                    now.isoformat(),
                ),
            )
        model_row = self._connection.execute(
            "SELECT id FROM model_profiles WHERE profile_key='scout'"
        ).fetchone()
        prompt_row = self._connection.execute(
            "SELECT id FROM prompt_versions WHERE prompt_key=? AND version=1", (prompt_key,)
        ).fetchone()
        if model_row is None or prompt_row is None:
            raise AnalysisServiceError(
                "RUNTIME_REGISTRATION_FAILED",
                "The local analysis runtime could not be registered.",
                status_code=500,
            )
        return str(model_row["id"]), str(prompt_row["id"])

    def _profile(self, analysis_type: AnalysisType) -> GenerationModelProfile:
        if analysis_type is AnalysisType.FAST_BRIEF:
            return self._settings.models.scout
        return self._settings.models.scout.model_copy(
            update={
                "maximum_context_tokens": min(
                    self._settings.models.analyst.maximum_context_tokens,
                    self._settings.token_limits.maximum_context_tokens,
                ),
                "maximum_output_tokens": min(
                    self._settings.models.analyst.maximum_output_tokens,
                    self._settings.token_limits.maximum_output_tokens,
                ),
            }
        )

    def _find_existing(
        self, analysis_type: AnalysisType, fingerprint: str, model_id: str, prompt_id: str
    ) -> AnalysisRun | None:
        row = self._connection.execute(
            """SELECT id FROM analysis_runs WHERE analysis_type=? AND input_fingerprint=?
            AND model_profile_id=? AND prompt_version_id=?""",
            (analysis_type.value, fingerprint, model_id, prompt_id),
        ).fetchone()
        return None if row is None else self._repositories.analyses.get(str(row["id"]))

    def _enforce_daily_budget(self, analysis_type: AnalysisType, now: datetime) -> None:
        start = now.astimezone(UTC).date().isoformat()
        count = int(
            self._connection.execute(
                """SELECT COUNT(*) FROM analysis_runs WHERE analysis_type=? AND status='succeeded'
                AND created_at>=?""",
                (analysis_type.value, f"{start}T00:00:00+00:00"),
            ).fetchone()[0]
        )
        limit = (
            self._settings.daily_work.maximum_fast_briefs
            if analysis_type is AnalysisType.FAST_BRIEF
            else self._settings.daily_work.maximum_automatic_deep_dives
        )
        if count >= limit:
            raise AnalysisServiceError(
                "DAILY_LIMIT",
                "The configured daily local-analysis limit has been reached.",
                status_code=429,
            )

    @staticmethod
    def _load_prompt(prompt_key: str) -> str:
        resource = files("app.analysis.prompts").joinpath(f"{prompt_key}.v1.txt")
        return resource.read_text(encoding="utf-8")

    def _persist_claims(
        self,
        run: AnalysisRun,
        report: FastBrief | DeepDiveReport,
        work: WorkInput,
    ) -> None:
        now = self._clock().isoformat()
        section_id = new_ulid()
        markdown = (
            report.change
            if isinstance(report, FastBrief)
            else report.executive_significance.markdown
        )
        self._connection.execute(
            """INSERT INTO analysis_sections
            (id,analysis_run_id,section_key,section_order,status,content_markdown,
            structured_json,confidence,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                section_id,
                run.id,
                "brief" if isinstance(report, FastBrief) else "report",
                1,
                "verified",
                markdown,
                report.model_dump_json(),
                1.0,
                now,
                now,
            ),
        )
        allowed = {item.id for item in work.evidence}
        if isinstance(report, FastBrief):
            claim_rows = tuple(
                (claim, "major", "supported" if claim.evidence_ids else "unsupported")
                for claim in report.claims
            )
        else:
            claim_rows = tuple(
                (claim, claim.importance, claim.verification_status) for claim in report.claims
            )
        for index, (claim, importance, verification) in enumerate(claim_rows, start=1):
            claim_id = f"{run.id}-claim-{index}"
            self._connection.execute(
                """INSERT INTO claims
                (id,analysis_section_id,claim_text,claim_type,importance,verification_status,created_at)
                VALUES (?,?,?,?,?,?,?)""",
                (
                    claim_id,
                    section_id,
                    claim.text,
                    claim.type.value,
                    importance,
                    verification,
                    now,
                ),
            )
            for evidence_id in sorted(set(claim.evidence_ids) & allowed):
                self._connection.execute(
                    """INSERT INTO claim_evidence
                    (claim_id,evidence_span_id,relation,relevance) VALUES (?,?,'supports',1.0)""",
                    (claim_id, evidence_id),
                )

    @staticmethod
    def _public_result(run: AnalysisRun, *, cached: bool) -> AnalysisResult:
        output: FastBrief | DeepDiveReport | None = None
        coverage = 0.0
        verified = 0
        if run.output is not None:
            payload = dict(run.output)
            verification = payload.pop("_verification", {})
            if isinstance(verification, dict):
                coverage_value = verification.get("citation_coverage", 0)
                verified_value = verification.get("citations_verified", 0)
                if isinstance(coverage_value, int | float):
                    coverage = float(coverage_value)
                if isinstance(verified_value, int):
                    verified = verified_value
            model_type = (
                FastBrief if run.analysis_type is AnalysisType.FAST_BRIEF else DeepDiveReport
            )
            try:
                output = model_type.model_validate(payload)
            except ValidationError:
                return AnalysisResult(
                    id=run.id,
                    work_id=run.work_id,
                    analysis_type=run.analysis_type,
                    status=AnalysisStatus.REJECTED,
                    cached=cached,
                    duration_ms=run.duration_ms,
                    error_code="STORED_OUTPUT_OUTDATED",
                    safe_detail=(
                        "This stored report predates current output validation. "
                        "Generate it again to replace the cached view."
                    ),
                    created_at=run.created_at,
                )
        return AnalysisResult(
            id=run.id,
            work_id=run.work_id,
            analysis_type=run.analysis_type,
            status=run.status,
            cached=cached,
            citation_coverage=coverage,
            citations_verified=verified,
            duration_ms=run.duration_ms,
            error_code=run.error_code,
            safe_detail=run.error_detail,
            output=output,
            created_at=run.created_at,
        )
