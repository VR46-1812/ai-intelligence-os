import { expect, test, type Page } from "@playwright/test";

const report = {
  schema_version: "1.0", report_date: "2026-07-22",
  pipeline: { discovered: 5, normalized: 5, filtered: 5, shortlisted: 3, briefed: 1, analyzed: 1, failed: 0, run_id: "daily-1" },
  top_technical: [{ work_id: "work-1", title: "Bounded Agents", score: 82, reason: "Primary evidence documents a bounded method.", status: "briefed", model_signal: { novelty: .8, method_depth: .7, impact: .8, opportunity: .6, confidence: .84, evidence_ids: ["ev-1"], fallback: false } }],
  top_commercial: [], deep_dives: ["deep-1"], important_updates: [{ summary: "A new repository release was associated." }], learning_focus: ["Evaluation"], coverage_gaps: [],
  executive_briefing: "A bounded agent method leads today's research. Its official repository adds implementation context. Production performance remains unverified.",
  what_happened: ["A bounded agent method was published."], why_it_matters: ["It improves local reliability."], evidence_versus_interpretation: [], research_and_product_launches: [], community_signals: [],
  learning_plan: [{ topic: "Agent evaluation", why_it_matters: "Reliable evaluation prevents unsupported automation.", prerequisites: ["Python"], estimated_minutes: 35, recommended_item: "Bounded Agents paper", exercise: "Reproduce one cited check.", expected_outcome: "A repeatable local evaluation.", evidence_ids: ["ev-1"] }],
  what_to_build: [{ work_id: "work-1", prototype: "Citation verifier", user_problem: "Teams cannot audit generated claims quickly.", architecture: ["Evidence store", "Verifier"], estimated_effort: "Two days", recommended_resource: "Official repository", validation_test: "Reject one unsupported claim.", project_relevance: ["BidReady"], evidence_ids: ["ev-1"] }],
  commercial_hypotheses: [{ label: "commercial_hypothesis", work_id: "work-1", title: "Bounded Agents", problem: "Manual claim validation is slow.", target_buyer: "Indian AI product teams", proposed_offer: "A fixed-scope validation pilot", supporting_evidence: ["ev-1"], prototype: "Citation verifier", effort: "Two days", validation_experiment: "Ask three buyers for a paid pilot within 48 hours.", pricing_hypothesis: "INR 50,000 pilot", competition: "Internal review", risks: ["Urgency is unknown."], assumptions: ["Teams own evidence."], india_market_relevance: "Fixed-scope procurement friendly.", project_relevance: ["BidReady"], confidence: .68 }],
  risks_and_unknowns: [], watchlist_changes: ["Watch repository releases."], source_coverage: [{ source_key: "arxiv", records: 1, status: "healthy" }, { source_key: "github", records: 1, status: "healthy" }],
};

const daily = { scheduler_enabled: true, schedule: "06:00 Asia/Kolkata", running: false, current_run_id: null, latest_run: { run_id: "daily-1", status: "succeeded", trigger: "manual", counts: { fetched: 5, normalized: 5, documents_processed: 1, documents_failed: 0, evidence_spans: 4, works_ranked: 5, briefs_generated: 1, briefs_cached: 0, deep_dives_generated: 1, deep_dives_cached: 0, files_cleaned: 0, source_counts: { arxiv: 1, github: 1 } }, started_at: "2026-07-22T00:00:00Z", completed_at: "2026-07-22T00:01:00Z", safe_detail: null }, latest_success_at: "2026-07-22T00:01:00Z", next_run_at: null };
const today = { report_date: "2026-07-22", model: { runtime: "ollama", available: true, model: "qwen3:4b", model_installed: true, runtime_version: "fixture", active: false, size_vram_mb: 0, detail: "Ready on demand." }, ranked: [{ work_id: "work-1", title: "Bounded Agents", technical_score: 82, brief: null }], generated_count: 0, remaining_fast_briefs: 9, remaining_deep_dives: 1 };
const events = { items: [{ id: "event-1", title: "Bounded Agents", primary_work_id: "work-1", occurred_at: "2026-07-22T00:00:00Z", corroboration: 1, source_count: 2, classification: "corroborated_event", corroboration_status: "corroborated", association_confidence: .86, linkage_reason: "Paper identity and official repository metadata agree.", sources: [{ artifact_id: "paper-1", source_key: "arxiv", source_type: "paper", artifact_type: "paper", title: "Paper", canonical_url: "https://arxiv.org/abs/1234.5678", relationship: "primary_research", content_class: "fact", authority: .95, freshness: .9, novelty: .8, published_at: null }, { artifact_id: "repo-1", source_key: "github", source_type: "repository", artifact_type: "repository", title: "Repository", canonical_url: "https://github.com/example/project", relationship: "official_repository", content_class: "fact", authority: .85, freshness: .9, novelty: .7, published_at: null }] }], total: 1, has_more: false };

