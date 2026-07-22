export type AnalysisType = "fast_brief" | "deep_dive";
export type AnalysisStatus = "queued" | "running" | "succeeded" | "failed" | "rejected";

export interface ModelStatus {
  readonly runtime: "ollama";
  readonly available: boolean;
  readonly model: string;
  readonly model_installed: boolean;
  readonly runtime_version: string | null;
  readonly active: boolean;
  readonly size_vram_mb: number;
  readonly detail: string;
}

export interface ReportClaim {
  readonly id?: string;
  readonly text: string;
  readonly type: "fact" | "interpretation" | "recommendation" | "hypothesis";
  readonly importance?: "minor" | "major" | "critical";
  readonly verification_status?: "unsupported" | "supported" | "conflicted" | "rejected";
  readonly evidence_ids: readonly string[];
  readonly qualifier?: string | null;
}

export interface FastBrief {
  readonly schema_version: "1.0";
  readonly work_id: string;
  readonly change: string;
  readonly problem: string;
  readonly contribution: string;
  readonly evidence_state: "strong" | "moderate" | "weak" | "unknown";
  readonly limitations: readonly string[];
  readonly code_state: string;
  readonly technical_relevance: string;
  readonly commercial_relevance: string;
  readonly recommended_action: string;
  readonly claims: readonly ReportClaim[];
}

export interface ReportSection {
  readonly markdown: string;
  readonly confidence: number;
  readonly claim_ids: readonly string[];
}

export interface DeepDive {
  readonly schema_version: "1.0";
  readonly work_id: string;
  readonly title: string;
  readonly publication_status: string;
  readonly executive_significance: ReportSection;
  readonly problem_context: ReportSection;
  readonly method: ReportSection;
  readonly evaluation: ReportSection;
  readonly limitations: ReportSection;
  readonly reproducibility: {
    readonly status: string;
    readonly repository_urls: readonly string[];
    readonly assets: readonly string[];
    readonly hardware_fit: string;
    readonly steps: readonly string[];
    readonly risks: readonly string[];
  };
  readonly production_applications: readonly Record<string, unknown>[];
  readonly commercial_hypotheses: readonly Record<string, unknown>[];
  readonly learning_path: readonly Record<string, unknown>[];
  readonly skeptic_findings: readonly {
    readonly severity: string;
    readonly finding: string;
    readonly affected_claim_ids: readonly string[];
    readonly resolution: string;
  }[];
  readonly claims: readonly ReportClaim[];
}

export interface AnalysisResult {
  readonly id: string;
  readonly work_id: string;
  readonly analysis_type: AnalysisType;
  readonly status: AnalysisStatus;
  readonly cached: boolean;
  readonly citation_coverage: number;
  readonly citations_verified: number;
  readonly duration_ms: number | null;
  readonly error_code: string | null;
  readonly safe_detail: string | null;
  readonly output: FastBrief | DeepDive | null;
  readonly created_at: string;
}

export interface DeepDiveProgress {
  readonly job_id: string;
  readonly work_id: string;
  readonly status: string;
  readonly analysis_run_id: string | null;
  readonly stages: readonly { readonly key: string; readonly order: number; readonly status: string; readonly error_code: string | null }[];
}

export interface TodayReport {
  readonly report_date: string;
  readonly model: ModelStatus;
  readonly ranked: readonly {
    readonly work_id: string;
    readonly title: string;
    readonly technical_score: number | null;
    readonly brief: AnalysisResult | null;
  }[];
  readonly generated_count: number;
  readonly remaining_fast_briefs: number;
  readonly remaining_deep_dives: number;
}

export interface RankedDailyItem {
  readonly work_id: string;
  readonly title: string;
  readonly score: number;
  readonly reason: string;
  readonly status: string;
  readonly model_signal?: {
    readonly novelty: number;
    readonly method_depth: number;
    readonly impact: number;
    readonly opportunity: number;
    readonly confidence: number;
    readonly evidence_ids: readonly string[];
    readonly fallback: boolean;
  } | null;
}

export interface LearningPlanItem {
  readonly topic: string;
  readonly why_it_matters: string;
  readonly prerequisites: readonly string[];
  readonly estimated_minutes: number;
  readonly recommended_item: string;
  readonly exercise: string;
  readonly expected_outcome: string;
  readonly evidence_ids: readonly string[];
}

export interface BuildPlanItem {
  readonly work_id: string;
  readonly prototype: string;
  readonly user_problem: string;
  readonly architecture: readonly string[];
  readonly estimated_effort: string;
  readonly recommended_resource: string;
  readonly validation_test: string;
  readonly project_relevance: readonly string[];
  readonly evidence_ids: readonly string[];
}

