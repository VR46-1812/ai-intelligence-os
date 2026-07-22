export interface AgentSpec {
  readonly agent_id: string;
  readonly version: string;
  readonly order: number;
  readonly name: string;
  readonly responsibility: string;
  readonly model_assisted: boolean;
  readonly prompt_version: string | null;
}

export interface AgentExecution {
  readonly id: string;
  readonly agent_id: string;
  readonly stage_order: number;
  readonly status: "queued" | "running" | "succeeded" | "failed" | "skipped";
  readonly attempt: number;
  readonly input: Readonly<Record<string, unknown>>;
  readonly output: Readonly<Record<string, unknown>> | null;
  readonly evidence_refs: readonly string[];
  readonly provenance_refs: readonly string[];
  readonly metrics: Readonly<Record<string, number>>;
  readonly safe_failure_reason: string | null;
  readonly started_at: string | null;
  readonly completed_at: string | null;
}

export interface AgentRunView {
  readonly pipeline_run_id: string | null;
  readonly current_agent: string | null;
  readonly latest_success_at: string | null;
  readonly executions: readonly AgentExecution[];
  readonly degraded_sources: readonly string[];
}

export class AgentApiError extends Error {}

function record(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

async function body(response: Response): Promise<unknown> {
  let value: unknown;
  try { value = await response.json(); } catch { throw new AgentApiError("Agent status was unreadable."); }
  if (!response.ok) throw new AgentApiError(record(value) && typeof value.detail === "string" ? value.detail : "Agent status is unavailable.");
  return value;
}

export async function fetchAgentGraph(fetcher: typeof fetch, base: string, signal: AbortSignal): Promise<readonly AgentSpec[]> {
  const value = await body(await fetcher(`${base}/agents/graph`, { signal, headers: { Accept: "application/json" } }));
  if (!Array.isArray(value) || !value.every((item) => record(item) && typeof item.agent_id === "string" && typeof item.order === "number" && typeof item.name === "string")) {
    throw new AgentApiError("Agent graph response was invalid.");
  }
  return value as AgentSpec[];
}

export async function fetchAgentStatus(fetcher: typeof fetch, base: string, signal: AbortSignal): Promise<AgentRunView> {
  const value = await body(await fetcher(`${base}/agents/status`, { signal, headers: { Accept: "application/json" } }));
  if (!record(value) || !Array.isArray(value.executions) || !Array.isArray(value.degraded_sources) || !value.executions.every((item) => record(item) && typeof item.agent_id === "string" && typeof item.status === "string" && record(item.input) && (item.output === null || record(item.output)))) throw new AgentApiError("Agent execution response was invalid.");
  return value as unknown as AgentRunView;
}

export async function retryAgents(fetcher: typeof fetch, base: string): Promise<void> {
  await body(await fetcher(`${base}/agents/retry`, { method: "POST", headers: { Accept: "application/json" } }));
}