async function mockDaily(page: Page) {
  await page.route("**/operations/status", (route) => route.fulfill({ json: daily }));
  await page.route("**/reports/daily/complete", (route) => route.fulfill({ json: report }));
  await page.route("**/reports/today", (route) => route.fulfill({ json: today }));
  await page.route("**/events?**", (route) => route.fulfill({ json: events }));
}

test("Daily Brief puts useful intelligence and semantic source counts in the first viewport", async ({ page }) => {
  await mockDaily(page); await page.setViewportSize({ width: 1366, height: 768 }); await page.goto("/#daily");
  await expect(page.getByRole("heading", { name: "What deserves attention" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Developments" })).toBeVisible();
  await expect(page.getByText("2 independent sources")).toBeVisible();
  await expect(page.getByText("0% corroboration")).toHaveCount(0);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
});

test("Daily Brief tabs expose complete Learn, Build, Earn, and Sources output", async ({ page }) => {
  await mockDaily(page); await page.goto("/#daily");
  await page.getByRole("tab", { name: "Learn" }).click(); await expect(page.getByText("35 min")).toBeVisible(); await expect(page.getByText("Expected outcome")).toBeVisible();
  await page.getByRole("tab", { name: "Build" }).click(); await expect(page.getByText("Citation verifier")).toBeVisible(); await expect(page.getByText("Validation test")).toBeVisible();
  await page.getByRole("tab", { name: "Earn", exact: true }).click(); await expect(page.getByText(/Commercial hypothesis/)).toBeVisible(); await expect(page.getByText("INR 50,000 pilot")).toBeVisible();
  await page.getByRole("tab", { name: "Sources" }).click(); await expect(page.getByText("corroborated event")).toBeVisible();
});

test("Daily Brief has clear empty and degraded states", async ({ page }) => {
  await page.route("**/operations/status", (route) => route.fulfill({ status: 503, json: { detail: "Pipeline status unavailable." } }));
  await page.route("**/reports/daily/complete", (route) => route.fulfill({ status: 409, json: { detail: "Run the daily pipeline first." } }));
  await page.route("**/reports/today", (route) => route.fulfill({ json: today }));
  await page.route("**/events?**", (route) => route.fulfill({ json: { items: [], total: 0, has_more: false } }));
  await page.goto("/#daily"); await expect(page.getByRole("heading", { name: "No published daily brief" })).toBeVisible(); await expect(page.getByRole("alert")).toContainText("Run the daily pipeline first.");
});

test("a one-source artifact is labelled Single source, never corroborated", async ({ page }) => {
  await mockDaily(page);
  const primary = events.items[0]!;
  await page.route("**/events?**", (route) => route.fulfill({ json: { items: [{ ...primary, source_count: 1, classification: "artifact", corroboration_status: "single_source", association_confidence: .95, linkage_reason: "Only the primary research record is available.", sources: primary.sources.slice(0, 1) }], total: 1, has_more: false } }));
  await page.goto("/#daily");
  await expect(page.getByText("Single source")).toBeVisible();
  await page.getByRole("tab", { name: "Sources" }).click();
  await expect(page.getByText("artifact", { exact: true })).toBeVisible();
});