export interface CommercialHypothesis {
  readonly label: "commercial_hypothesis";
  readonly work_id: string;
  readonly title: string;
  readonly problem: string;
  readonly target_buyer: string;
  readonly proposed_offer: string;
  readonly supporting_evidence: readonly string[];
  readonly prototype: string;
  readonly effort: string;
  readonly validation_experiment: string;
  readonly pricing_hypothesis: string;
  readonly competition: string;
  readonly risks: readonly string[];
  readonly assumptions: readonly string[];
  readonly india_market_relevance: string;
  readonly project_relevance: readonly string[];
  readonly confidence: number;
}

export interface DailyIntelligenceReport {
  readonly schema_version: "1.0";
  readonly report_date: string;
  readonly pipeline: {
    readonly discovered: number; readonly normalized: number; readonly filtered: number;
    readonly shortlisted: number; readonly briefed: number; readonly analyzed: number;
    readonly failed: number; readonly run_id: string;
  };
  readonly top_technical: readonly RankedDailyItem[];
  readonly top_commercial: readonly RankedDailyItem[];
  readonly deep_dives: readonly string[];
  readonly important_updates: readonly Record<string, string>[];
  readonly learning_focus: readonly string[];
  readonly coverage_gaps: readonly string[];
  readonly executive_briefing: string;
  readonly what_happened: readonly string[];
  readonly why_it_matters: readonly string[];
  readonly evidence_versus_interpretation: readonly string[];
  readonly research_and_product_launches: readonly string[];
  readonly community_signals: readonly string[];
  readonly learning_plan: readonly LearningPlanItem[];
  readonly what_to_build: readonly BuildPlanItem[];
  readonly commercial_hypotheses: readonly CommercialHypothesis[];
  readonly risks_and_unknowns: readonly string[];
  readonly watchlist_changes: readonly string[];
  readonly source_coverage: readonly {
    readonly source_key: string;
    readonly records: number;
    readonly status: string;
  }[];
}

