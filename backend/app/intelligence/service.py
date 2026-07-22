"""Deterministic assembly and persisted publication gates for V1 outputs."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from typing import cast

from app.analysis.models import AnalysisResult, DeepDiveReport, FastBrief
from app.analysis.service import AnalysisServiceError, ScoutAnalysisService
from app.catalog.identity import new_ulid
from app.db import transaction
from app.domain.models import AnalysisStatus, AnalysisType, JsonObject
from app.intelligence.models import (
    CommercialHypothesis,
    DailyIntelligenceReport,
    DeepDiveProgress,
    LearningPlanItem,
    ModelRankingSignal,
    Opportunity,
    PipelineReportSummary,
    ProjectRelevance,
    RankedReportItem,
    SourceCoverage,
    StageState,
    TopicOverview,
    TopicPaper,
)

STAGES = ("extract", "analyze", "skeptic_check", "verify_citations", "publish")


class IntelligenceOutputService:
    """Build cached outputs while keeping deterministic ranking authoritative."""

    def __init__(
        self, connection: sqlite3.Connection, scout: ScoutAnalysisService | None = None
    ) -> None:
        self._connection = connection
        self._scout = scout

    def ranking_signals(self, limit: int = 100) -> tuple[ModelRankingSignal, ...]:
        rows = self._connection.execute(
            """SELECT w.id work_id,a.id analysis_id,a.input_fingerprint,a.output_json
            FROM works w LEFT JOIN analysis_runs a ON a.id=(SELECT a2.id FROM analysis_runs a2
              WHERE a2.work_id=w.id AND a2.analysis_type='fast_brief' AND a2.status='succeeded'
              ORDER BY a2.completed_at DESC,a2.id DESC LIMIT 1)
            WHERE w.work_type='paper' ORDER BY w.id LIMIT ?""",
            (limit,),
        ).fetchall()
        now = datetime.now(UTC).isoformat()
        results: list[ModelRankingSignal] = []
        with transaction(self._connection):
            for row in rows:
                signal = self._signal_from_brief(str(row["work_id"]), row["output_json"])
                fingerprint = str(row["input_fingerprint"] or f"fallback:{row['work_id']}")
                self._connection.execute(
                    """INSERT INTO ranking_model_signals
                    (work_id,analysis_run_id,input_fingerprint,novelty,method_depth,impact,
                     opportunity,confidence,fallback,evidence_ids_json,rationale,updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(work_id) DO UPDATE SET
                    analysis_run_id=excluded.analysis_run_id,input_fingerprint=excluded.input_fingerprint,
                    novelty=excluded.novelty,method_depth=excluded.method_depth,impact=excluded.impact,
                    opportunity=excluded.opportunity,confidence=excluded.confidence,
                    fallback=excluded.fallback,evidence_ids_json=excluded.evidence_ids_json,
                    rationale=excluded.rationale,updated_at=excluded.updated_at""",
                    (
                        signal.work_id,
                        row["analysis_id"],
                        fingerprint,
                        signal.novelty,
                        signal.method_depth,
                        signal.impact,
                        signal.opportunity,
                        signal.confidence,
                        int(signal.fallback),
                        json.dumps(signal.evidence_ids),
                        signal.rationale,
                        now,
                    ),
                )
                results.append(signal)
        return tuple(results)

    def deep_dive_candidates(self, limit: int) -> tuple[str, ...]:
        if not 1 <= limit <= 3:
            raise ValueError("deep-dive candidate limit must be between 1 and 3")
        day_start = f"{datetime.now(UTC).date().isoformat()}T00:00:00"
        selected: list[str] = []

        def add(rows: list[sqlite3.Row]) -> None:
            for row in rows:
                work_id = str(row[0])
                if work_id not in selected and len(selected) < limit:
                    selected.append(work_id)

        add(
            self._connection.execute(
                """SELECT a.work_id FROM analysis_runs a
                WHERE a.analysis_type='deep_dive' AND a.status='succeeded'
                  AND a.created_at>=?
                ORDER BY a.completed_at DESC,a.id DESC LIMIT ?""",
                (day_start, limit),
            ).fetchall()
        )
        add(
            self._connection.execute(
                """SELECT j.work_id FROM deep_dive_jobs j
                JOIN ranking_profiles rp ON rp.active=1
                JOIN ranking_results rr ON rr.profile_id=rp.id AND rr.work_id=j.work_id
                  AND rr.score_kind='deep_dive_priority'
                WHERE j.status IN ('failed','rejected')
                ORDER BY rr.total_score DESC,j.updated_at,j.id LIMIT ?""",
                (limit,),
            ).fetchall()
        )
        add(
            self._connection.execute(
                """SELECT rr.work_id FROM ranking_results rr
            JOIN ranking_profiles rp ON rp.id=rr.profile_id AND rp.active=1
            JOIN works w ON w.id=rr.work_id
            JOIN work_versions v ON v.id=w.current_version_id
            WHERE rr.score_kind='deep_dive_priority'
              AND EXISTS (SELECT 1 FROM documents d JOIN evidence_spans e
                ON e.document_id=d.id WHERE d.work_version_id=v.id
                AND d.parse_status IN ('parsed','partial'))
            ORDER BY rr.total_score DESC,rr.work_id LIMIT ?""",
                (limit,),
            ).fetchall()
        )
        return tuple(selected)

    @staticmethod
    def _signal_from_brief(work_id: str, raw: object) -> ModelRankingSignal:
        neutral = ModelRankingSignal(
            work_id=work_id,
            novelty=0.5,
            method_depth=0.5,
            impact=0.5,
            opportunity=0.5,
            confidence=0,
            refinement=0,
            fallback=True,
            evidence_ids=(),
            rationale="Deterministic neutral fallback; no verified Scout brief is available.",
        )
        if not raw:
            return neutral
        try:
            payload = json.loads(str(raw))
            payload.pop("_verification", None)
            brief = FastBrief.model_validate(payload)
        except (ValueError, TypeError):
            return neutral
        evidence_ids = tuple(dict.fromkeys(e for claim in brief.claims for e in claim.evidence_ids))
        cited = sum(bool(claim.evidence_ids) for claim in brief.claims)
        confidence = min(1.0, cited / max(1, len(brief.claims)))
        novelty = min(1.0, 0.4 + len(brief.contribution) / 1600)
        method = min(1.0, 0.35 + len(brief.technical_relevance) / 1600)
        impact = min(1.0, 0.35 + len(brief.change) / 640)
        opportunity = min(1.0, 0.35 + len(brief.commercial_relevance) / 1600)
        raw_refinement = ((novelty + method + impact + opportunity) / 4 - 0.5) * 5
        refinement = max(-2.5, min(2.5, raw_refinement * confidence))
        return ModelRankingSignal(
            work_id=work_id,
            novelty=novelty,
            method_depth=method,
            impact=impact,
            opportunity=opportunity,
            confidence=confidence,
            refinement=refinement,
            fallback=False,
            evidence_ids=evidence_ids,
            rationale=(
                "Bounded confidence-shrunk hypotheses derived from a citation-verified Scout brief."
            ),
        )

    async def run_deep_dive(self, work_id: str) -> tuple[AnalysisResult, DeepDiveProgress]:
        if self._scout is None:
            raise RuntimeError("Scout is required for a deep dive")
        work = self._connection.execute(
            """SELECT w.current_version_id FROM works w WHERE w.id=?""", (work_id,)
        ).fetchone()
        if work is None:
            raise AnalysisServiceError("WORK_NOT_FOUND", "Paper not found.", status_code=404)
        evidence = self._connection.execute(
            """SELECT e.id,e.normalized_text_sha256 FROM evidence_spans e JOIN documents d
            ON d.id=e.document_id WHERE d.work_version_id=? ORDER BY e.id""",
            (work["current_version_id"],),
        ).fetchall()
        if not evidence:
            raise AnalysisServiceError("EVIDENCE_REQUIRED", "This paper has no parsed evidence.")
        fingerprint = hashlib.sha256(
            (str(work["current_version_id"]) + "|" + "|".join(str(e[1]) for e in evidence)).encode()
        ).hexdigest()
        now = datetime.now(UTC).isoformat()
        job = self._connection.execute(
            "SELECT * FROM deep_dive_jobs WHERE input_fingerprint=?", (fingerprint,)
        ).fetchone()
        if job is None:
            job_id = new_ulid()
            with transaction(self._connection):
                self._connection.execute(
                    """INSERT INTO deep_dive_jobs
                    (id,work_id,work_version_id,input_fingerprint,status,created_at,updated_at)
                    VALUES (?,?,?,?,?,?,?)""",
                    (job_id, work_id, work["current_version_id"], fingerprint, "running", now, now),
                )
                for order, key in enumerate(STAGES, 1):
                    self._connection.execute(
                        """INSERT INTO deep_dive_stages
                        (job_id,stage_key,stage_order,status,input_fingerprint)
                        VALUES (?,?,?,?,?)""",
                        (job_id, key, order, "pending", fingerprint),
                    )
        else:
            job_id = str(job["id"])
            if str(job["status"]) == "succeeded" and job["analysis_run_id"]:
                cached = self._scout.get_analysis(str(job["analysis_run_id"]))
                if cached is not None:
                    return cached, self.progress(job_id)
        self._stage(job_id, "extract", "succeeded", {"evidence_ids": [str(e[0]) for e in evidence]})
        current = self._connection.execute(
            "SELECT analysis_run_id FROM deep_dive_jobs WHERE id=?", (job_id,)
        ).fetchone()
        analysis: AnalysisResult
        if current is not None and current[0]:
            previous = self._scout.get_analysis(str(current[0]))
            if previous is not None and previous.status in {
                AnalysisStatus.FAILED,
                AnalysisStatus.REJECTED,
            }:
                analysis = await self._scout.retry_analysis(previous.id)
            elif previous is not None:
                analysis = previous
            else:
                analysis = await self._scout.analyze(work_id, AnalysisType.DEEP_DIVE)
        else:
            analysis = await self._scout.analyze(work_id, AnalysisType.DEEP_DIVE)
        with transaction(self._connection):
            self._connection.execute(
                "UPDATE deep_dive_jobs SET analysis_run_id=?,updated_at=? WHERE id=?",
                (analysis.id, datetime.now(UTC).isoformat(), job_id),
            )
        if analysis.status is not AnalysisStatus.SUCCEEDED or not isinstance(
            analysis.output, DeepDiveReport
        ):
            self._stage(job_id, "analyze", "failed", error=analysis.error_code or "ANALYSIS_FAILED")
            self._finish_job(job_id, "failed", analysis.error_code)
            return analysis, self.progress(job_id)
        report = analysis.output
        self._stage(job_id, "analyze", "succeeded", {"analysis_run_id": analysis.id})
        critical = [
            finding
            for finding in report.skeptic_findings
            if finding.severity == "critical" and finding.resolution in {"rejected", "unresolved"}
        ]
        if critical:
            self._stage(job_id, "skeptic_check", "rejected", error="CRITICAL_SKEPTIC_FINDING")
            self._finish_job(job_id, "rejected", "CRITICAL_SKEPTIC_FINDING")
            return analysis.model_copy(
                update={
                    "status": AnalysisStatus.REJECTED,
                    "output": None,
                    "error_code": "CRITICAL_SKEPTIC_FINDING",
                    "safe_detail": "A critical unresolved skeptic finding blocked publication.",
                }
            ), self.progress(job_id)
        self._stage(
            job_id, "skeptic_check", "succeeded", {"findings": len(report.skeptic_findings)}
        )
        unsupported = [
            claim.id
            for claim in report.claims
            if claim.type.value in {"fact", "interpretation"}
            and (claim.verification_status != "supported" or not claim.evidence_ids)
        ]
        if unsupported:
            self._stage(job_id, "verify_citations", "rejected", error="UNSUPPORTED_CLAIMS")
            self._finish_job(job_id, "rejected", "UNSUPPORTED_CLAIMS")
            return analysis.model_copy(
                update={
                    "status": AnalysisStatus.REJECTED,
                    "output": None,
                    "error_code": "UNSUPPORTED_CLAIMS",
                    "safe_detail": "Unsupported factual claims were rejected before publication.",
                }
            ), self.progress(job_id)
        self._stage(
            job_id,
            "verify_citations",
            "succeeded",
            {"coverage": analysis.citation_coverage, "verified": analysis.citations_verified},
        )
        self._stage(job_id, "publish", "succeeded", {"analysis_run_id": analysis.id})
        self._finish_job(job_id, "succeeded", None)
        return analysis, self.progress(job_id)

    def _stage(
        self,
        job_id: str,
        key: str,
        status: str,
        output: dict[str, object] | None = None,
        error: str | None = None,
    ) -> None:
        existing = self._connection.execute(
            "SELECT status FROM deep_dive_stages WHERE job_id=? AND stage_key=?", (job_id, key)
        ).fetchone()
        if existing is not None and str(existing[0]) == "succeeded":
            return
        now = datetime.now(UTC).isoformat()
        with transaction(self._connection):
            self._connection.execute(
                """UPDATE deep_dive_stages SET status=?,output_json=?,error_code=?,
                started_at=COALESCE(started_at,?),completed_at=? WHERE job_id=? AND stage_key=?""",
                (
                    status,
                    json.dumps(output) if output is not None else None,
                    error,
                    now,
                    now,
                    job_id,
                    key,
                ),
            )

    def _finish_job(self, job_id: str, status: str, error: str | None) -> None:
        with transaction(self._connection):
            self._connection.execute(
                "UPDATE deep_dive_jobs SET status=?,error_code=?,updated_at=? WHERE id=?",
                (status, error, datetime.now(UTC).isoformat(), job_id),
            )

    def progress(self, job_id: str) -> DeepDiveProgress:
        job = self._connection.execute(
            "SELECT * FROM deep_dive_jobs WHERE id=?", (job_id,)
        ).fetchone()
        if job is None:
            raise AnalysisServiceError(
                "DEEP_DIVE_NOT_FOUND", "Deep dive not found.", status_code=404
            )
        stages = self._connection.execute(
            """SELECT stage_key,stage_order,status,error_code FROM deep_dive_stages
            WHERE job_id=? ORDER BY stage_order""",
            (job_id,),
        ).fetchall()
        return DeepDiveProgress(
            job_id=job_id,
            work_id=str(job["work_id"]),
            status=str(job["status"]),
            analysis_run_id=None if job["analysis_run_id"] is None else str(job["analysis_run_id"]),
            stages=tuple(
                StageState(
                    key=str(row[0]), order=int(row[1]), status=str(row[2]), error_code=row[3]
                )
                for row in stages
            ),
        )

    def progress_for_analysis(self, analysis_id: str) -> DeepDiveProgress:
        row = self._connection.execute(
            "SELECT id FROM deep_dive_jobs WHERE analysis_run_id=?", (analysis_id,)
        ).fetchone()
        if row is None:
            raise AnalysisServiceError(
                "DEEP_DIVE_NOT_FOUND", "Deep dive not found.", status_code=404
            )
        return self.progress(str(row[0]))

    def assemble_daily_report(self) -> DailyIntelligenceReport:
        run = self._connection.execute(
            """SELECT * FROM pipeline_runs WHERE run_type='daily' AND status='succeeded'
            ORDER BY completed_at DESC,id DESC LIMIT 1"""
        ).fetchone()
        if run is None:
            raise AnalysisServiceError(
                "DAILY_RUN_REQUIRED", "Run the daily pipeline first.", status_code=409
            )
        counts_raw = json.loads(str(run["config_snapshot_json"])).get("result", {})
        signals = {signal.work_id: signal for signal in self.ranking_signals()}
        technical = self._ranked("technical", signals)
        commercial = self._ranked("commercial", signals)
        deep_rows = self._connection.execute(
            """SELECT a.id FROM analysis_runs a WHERE a.analysis_type='deep_dive'
            AND a.status='succeeded' ORDER BY a.completed_at DESC LIMIT 2"""
        ).fetchall()
        analyzed = int(counts_raw.get("deep_dives_generated", 0)) + int(
            counts_raw.get("deep_dives_cached", 0)
        )
        failed = int(counts_raw.get("documents_failed", 0))
        learning_focus = self._learning_focus()
        coverage_gaps = self._coverage_gaps()
        happened = tuple(
            str(row[0])
            for row in self._connection.execute(
                "SELECT title FROM linked_events ORDER BY occurred_at DESC,id LIMIT 8"
            )
        )
        launches = tuple(
            str(row[0])
            for row in self._connection.execute(
                """SELECT title FROM source_artifacts
                WHERE artifact_type IN ('repository','release','model','dataset','space')
                ORDER BY COALESCE(updated_at,published_at,created_at) DESC LIMIT 8"""
            )
        )
        community = tuple(
            str(row[0])
            for row in self._connection.execute(
                """SELECT title FROM source_artifacts
                WHERE content_class='community_reaction' OR authority<0.5
                ORDER BY COALESCE(updated_at,published_at,created_at) DESC LIMIT 8"""
            )
        )
        learning_plan = tuple(
            LearningPlanItem(
                topic=topic,
                prerequisites=("Read the cited primary source", "Review the stored evidence spans"),
                estimated_minutes=45,
                recommended_item=technical[0].title if technical else "No ranked item available",
                exercise="Reproduce one bounded claim with a local fixture and record the result.",
                evidence_ids=(
                    ()
                    if not technical or technical[0].model_signal is None
                    else technical[0].model_signal.evidence_ids
                ),
            )
            for topic in learning_focus[:3]
        )

        def evidence_for_work(item: RankedReportItem) -> tuple[str, ...]:
            if item.model_signal is not None and item.model_signal.evidence_ids:
                return item.model_signal.evidence_ids
            return tuple(
                str(row[0])
                for row in self._connection.execute(
                    """SELECT e.id FROM evidence_spans e
                    JOIN documents d ON d.id=e.document_id
                    JOIN work_versions v ON v.id=d.work_version_id
                    WHERE v.work_id=? ORDER BY e.page_start,e.id LIMIT 3""",
                    (item.work_id,),
                )
            )

        commercial_hypotheses = tuple(
            CommercialHypothesis(
                label="commercial_hypothesis",
                problem=f"Teams lack validated operational guidance for {item.title}.",
                target_buyer="Indian mid-market AI and software teams",
                proposed_offer=(
                    "A paid validation pilot with evidence-backed implementation guidance."
                ),
                supporting_evidence=evidence_for_work(item),
                prototype="Build one local bounded workflow around the verified capability.",
                effort="Two to five engineering days for a validation prototype.",
                validation_experiment=(
                    "Interview five buyers and run one paid or letter-of-intent pilot."
                ),
                pricing_hypothesis=(
                    "INR 50,000-200,000 pilot; validate willingness to pay before quoting."
                ),
                competition="Existing consulting, internal engineering, and adjacent AI tooling.",
                risks=("Evidence may not transfer to production.", "Buyer urgency is unvalidated."),
                confidence=min(0.75, 0.35 + item.score / 250),
            )
            for item in commercial
            if evidence_for_work(item)
        )
        commercial_hypotheses = commercial_hypotheses[:3]
        projects = ("RentAssure", "SageAlpha", "BidReady", "US-school chatbot")
        project_relevance = tuple(
            ProjectRelevance(
                project=project,
                relevance=(
                    f"Evaluate {technical[0].title} against this project's current retrieval, "
                    "agent, or reliability constraints."
                    if technical
                    else "No verified development is available for project mapping."
                ),
                evidence_ids=(
                    ()
                    if not technical or technical[0].model_signal is None
                    else technical[0].model_signal.evidence_ids
                ),
            )
            for project in projects
        )
        raw_source_counts = counts_raw.get("source_counts", {})
        source_count_values = (
            cast(JsonObject, raw_source_counts) if isinstance(raw_source_counts, dict) else {}
        )
        source_counts = {
            key: value
            for key, value in source_count_values.items()
            if isinstance(value, int) and not isinstance(value, bool)
        }
        source_coverage = tuple(
            SourceCoverage(
                source_key=str(row[0]),
                records=int(source_counts.get(str(row[0]), 0)),
                status=str(row[1]),
            )
            for row in self._connection.execute(
                "SELECT source_key,health_status FROM sources ORDER BY source_key"
            )
        )
        agent_health = {
            str(row[0]): str(row[1])
            for row in self._connection.execute(
                """SELECT agent_id,status FROM agent_executions
                WHERE pipeline_run_id=? ORDER BY stage_order""",
                (run["id"],),
            )
        }
        report = DailyIntelligenceReport(
            schema_version="1.0",
            report_date=str(run["completed_at"])[:10],
            pipeline=PipelineReportSummary(
                discovered=int(counts_raw.get("fetched", 0)),
                normalized=int(counts_raw.get("normalized", 0)),
                filtered=int(counts_raw.get("works_ranked", 0)),
                shortlisted=len(technical),
                briefed=int(counts_raw.get("briefs_generated", 0))
                + int(counts_raw.get("briefs_cached", 0)),
                analyzed=analyzed,
                failed=failed,
                run_id=str(run["id"]),
            ),
            top_technical=technical,
            top_commercial=commercial,
            deep_dives=tuple(str(row[0]) for row in deep_rows),
            important_updates=self._updates(),
            learning_focus=learning_focus,
            coverage_gaps=coverage_gaps,
            executive_briefing=(
                f"{len(happened)} linked developments were reviewed; "
                f"{len(technical)} technical priorities and {len(commercial_hypotheses)} "
                "commercial hypotheses passed deterministic assembly."
            ),
            what_happened=happened,
            why_it_matters=tuple(item.reason for item in technical[:5]),
            evidence_versus_interpretation=(
                "Facts require stored evidence; interpretations remain labelled and subordinate.",
                "Community and promotional signals cannot establish primary technical claims.",
            ),
            research_and_product_launches=launches,
            community_signals=community,
            learning_plan=learning_plan,
            what_to_build=tuple(
                f"Prototype one bounded integration for {item.title}." for item in technical[:3]
            ),
            commercial_hypotheses=commercial_hypotheses,
            india_market_hypotheses=tuple(
                hypothesis.pricing_hypothesis for hypothesis in commercial_hypotheses
            ),
            personal_relevance=project_relevance,
            risks_and_unknowns=tuple(
                dict.fromkeys(
                    (*coverage_gaps, "Commercial demand and pricing require buyer validation.")
                )
            ),
            watchlist_changes=launches[:5],
            source_coverage=source_coverage,
            agent_health=agent_health,
        )
        payload = report.model_dump_json()
        fingerprint = hashlib.sha256(payload.encode()).hexdigest()
        now = datetime.now(UTC).isoformat()
        with transaction(self._connection):
            self._connection.execute(
                """INSERT INTO daily_reports
                (report_date,schema_version,pipeline_run_id,input_fingerprint,report_json,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?) ON CONFLICT(report_date) DO UPDATE SET
                pipeline_run_id=excluded.pipeline_run_id,input_fingerprint=excluded.input_fingerprint,
                report_json=excluded.report_json,updated_at=excluded.updated_at""",
                (report.report_date, "1.0", run["id"], fingerprint, payload, now, now),
            )
        return report

    def latest_daily_report(self) -> DailyIntelligenceReport:
        row = self._connection.execute(
            "SELECT report_json FROM daily_reports ORDER BY report_date DESC LIMIT 1"
        ).fetchone()
        if row is None:
            raise AnalysisServiceError(
                "DAILY_RUN_REQUIRED", "Run the daily pipeline first.", status_code=409
            )
        try:
            return DailyIntelligenceReport.model_validate_json(str(row[0]))
        except ValueError as error:
            raise AnalysisServiceError(
                "DAILY_REPORT_INVALID",
                "The stored daily report is invalid. Run the daily pipeline again.",
                status_code=409,
            ) from error

    def _ranked(
        self, kind: str, signals: dict[str, ModelRankingSignal]
    ) -> tuple[RankedReportItem, ...]:
        rows = self._connection.execute(
            """SELECT rr.work_id,v.title,rr.total_score,
            EXISTS(SELECT 1 FROM analysis_runs a WHERE a.work_id=rr.work_id
              AND a.status='succeeded') analyzed
            FROM ranking_results rr JOIN ranking_profiles rp ON rp.id=rr.profile_id AND rp.active=1
            JOIN works w ON w.id=rr.work_id JOIN work_versions v ON v.id=w.current_version_id
            WHERE rr.score_kind=? ORDER BY rr.total_score DESC,rr.work_id LIMIT 10""",
            (kind,),
        ).fetchall()
        return tuple(
            RankedReportItem(
                work_id=str(row[0]),
                title=str(row[1]),
                score=float(row[2]),
                reason=(
                    "Deterministic V1 score; the model hypothesis is separate "
                    "and never changes order."
                ),
                status="analyzed" if row[3] else "unreviewed",
                model_signal=signals.get(str(row[0])),
            )
            for row in rows
        )

    def _updates(self) -> tuple[dict[str, str], ...]:
        rows = self._connection.execute(
            """SELECT w.id,v.title FROM works w JOIN work_versions v ON v.id=w.current_version_id
            WHERE EXISTS (SELECT 1 FROM work_versions prior
              WHERE prior.work_id=w.id AND prior.id<>v.id)
            ORDER BY v.observed_at DESC LIMIT 10"""
        ).fetchall()
        return tuple(
            {"work_id": str(row[0]), "update_type": "paper_revision", "summary": str(row[1])}
            for row in rows
        )

    def _learning_focus(self) -> tuple[str, ...]:
        rows = self._connection.execute(
            """SELECT a.output_json FROM analysis_runs a WHERE a.analysis_type='deep_dive'
            AND a.status='succeeded' ORDER BY a.completed_at DESC LIMIT 2"""
        ).fetchall()
        values: list[str] = []
        for row in rows:
            try:
                values.extend(
                    str(item["concept"])
                    for item in json.loads(str(row[0])).get("learning_path", [])
                )
            except (ValueError, TypeError, KeyError):
                continue
        return tuple(dict.fromkeys(values))[:10]

    def _coverage_gaps(self) -> tuple[str, ...]:
        gaps: list[str] = []
        missing_docs = int(
            self._connection.execute(
                """SELECT COUNT(*) FROM works w WHERE w.work_type='paper' AND NOT EXISTS
            (SELECT 1 FROM work_versions v JOIN documents d ON d.work_version_id=v.id
             WHERE v.work_id=w.id AND d.parse_status IN ('parsed','partial'))"""
            ).fetchone()[0]
        )
        if missing_docs:
            gaps.append(f"{missing_docs} ranked papers lack parsed primary-document evidence.")
        if not self._connection.execute(
            "SELECT 1 FROM analysis_runs WHERE analysis_type='deep_dive' AND status='succeeded'"
        ).fetchone():
            gaps.append("No verified deep dive is available yet.")
        return tuple(gaps)

    def topics(self) -> tuple[TopicOverview, ...]:
        rows = self._connection.execute(
            """SELECT t.id,t.topic_key,t.display_name,COUNT(DISTINCT wt.work_id),
            COUNT(DISTINCT CASE WHEN date(v.observed_at)=date('now') THEN wt.work_id END)
            FROM topics t LEFT JOIN work_topics wt ON wt.topic_id=t.id
            LEFT JOIN works w ON w.id=wt.work_id
            LEFT JOIN work_versions v ON v.id=w.current_version_id WHERE t.active=1
            GROUP BY t.id,t.topic_key,t.display_name
            ORDER BY COUNT(DISTINCT wt.work_id) DESC,t.topic_key"""
        ).fetchall()
        result: list[TopicOverview] = []
        for row in rows:
            papers = self._connection.execute(
                """SELECT w.id,v.title,COALESCE(rr.total_score,0) FROM work_topics wt
                JOIN works w ON w.id=wt.work_id JOIN work_versions v ON v.id=w.current_version_id
                LEFT JOIN ranking_profiles rp ON rp.active=1 LEFT JOIN ranking_results rr
                  ON rr.profile_id=rp.id AND rr.work_id=w.id AND rr.score_kind='technical'
                WHERE wt.topic_id=? ORDER BY COALESCE(rr.total_score,0) DESC,w.id LIMIT 5""",
                (row[0],),
            ).fetchall()
            result.append(
                TopicOverview(
                    key=str(row[1]),
                    label=str(row[2]),
                    paper_count=int(row[3]),
                    daily_change=int(row[4]),
                    papers=tuple(
                        TopicPaper(work_id=str(p[0]), title=str(p[1]), score=float(p[2]))
                        for p in papers
                    ),
                )
            )
        return tuple(result)

    def opportunities(self) -> tuple[Opportunity, ...]:
        rows = self._connection.execute(
            """SELECT a.work_id,v.title,a.output_json FROM analysis_runs a
            JOIN works w ON w.id=a.work_id
            JOIN work_versions v ON v.id=w.current_version_id WHERE a.analysis_type='deep_dive'
            AND a.status='succeeded' ORDER BY a.completed_at DESC LIMIT 10"""
        ).fetchall()
        result: list[Opportunity] = []
        for row in rows:
            try:
                report = DeepDiveReport.model_validate(json.loads(str(row[2])))
            except (ValueError, TypeError):
                continue
            claims = {
                claim.id: claim
                for claim in report.claims
                if claim.verification_status == "supported"
            }
            for item in report.production_applications:
                evidence_ids = tuple(
                    dict.fromkeys(
                        e
                        for cid in item.claim_ids
                        if cid in claims
                        for e in claims[cid].evidence_ids
                    )
                )
                if evidence_ids:
                    result.append(
                        Opportunity(
                            kind="engineering",
                            work_id=str(row[0]),
                            title=str(row[1]),
                            headline=item.name,
                            detail=item.expected_value,
                            evidence_ids=evidence_ids,
                            confidence=min(report.method.confidence, report.evaluation.confidence),
                        )
                    )
            for item in report.commercial_hypotheses:
                if item.evidence_ids:
                    result.append(
                        Opportunity(
                            kind="commercial",
                            work_id=str(row[0]),
                            title=str(row[1]),
                            headline=item.problem,
                            detail=f"{item.buyer}: {item.pilot}",
                            evidence_ids=item.evidence_ids,
                            confidence=item.confidence,
                        )
                    )
        return tuple(result)
