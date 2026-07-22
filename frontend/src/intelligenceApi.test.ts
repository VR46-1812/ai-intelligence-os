import { describe, expect, it, vi } from "vitest";
import { fetchOpportunities, fetchTopics, IntelligenceApiError } from "./intelligenceApi";

describe("intelligence output API", () => {
  it("accepts ranked topics and evidence-backed opportunities", async () => {
    const topicsFetch = vi.fn<typeof fetch>().mockResolvedValue(new Response(JSON.stringify([
      { key: "agents", label: "Agents", paper_count: 2, daily_change: 1,
        papers: [{ work_id: "work", title: "Bounded agents", score: 78.2 }] },
    ]), { status: 200 }));
    const opportunityFetch = vi.fn<typeof fetch>().mockResolvedValue(new Response(JSON.stringify([
      { kind: "engineering", work_id: "work", title: "Bounded agents", headline: "Guarded loop",
        label: "build_opportunity", detail: "Reduce tool errors", evidence_ids: ["ev-1"], confidence: 0.8,
        target_customer: null, painful_workflow: null, proposed_offer: null, prototype: "Guarded loop",
        effort: null, provisional_pricing: null, validation_experiment: "Fixture test",
        assumptions: [], risks: ["Transfer risk"], india_market_relevance: null, project_relevance: [] },
    ]), { status: 200 }));

    await expect(fetchTopics(topicsFetch, "/api", new AbortController().signal)).resolves.toHaveLength(1);
    await expect(fetchOpportunities(opportunityFetch, "/api", new AbortController().signal)).resolves.toHaveLength(1);
  });

  it("rejects unverified opportunity response shapes", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(new Response(JSON.stringify([
      { kind: "commercial", work_id: "work", title: "Paper", headline: "Idea", detail: "Pilot", evidence_ids: [], confidence: "high" },
    ]), { status: 200 }));
    await expect(fetchOpportunities(fetcher, "/api", new AbortController().signal)).rejects.toBeInstanceOf(IntelligenceApiError);
  });
});
