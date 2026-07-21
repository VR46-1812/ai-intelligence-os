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
