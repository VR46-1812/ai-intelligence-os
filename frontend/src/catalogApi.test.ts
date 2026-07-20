import { describe, expect, it, vi } from "vitest";

import {
  buildCatalogUrl,
  type CatalogPaper,
  type CatalogQuery,
  fetchCatalogPage,
  safeExternalUrl,
  syncLatestResearch,
} from "./catalogApi";

const signal = new AbortController().signal;
const query: CatalogQuery = {
  q: "agent memory",
  topic: "agentic-systems",
  source: "arxiv",
  publishedFrom: "2026-07-01",
  publishedTo: "2026-07-20",
  sort: "newest",
  limit: 5,
  offset: 0,
};
const paper: CatalogPaper = {
  id: "work-1",
  title: "Agent Memory",
  abstract: "A bounded local memory system.",
  publication_status: "preprint",
  published_at: "2026-07-20T00:00:00Z",
  updated_at: "2026-07-20T00:00:00Z",
  current_version: "v1",
  authors: [{ display_name: "Ada Lovelace", order: 1, orcid: null }],
  identities: [
    {
      id_type: "arxiv",
      value: "2607.00001",
      external_url: "https://arxiv.org/abs/2607.00001",
    },
  ],
  topics: [{ key: "agentic-systems", name: "Agentic Systems" }],
  source_key: "arxiv",
  source_name: "arXiv",
  external_url: "https://arxiv.org/abs/2607.00001",
  match_reason: "keyword match",
};

function jsonResponse(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("catalog API client", () => {
  it("builds encoded, bounded catalog query URLs", () => {
    const url = new URL(buildCatalogUrl("http://localhost/api", query));

    expect(url.pathname).toBe("/api/items");
    expect(url.searchParams.get("q")).toBe("agent memory");
    expect(url.searchParams.get("topic")).toBe("agentic-systems");
    expect(url.searchParams.get("limit")).toBe("5");
    expect(url.searchParams.get("offset")).toBe("0");
  });

  it("loads and validates a typed paper page", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(
      jsonResponse({ items: [paper], total: 1, limit: 5, offset: 0, has_more: false }),
    );

    const page = await fetchCatalogPage(fetcher, "/api", query, signal);

    expect(page.items[0]?.title).toBe("Agent Memory");
    expect(fetcher).toHaveBeenCalledOnce();
  });

  it("rejects malformed and failed responses without exposing response internals", async () => {
    const malformed = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse({ items: "bad" }));
    const failed = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse({ sql: "private" }, 500));

    await expect(fetchCatalogPage(malformed, "/api", query, signal)).rejects.toThrow(
      "catalog response was invalid",
    );
    await expect(fetchCatalogPage(failed, "/api", query, signal)).rejects.toThrow(
      "local research catalog",
    );
  });

  it("allows only canonical HTTPS research links", () => {
    expect(safeExternalUrl("https://arxiv.org/abs/2607.00001")).toBe(
      "https://arxiv.org/abs/2607.00001",
    );
    expect(safeExternalUrl("https://doi.org/10.1234/paper")).toBe(
      "https://doi.org/10.1234/paper",
    );
    expect(safeExternalUrl("javascript:alert(1)")).toBeNull();
    expect(safeExternalUrl("https://arxiv.org.evil.test/abs/1")).toBeNull();
  });

  it("starts only the bounded five-record discovery action", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(
      jsonResponse({
        ingestion: {
          status: "succeeded",
          records_seen: 5,
          records_created: 2,
          duplicate_records: 3,
        },
        records_normalized: 2,
        records_rejected: 0,
      }),
    );

    await expect(syncLatestResearch(fetcher, "/api")).resolves.toMatchObject({
      records_normalized: 2,
    });
    expect(fetcher).toHaveBeenCalledWith(
      "/api/sources/arxiv/sync",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ maximum_records: 5, lookback_hours: 168 }),
      }),
    );
  });
});
