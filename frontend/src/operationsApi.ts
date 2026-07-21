import type { ModelStatus } from "./analysisApi";

export interface DailyCounts {
  readonly fetched: number;
  readonly normalized: number;
  readonly documents_processed: number;
  readonly documents_failed: number;
  readonly evidence_spans: number;
  readonly works_ranked: number;
  readonly briefs_generated: number;
  readonly briefs_cached: number;
  readonly files_cleaned: number;
}

export interface DailyRunResult {
  readonly run_id: string;
  readonly status: "queued" | "running" | "succeeded" | "failed" | "cancelled" | "deferred";
  readonly trigger: "manual" | "schedule" | "retry";
  readonly counts: DailyCounts;
  readonly started_at: string;
  readonly completed_at: string | null;
  readonly safe_detail: string | null;
}

export interface DailyRunStatus {
  readonly scheduler_enabled: boolean;
  readonly schedule: string;
  readonly running: boolean;
  readonly current_run_id: string | null;
  readonly latest_run: DailyRunResult | null;
  readonly latest_success_at: string | null;
  readonly next_run_at: string | null;
}

export interface SystemStatus {
  readonly daily: DailyRunStatus;
  readonly source: {
    readonly source_key: string;
    readonly health: string;
    readonly checkpoint: Readonly<Record<string, unknown>> | null;
    readonly last_attempt_at: string | null;
    readonly last_success_at: string | null;
  };
  readonly model: ModelStatus;
  readonly resources: {
    readonly non_llm_ram_mb: number;
    readonly normal_total_ram_mb: number;
    readonly temporary_peak_ram_mb: number;
    readonly reserved_windows_ram_mb: number;
    readonly vram_target_mb: number;
    readonly download_concurrency: number;
    readonly generation_concurrency: number;
    readonly maximum_storage_gib: number;
  };
  readonly storage_bytes: number;
  readonly failures: readonly {
    readonly kind: string;
    readonly run_id: string;
    readonly occurred_at: string;
    readonly safe_detail: string;
    readonly retryable: boolean;
  }[];
}

export class OperationsApiError extends Error {
  constructor(message = "Local operations are unavailable.") {
    super(message);
    this.name = "OperationsApiError";
  }
}

function record(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function nullableString(value: unknown): value is string | null {
  return typeof value === "string" || value === null;
}

function counts(value: unknown): value is DailyCounts {
  return record(value) && ["fetched", "normalized", "documents_processed", "documents_failed",
    "evidence_spans", "works_ranked", "briefs_generated", "briefs_cached", "files_cleaned"]
    .every((key) => typeof value[key] === "number");
}

function run(value: unknown): value is DailyRunResult {
  return record(value) && typeof value.run_id === "string" && typeof value.status === "string" &&
    typeof value.trigger === "string" && counts(value.counts) && typeof value.started_at === "string" &&
    nullableString(value.completed_at) && nullableString(value.safe_detail);
}

function daily(value: unknown): value is DailyRunStatus {
  return record(value) && typeof value.scheduler_enabled === "boolean" && typeof value.schedule === "string" &&
    typeof value.running === "boolean" && nullableString(value.current_run_id) &&
    (value.latest_run === null || run(value.latest_run)) && nullableString(value.latest_success_at) &&
    nullableString(value.next_run_at);
}

async function payload(response: Response): Promise<unknown> {
  let value: unknown;
  try { value = await response.json(); } catch { throw new OperationsApiError("The local API returned unreadable data."); }
  if (!response.ok) {
    const detail = record(value) && typeof value.detail === "string" ? value.detail : undefined;
    throw new OperationsApiError(detail);
  }
  return value;
}

export async function fetchDailyStatus(fetcher: typeof fetch, base: string, signal: AbortSignal): Promise<DailyRunStatus> {
  const value = await payload(await fetcher(`${base}/operations/status`, { signal, headers: { Accept: "application/json" } }));
  if (!daily(value)) throw new OperationsApiError("The daily-run status response was invalid.");
  return value;
}

export async function runDailyNow(fetcher: typeof fetch, base: string): Promise<DailyRunResult> {
  const value = await payload(await fetcher(`${base}/operations/run-now`, { method: "POST", headers: { Accept: "application/json" } }));
  if (!run(value)) throw new OperationsApiError("The daily-run response was invalid.");
  return value;
}

export async function fetchSystemStatus(fetcher: typeof fetch, base: string, signal: AbortSignal): Promise<SystemStatus> {
  const value = await payload(await fetcher(`${base}/system/status`, { signal, headers: { Accept: "application/json" } }));
  if (!record(value) || !daily(value.daily) || !record(value.source) || !record(value.model) ||
      !record(value.resources) || typeof value.storage_bytes !== "number" || !Array.isArray(value.failures)) {
    throw new OperationsApiError("The System status response was invalid.");
  }
  return value as unknown as SystemStatus;
}
