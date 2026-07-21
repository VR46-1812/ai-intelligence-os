import { describe, expect, it, vi } from "vitest";

import { fetchDailyStatus, fetchSystemStatus, runDailyNow } from "./operationsApi";

const counts = { fetched: 5, normalized: 4, documents_processed: 2, documents_failed: 0,
  evidence_spans: 20, works_ranked: 5, briefs_generated: 1, briefs_cached: 0, files_cleaned: 1 };
const run = { run_id: "daily-1", status: "succeeded", trigger: "manual", counts,
  started_at: "2026-07-21T00:00:00Z", completed_at: "2026-07-21T00:02:00Z", safe_detail: null };
const daily = { scheduler_enabled: true, schedule: "06:00 Asia/Kolkata", running: false,
  current_run_id: null, latest_run: run, latest_success_at: "2026-07-21T00:02:00Z",
  next_run_at: "2026-07-22T00:30:00Z" };

function response(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });
}

describe("operations API", () => {
  it("validates daily status and manual run results", async () => {
    const fetcher = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(response(daily))
      .mockResolvedValueOnce(response(run));
    await expect(fetchDailyStatus(fetcher, "/api", new AbortController().signal)).resolves.toEqual(daily);
    await expect(runDailyNow(fetcher, "/api")).resolves.toEqual(run);
    expect(fetcher.mock.calls[1]?.[1]).toMatchObject({ method: "POST" });
  });

  it("rejects malformed and internal failure payloads safely", async () => {
    const malformed = vi.fn<typeof fetch>().mockResolvedValue(response({ running: "yes" }));
    const failed = vi.fn<typeof fetch>().mockResolvedValue(response({ detail: "Run already active" }, 409));
    await expect(fetchDailyStatus(malformed, "/api", new AbortController().signal)).rejects.toThrow("invalid");
    await expect(runDailyNow(failed, "/api")).rejects.toThrow("Run already active");
  });

  it("loads the typed System projection", async () => {
    const system = { daily, source: { source_key: "arxiv", health: "healthy", checkpoint: { position: "5" },
      last_attempt_at: null, last_success_at: null }, model: { runtime: "ollama", available: true,
      model: "qwen3:4b", model_installed: true, runtime_version: "1", active: false,
      size_vram_mb: 0, detail: "ready" }, resources: { non_llm_ram_mb: 2048,
      normal_total_ram_mb: 6144, temporary_peak_ram_mb: 8192, reserved_windows_ram_mb: 8192,
      vram_target_mb: 6656, download_concurrency: 3, generation_concurrency: 1,
      maximum_storage_gib: 100 }, storage_bytes: 10, failures: [] };
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(response(system));
    await expect(fetchSystemStatus(fetcher, "/api", new AbortController().signal)).resolves.toEqual(system);
  });
});
