import { describe, expect, it, vi } from "vitest";

import { fetchLinkedEvents } from "./eventApi";

const signal = new AbortController().signal;

function response(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("linked-event API", () => {
  it("validates a paper linked to primary and official implementation evidence", async () => {
    const value = {
      items: [{
        id: "event-1", title: "Agent Memory", primary_work_id: "work-1",
        occurred_at: "2026-07-21T00:00:00Z", corroboration: 0.5,
        sources: [{ artifact_id: "repo-1", source_key: "github", artifact_type: "repository",
          title: "example/agent-memory", canonical_url: "https://github.com/example/agent-memory",
          relationship: "official_repository", content_class: "fact", authority: 0.85,
          freshness: 1, novelty: 1, published_at: null }],
      }], total: 1, limit: 20, offset: 0, has_more: false,
    };
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(response(value));
    await expect(fetchLinkedEvents(fetcher, "/api", signal)).resolves.toMatchObject({ total: 1 });
  });

  it("rejects malformed source labels", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(response({ items: [{}], total: 1, has_more: false }));
    await expect(fetchLinkedEvents(fetcher, "/api", signal)).rejects.toThrow("invalid");
  });

  it("reports source endpoint failures safely", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(response({ sql: "hidden" }, 500));
    await expect(fetchLinkedEvents(fetcher, "/api", signal)).rejects.toThrow("unavailable");
  });
});
