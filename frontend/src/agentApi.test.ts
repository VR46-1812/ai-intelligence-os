import { describe, expect, it, vi } from "vitest";

import { fetchAgentGraph, fetchAgentStatus } from "./agentApi";

describe("agent API", () => {
  it("accepts the ordered graph", async () => {
    const fetcher = vi.fn(() => Promise.resolve(new Response(JSON.stringify([{ agent_id: "orchestrator", version: "1.0", order: 1, name: "Orchestrator Agent", responsibility: "Coordinate", model_assisted: false, prompt_version: null }]), { status: 200 }))) as unknown as typeof fetch;
    await expect(fetchAgentGraph(fetcher, "/api", new AbortController().signal)).resolves.toHaveLength(1);
  });

  it("accepts degraded source state", async () => {
    const fetcher = vi.fn(() => Promise.resolve(new Response(JSON.stringify({ pipeline_run_id: "run-1", current_agent: null, latest_success_at: "2026-07-22T00:00:00Z", executions: [{ id: "execution-1", agent_id: "orchestrator", stage_order: 1, status: "succeeded", attempt: 1, input: { report_date: "2026-07-22" }, output: { summary: "Completed" }, evidence_refs: [], provenance_refs: [], metrics: { duration_ms: 0.125 }, safe_failure_reason: null, started_at: "2026-07-22T00:00:00Z", completed_at: "2026-07-22T00:00:00Z", execution_mode: "deterministic", input_record_count: 0, output_record_count: 1, reused_from_run_id: null }], degraded_sources: ["openreview"] }), { status: 200 }))) as unknown as typeof fetch;
    const value = await fetchAgentStatus(fetcher, "/api", new AbortController().signal);
    expect(value.degraded_sources).toEqual(["openreview"]);
    expect(value.executions[0]?.output).toEqual({ summary: "Completed" });
    expect(value.executions[0]?.execution_mode).toBe("deterministic");
  });
});
