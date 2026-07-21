import { describe, expect, it, vi } from "vitest";

import { fetchAnalysis, fetchModelStatus, fetchToday, generateAnalysis } from "./analysisApi";

const model = {
  runtime: "ollama",
  available: true,
  model: "qwen3:4b",
  model_installed: true,
  runtime_version: "fixture",
  active: false,
  size_vram_mb: 0,
  detail: "Ready on demand.",
};
const brief = {
  id: "analysis-1",
  work_id: "work-1",
  analysis_type: "fast_brief",
  status: "succeeded",
  cached: false,
  citation_coverage: 1,
  citations_verified: 1,
  duration_ms: 1200,
  error_code: null,
  safe_detail: null,
  output: {
    schema_version: "1.0",
    work_id: "work-1",
    change: "A bounded agent method was evaluated.",
    problem: "Agent reliability.",
    contribution: "A bounded loop.",
    evidence_state: "moderate",
    limitations: ["Limited evaluation."],
    code_state: "unknown",
    technical_relevance: "Agent systems.",
    commercial_relevance: "Unverified.",
    recommended_action: "read_source",
    claims: [{ text: "The method is bounded.", type: "fact", evidence_ids: ["ev-1"] }],
  },
  created_at: "2026-07-21T00:00:00Z",
};

function response(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), { status, headers: { "Content-Type": "application/json" } });
}

describe("local analysis API", () => {
  it("validates model and Today status", async () => {
    const fetcher = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(response(model))
      .mockResolvedValueOnce(response({ report_date: "2026-07-21", model, ranked: [{ work_id: "work-1", title: "Bounded Agents", technical_score: 75, brief }], generated_count: 1, remaining_fast_briefs: 9, remaining_deep_dives: 2 }));
    await expect(fetchModelStatus(fetcher, "/api", new AbortController().signal)).resolves.toMatchObject({ model: "qwen3:4b" });
    await expect(fetchToday(fetcher, "/api", new AbortController().signal)).resolves.toMatchObject({ generated_count: 1 });
  });

  it("generates and reads only typed citation-bearing analysis", async () => {
    const fetcher = vi.fn<typeof fetch>().mockImplementation(() => Promise.resolve(response(brief)));
    await expect(generateAnalysis(fetcher, "/api", "work-1", "brief")).resolves.toMatchObject({ citations_verified: 1 });
    await expect(fetchAnalysis(fetcher, "/api", "analysis-1", new AbortController().signal)).resolves.toMatchObject({ status: "succeeded" });
  });

  it("surfaces only safe API error details", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(response({ detail: "The configured daily local-analysis limit has been reached." }, 429));
    await expect(generateAnalysis(fetcher, "/api", "work-1", "brief")).rejects.toThrow("daily local-analysis limit");
  });
});
