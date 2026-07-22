import { expect, test } from "@playwright/test";

const model = { runtime: "ollama", available: true, model: "qwen3:4b", model_installed: true, runtime_version: "fixture", active: false, size_vram_mb: 0, detail: "Scout model is installed and ready on demand." };

function deepDive(workId: string) {
  return {
    id: "analysis-deep-1", work_id: workId, analysis_type: "deep_dive", status: "succeeded", cached: false,
    citation_coverage: 1, citations_verified: 2, duration_ms: 4230, error_code: null, safe_detail: null,
    created_at: "2026-07-21T00:00:00Z",
    output: {
      schema_version: "1.0", work_id: workId, title: "Verified local research report", publication_status: "preprint",
      executive_significance: { markdown: "The stored evidence supports a bounded technical finding.", confidence: 0.82, claim_ids: ["claim-1"] },
      problem_context: { markdown: "The paper studies a documented reliability problem.", confidence: 0.8, claim_ids: ["claim-1"] },
      method: { markdown: "The method is described only to the extent supported by the cited page.", confidence: 0.78, claim_ids: ["claim-1"] },
      evaluation: { markdown: "Evaluation evidence remains limited.", confidence: 0.65, claim_ids: ["claim-2"] },
      limitations: { markdown: "Broader production validity is unknown.", confidence: 0.9, claim_ids: ["claim-2"] },
      reproducibility: { status: "unknown", repository_urls: [], assets: [], hardware_fit: "unknown", steps: [], risks: ["No repository evidence."] },
      production_applications: [], commercial_hypotheses: [], learning_path: [],
      skeptic_findings: [{ severity: "warning", finding: "External validation is absent.", affected_claim_ids: ["claim-2"], resolution: "qualified" }],
      claims: [
        { id: "claim-1", text: "The evidence describes the method.", type: "fact", importance: "major", verification_status: "supported", evidence_ids: ["evidence-00000001"], qualifier: null },
        { id: "claim-2", text: "Production performance is not established.", type: "interpretation", importance: "major", verification_status: "supported", evidence_ids: ["evidence-00000002"], qualifier: "Limited to supplied evidence." },
      ],
    },
  };
}

test("Today shows local model state and a citation-verified brief", async ({ page }) => {
  await page.route("**/events?**", (route) => route.fulfill({ json: { items: [{ id: "event-1", title: "Bounded Agents released", primary_work_id: "work-1", occurred_at: "2026-07-21T00:00:00Z", corroboration: 0.5, sources: [{ artifact_id: "repo-1", source_key: "github", artifact_type: "repository", title: "Official repository", canonical_url: "https://github.com/example/bounded-agents", relationship: "official_repository", content_class: "fact", authority: 0.85, freshness: 1, novelty: 1, published_at: null }] }], total: 1, limit: 20, offset: 0, has_more: false } }));
  await page.route("**/models/scout/status", (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(model) }));
  await page.route("**/reports/today", (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ report_date: "2026-07-21", model, ranked: [{ work_id: "work-1", title: "Bounded Agents", technical_score: 78.4, brief: { ...deepDive("work-1"), analysis_type: "fast_brief", output: { schema_version: "1.0", work_id: "work-1", change: "A bounded method was evaluated.", problem: "Reliability", contribution: "A bounded method", evidence_state: "moderate", limitations: [], code_state: "unknown", technical_relevance: "Relevant", commercial_relevance: "Unverified", recommended_action: "read_source", claims: [{ text: "A method was evaluated.", type: "fact", evidence_ids: ["evidence-00000001"] }] } } }], generated_count: 1, remaining_fast_briefs: 9, remaining_deep_dives: 2 }) }));
  await page.route("**/reports/daily/complete", (route) => route.fulfill({ json: {
    schema_version: "1.0", report_date: "2026-07-21",
    pipeline: { discovered: 5, normalized: 5, filtered: 5, shortlisted: 5, briefed: 1, analyzed: 2, failed: 0, run_id: "daily-1" },
    top_technical: [], top_commercial: [], deep_dives: ["analysis-deep-1"],
    important_updates: [], learning_focus: ["Citation verification"], coverage_gaps: [],
  } }));
  await page.goto("/#today");
  await expect(page.getByText("Scout · qwen3:4b")).toBeVisible();
  await expect(page.getByText("100% citation coverage")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Published intelligence" })).toBeVisible();
  await expect(page.getByRole("link", { name: /Open verified deep dive/ })).toBeVisible();
  await expect(page.getByRole("heading", { name: "What happened" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Commercial opportunities" })).toBeVisible();
  await expect(page.getByText("github · 50% corroboration")).toBeVisible();
});

test("Today shows a clear empty state before the first final report", async ({ page }) => {
  await page.route("**/reports/today", (route) => route.fulfill({ json: {
    report_date: "2026-07-21", model, ranked: [], generated_count: 0,
    remaining_fast_briefs: 10, remaining_deep_dives: 2,
  } }));
  await page.route("**/reports/daily/complete", (route) => route.fulfill({ status: 409, json: { detail: "Run the daily pipeline first." } }));
  await page.goto("/#today");
  await expect(page.getByRole("heading", { name: "No final daily report yet" })).toBeVisible();
  await expect(page.getByText("Run the daily pipeline first.")).toBeVisible();
});

test("Explore deep-dive action opens a verified report with citations", async ({ page }) => {
  let report = deepDive("work-placeholder");
  await page.route("**/items/*/deep-dive", async (route) => {
    const workId = decodeURIComponent(new URL(route.request().url()).pathname.split("/")[2] ?? "work-placeholder");
    report = deepDive(workId);
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(report) });
  });
  await page.route("**/analyses/analysis-deep-1", (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(report) }));
  await page.route("**/events?**", (route) => route.fulfill({ json: { items: [{ id: "event-1", title: "Linked report", primary_work_id: report.work_id, occurred_at: null, corroboration: 0.5, sources: [{ artifact_id: "model-1", source_key: "huggingface", artifact_type: "model", title: "Official model", canonical_url: "https://huggingface.co/example/model", relationship: "official_model", content_class: "fact", authority: 0.85, freshness: 1, novelty: 1, published_at: null }] }], total: 1, limit: 20, offset: 0, has_more: false } }));
  await page.goto("/#explore");
  await page.locator(".paper-title").first().click();
  await page.getByRole("button", { name: "Run deep dive" }).click();
  await page.getByRole("link", { name: "Open verified deep dive" }).click();
  await expect(page.getByRole("heading", { name: "Verified local research report" })).toBeVisible();
  await expect(page.getByText("100% citation coverage")).toBeVisible();
  await expect(page.getByRole("complementary", { name: "Verified claims" })).toContainText("Evidence 00000001");
  await expect(page.getByRole("complementary", { name: "Verified claims" })).toContainText("Linked-source evidence");
});