export class AnalysisApiError extends Error {
  constructor(message = "Local Scout analysis is unavailable.") {
    super(message);
    this.name = "AnalysisApiError";
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function isClaim(value: unknown): value is ReportClaim {
  return isRecord(value) && typeof value.text === "string" && typeof value.type === "string" &&
    Array.isArray(value.evidence_ids) && value.evidence_ids.every((item) => typeof item === "string");
}

function isOutput(value: unknown): value is FastBrief | DeepDive {
  if (!isRecord(value) || value.schema_version !== "1.0" || typeof value.work_id !== "string" ||
      !Array.isArray(value.claims) || !value.claims.every(isClaim)) return false;
  if (typeof value.change === "string") return typeof value.problem === "string";
  return typeof value.title === "string" && isRecord(value.executive_significance) &&
    typeof value.executive_significance.markdown === "string";
}

function isAnalysis(value: unknown): value is AnalysisResult {
  return isRecord(value) && typeof value.id === "string" && typeof value.work_id === "string" &&
    typeof value.analysis_type === "string" && typeof value.status === "string" &&
    typeof value.cached === "boolean" && typeof value.citation_coverage === "number" &&
    typeof value.citations_verified === "number" &&
    (typeof value.duration_ms === "number" || value.duration_ms === null) &&
    (typeof value.error_code === "string" || value.error_code === null) &&
    (typeof value.safe_detail === "string" || value.safe_detail === null) &&
    (value.output === null || isOutput(value.output)) && typeof value.created_at === "string";
}

function isModelStatus(value: unknown): value is ModelStatus {
  return isRecord(value) && value.runtime === "ollama" && typeof value.available === "boolean" &&
    typeof value.model === "string" && typeof value.model_installed === "boolean" &&
    (typeof value.runtime_version === "string" || value.runtime_version === null) &&
    typeof value.active === "boolean" && typeof value.size_vram_mb === "number" &&
    typeof value.detail === "string";
}

function isToday(value: unknown): value is TodayReport {
  return isRecord(value) && typeof value.report_date === "string" && isModelStatus(value.model) &&
    Array.isArray(value.ranked) && value.ranked.every((item) => isRecord(item) &&
      typeof item.work_id === "string" && typeof item.title === "string" &&
      (typeof item.technical_score === "number" || item.technical_score === null) &&
      (item.brief === null || isAnalysis(item.brief))) && typeof value.generated_count === "number" &&
    typeof value.remaining_fast_briefs === "number" && typeof value.remaining_deep_dives === "number";
}

async function json(response: Response): Promise<unknown> {
  let payload: unknown;
  try { payload = await response.json(); } catch { throw new AnalysisApiError("The local API returned unreadable data."); }
  if (!response.ok) {
    const detail = isRecord(payload) && typeof payload.detail === "string" ? payload.detail : undefined;
    throw new AnalysisApiError(detail);
  }
  return payload;
}

export async function fetchToday(fetcher: typeof fetch, base: string, signal: AbortSignal): Promise<TodayReport> {
  const payload = await json(await fetcher(`${base}/reports/today`, { signal, headers: { Accept: "application/json" } }));
  if (!isToday(payload)) throw new AnalysisApiError("Today's report response was invalid.");
  return payload;
}

export async function fetchCompleteDailyReport(fetcher: typeof fetch, base: string, signal: AbortSignal): Promise<DailyIntelligenceReport> {
  const value = await json(await fetcher(`${base}/reports/daily/complete`, { signal, headers: { Accept: "application/json" } }));
  if (!isRecord(value) || value.schema_version !== "1.0" || typeof value.report_date !== "string" ||
      !isRecord(value.pipeline) || typeof value.pipeline.run_id !== "string" ||
      !Array.isArray(value.top_technical) || !Array.isArray(value.top_commercial) ||
      !Array.isArray(value.deep_dives) || !value.deep_dives.every((id) => typeof id === "string") ||
      !Array.isArray(value.learning_focus) || !Array.isArray(value.coverage_gaps) ||
      typeof value.executive_briefing !== "string" || !Array.isArray(value.learning_plan) ||
      !Array.isArray(value.what_to_build) || !Array.isArray(value.commercial_hypotheses) ||
      !Array.isArray(value.source_coverage)) {
    throw new AnalysisApiError("The final daily report response was invalid.");
  }
  return value as unknown as DailyIntelligenceReport;
}

export async function fetchModelStatus(fetcher: typeof fetch, base: string, signal: AbortSignal): Promise<ModelStatus> {
  const payload = await json(await fetcher(`${base}/models/scout/status`, { signal, headers: { Accept: "application/json" } }));
  if (!isModelStatus(payload)) throw new AnalysisApiError("The Scout model status was invalid.");
  return payload;
}

export async function generateToday(fetcher: typeof fetch, base: string): Promise<TodayReport> {
  const payload = await json(await fetcher(`${base}/reports/today/generate?limit=1`, { method: "POST", headers: { Accept: "application/json" } }));
  if (!isToday(payload)) throw new AnalysisApiError("Today's generated report was invalid.");
  return payload;
}

export async function generateAnalysis(fetcher: typeof fetch, base: string, workId: string, type: "brief" | "deep-dive"): Promise<AnalysisResult> {
  const payload = await json(await fetcher(`${base}/items/${encodeURIComponent(workId)}/${type}`, { method: "POST", headers: { Accept: "application/json" } }));
  if (!isAnalysis(payload)) throw new AnalysisApiError("The Scout analysis response was invalid.");
  return payload;
}

export async function fetchAnalysis(fetcher: typeof fetch, base: string, id: string, signal: AbortSignal): Promise<AnalysisResult> {
  const payload = await json(await fetcher(`${base}/analyses/${encodeURIComponent(id)}`, { signal, headers: { Accept: "application/json" } }));
  if (!isAnalysis(payload)) throw new AnalysisApiError("The stored analysis response was invalid.");
  return payload;
}

export async function fetchAnalysisProgress(fetcher: typeof fetch, base: string, id: string, signal: AbortSignal): Promise<DeepDiveProgress> {
  const payload = await json(await fetcher(`${base}/analyses/${encodeURIComponent(id)}/progress`, { signal, headers: { Accept: "application/json" } }));
  if (!isRecord(payload) || typeof payload.job_id !== "string" || typeof payload.work_id !== "string" ||
      typeof payload.status !== "string" || !Array.isArray(payload.stages) || !payload.stages.every((stage) =>
        isRecord(stage) && typeof stage.key === "string" && typeof stage.order === "number" && typeof stage.status === "string")) {
    throw new AnalysisApiError("Deep-dive progress was invalid.");
  }
  return payload as unknown as DeepDiveProgress;
}

export async function retryAnalysis(fetcher: typeof fetch, base: string, id: string): Promise<AnalysisResult> {
  const payload = await json(await fetcher(`${base}/analyses/${encodeURIComponent(id)}/retry`, {
    method: "POST",
    headers: { Accept: "application/json" },
  }));
  if (!isAnalysis(payload)) throw new AnalysisApiError("The retried Scout response was invalid.");
  return payload;
}
