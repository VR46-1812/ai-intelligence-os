import { expect, test } from "@playwright/test";

const counts = { fetched: 5, normalized: 5, documents_processed: 2, documents_failed: 0,
  evidence_spans: 42, works_ranked: 5, briefs_generated: 1, briefs_cached: 0,
  deep_dives_generated: 2, deep_dives_cached: 0, files_cleaned: 1,
  source_counts: { arxiv: 5, openreview: 2, huggingface: 3, "official-rss": 2 } };
const run = { run_id: "daily-smoke", status: "succeeded", trigger: "manual", counts,
  started_at: "2026-07-21T00:00:00Z", completed_at: "2026-07-21T00:02:00Z", safe_detail: null };
const daily = { scheduler_enabled: true, schedule: "06:00 Asia/Kolkata", running: false,
  current_run_id: null, latest_run: run, latest_success_at: "2026-07-21T00:02:00Z",
  next_run_at: "2026-07-22T00:30:00Z" };

test("System shows local operations and runs the bounded daily pipeline", async ({ page }) => {
  let runNowCalled = false;
  await page.route("**/system/status", (route) => route.fulfill({ json: {
    daily,
    source: { source_key: "arxiv", health: "healthy", checkpoint: { position: "5" },
      last_attempt_at: null, last_success_at: "2026-07-21T00:00:00Z" },
    model: { runtime: "ollama", available: true, model: "qwen3:4b", model_installed: true,
      runtime_version: "fixture", active: false, size_vram_mb: 0, detail: "Ready on demand." },
    resources: { non_llm_ram_mb: 2048, normal_total_ram_mb: 6144, temporary_peak_ram_mb: 8192,
      reserved_windows_ram_mb: 8192, vram_target_mb: 6656, download_concurrency: 3,
      generation_concurrency: 1, maximum_storage_gib: 100 }, storage_bytes: 1024, failures: [],
  } }));
  await page.route("**/operations/run-now", (route) => {
    runNowCalled = true;
    return route.fulfill({ json: run });
  });
  await page.goto("/#system");
  await expect(page.getByRole("heading", { name: "Everything running on this machine." })).toBeVisible();
  await expect(page.getByText("Unloaded / on demand")).toBeVisible();
  await expect(page.getByText("6656 MB VRAM target")).toBeVisible();
  await page.getByRole("button", { name: "Run Now" }).click();
  await expect.poll(() => runNowCalled).toBe(true);
});

test("System remains readable at mobile width", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.route("**/system/status", (route) => route.fulfill({ status: 503, json: { detail: "System status temporarily unavailable." } }));
  await page.goto("/#system");
  await expect(page.getByRole("alert")).toContainText("temporarily unavailable");
});
