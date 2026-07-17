import { describe, expect, it, vi } from "vitest";

import {
  INITIAL_API_HEALTH_STATE,
  resolveApiHealthState,
  type HealthFetcher,
} from "./apiHealth";

const signal = new AbortController().signal;

describe("API health state", () => {
  it("starts in the loading state", () => {
    expect(INITIAL_API_HEALTH_STATE).toBe("loading");
  });

  it("becomes healthy only for the expected successful contract", async () => {
    const fetcher = vi.fn<HealthFetcher>().mockResolvedValue({
      ok: true,
      json: vi.fn().mockResolvedValue({ service: "ai-intelligence-os", status: "ok" }),
    });

    await expect(resolveApiHealthState(fetcher, "/api/health", signal)).resolves.toBe("healthy");
    expect(fetcher).toHaveBeenCalledWith("/api/health", {
      headers: { Accept: "application/json" },
      signal,
    });
  });

  it.each([
    { service: "unexpected", status: "ok" },
    { service: "ai-intelligence-os", status: "failed" },
    null,
  ])("becomes unavailable for malformed payload %#", async (payload) => {
    const fetcher = vi.fn<HealthFetcher>().mockResolvedValue({
      ok: true,
      json: vi.fn().mockResolvedValue(payload),
    });

    await expect(resolveApiHealthState(fetcher, "/api/health", signal)).resolves.toBe(
      "unavailable",
    );
  });

  it("becomes unavailable when the API request fails", async () => {
    const fetcher = vi.fn<HealthFetcher>().mockRejectedValue(new TypeError("connection refused"));

    await expect(resolveApiHealthState(fetcher, "/api/health", signal)).resolves.toBe(
      "unavailable",
    );
  });
});
