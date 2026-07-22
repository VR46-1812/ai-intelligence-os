import { useEffect, useState } from "react";

import { fetchAgentGraph, fetchAgentStatus, retryAgents, type AgentRunView, type AgentSpec } from "./agentApi";
import { fetchSystemStatus, OperationsApiError, runDailyNow, type SystemStatus } from "./operationsApi";

function time(value: string | null): string {
  if (!value) return "Not yet";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? "Unavailable" : parsed.toLocaleString();
}
function duration(value: number | undefined): string {
  if (value === undefined) return "Not measured";
  if (value < 1) return `${value.toFixed(3)} ms`;
  return `${value.toFixed(1)} ms`;
}

export function SystemPage({ apiBaseUrl }: { readonly apiBaseUrl: string }) {
  const [value, setValue] = useState<SystemStatus | null>(null);
  const [state, setState] = useState<"loading" | "ready" | "running" | "error">("loading");
  const [error, setError] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  const [agentGraph, setAgentGraph] = useState<readonly AgentSpec[]>([]);
  const [agents, setAgents] = useState<AgentRunView | null>(null);
  const [agentError, setAgentError] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    void fetchSystemStatus(fetch, apiBaseUrl, controller.signal).then((next) => { setValue(next); setState("ready"); }).catch((reason: unknown) => { if (!controller.signal.aborted) { setError(reason instanceof OperationsApiError ? reason.message : "Operations status is unavailable."); setState("error"); } });
    void Promise.all([fetchAgentGraph(fetch, apiBaseUrl, controller.signal), fetchAgentStatus(fetch, apiBaseUrl, controller.signal)]).then(([graph, status]) => { setAgentGraph(graph); setAgents(status); setAgentError(null); }).catch(() => { if (!controller.signal.aborted) setAgentError("Agent execution history is unavailable."); });
    return () => controller.abort();
  }, [apiBaseUrl, reload]);

  async function runNow() { setState("running"); setError(null); try { const result = await runDailyNow(fetch, apiBaseUrl); if (result.status === "failed") setError(result.safe_detail ?? "The daily run failed safely."); setReload((current) => current + 1); } catch (reason) { setError(reason instanceof OperationsApiError ? reason.message : "The daily run could not start."); setState("error"); } }
  async function retryFailedAgent() { setState("running"); setAgentError(null); try { await retryAgents(fetch, apiBaseUrl); setReload((current) => current + 1); } catch { setAgentError("The failed agent could not be retried safely."); setState("error"); } }

  return <main className="settings-page page-frame">
    <header className="page-header compact"><div><p className="eyebrow">Settings / Operations</p><h1>Local operations</h1><p>Detailed source, model, resource, failure, and agent state stays here—not in the daily reading flow.</p></div><button className="primary-action" type="button" disabled={state === "running" || value?.daily.running} onClick={() => void runNow()}>{state === "running" ? "Running…" : "Run daily pipeline"}</button></header>
    {state === "loading" && <div className="state-banner" role="status">Reading local operational state…</div>}{error && <div className="state-banner error" role="alert">{error}</div>}
    {value && <>
      <section className="operations-grid"><article><span>Daily schedule</span><strong>{value.daily.running ? "Running" : "Ready"}</strong><p>{value.daily.schedule}</p><small>Latest success {time(value.daily.latest_success_at)}</small></article><article><span>arXiv source</span><strong>{value.source.health}</strong><p>Last success {time(value.source.last_success_at)}</p></article><article><span>Local Scout</span><strong>{value.model.model}</strong><p>{value.model.active ? `Loaded · ${value.model.size_vram_mb} MB VRAM` : "Unloaded · on demand"}</p></article><article><span>Storage</span><strong>{(value.storage_bytes / 1024 ** 3).toFixed(2)} GiB</strong><p>{value.resources.maximum_storage_gib} GiB ceiling</p></article></section>
      <details className="settings-section"><summary>Resource budgets</summary><div className="budget-row"><span>{value.resources.non_llm_ram_mb} MB app RAM</span><span>{value.resources.normal_total_ram_mb} MB normal total</span><span>{value.resources.temporary_peak_ram_mb} MB peak</span><span>{value.resources.reserved_windows_ram_mb} MB Windows reserve</span><span>{value.resources.vram_target_mb} MB VRAM target</span></div></details>
      <section className="settings-section"><div className="section-title"><div><p className="eyebrow">Sequential runtime</p><h2>Agent graph</h2></div><span>{agentGraph.length} agents</span></div>{agentError && <div className="state-banner error" role="alert">{agentError}</div>}{agents?.degraded_sources.length ? <div className="degraded-banner"><strong>Degraded sources</strong><span>{agents.degraded_sources.join(" · ")}</span></div> : <p className="quiet-state">No enabled source is degraded.</p>}<div className="agent-graph">{agentGraph.map((agent) => { const execution = agents?.executions.find((item) => item.agent_id === agent.agent_id); return <article key={agent.agent_id} className={`agent-node ${execution?.status ?? "queued"}`}><span className="agent-order">{String(agent.order).padStart(2, "0")}</span><div><header><strong>{agent.name}</strong><span className={`mode-badge ${execution?.execution_mode ?? "queued"}`}>{execution?.execution_mode ?? "not run"}</span></header><p>{execution?.output && typeof execution.output.summary === "string" ? execution.output.summary : agent.responsibility}</p>{execution && <div className="agent-metrics"><span>{duration(execution.metrics.duration_ms)}</span><span>{execution.input_record_count} in</span><span>{execution.output_record_count} out</span><span>{execution.evidence_refs.length} evidence</span><span>{execution.provenance_refs.length} provenance</span></div>}{execution?.reused_from_run_id && <small>Reused from the current persisted run checkpoint.</small>}{execution?.safe_failure_reason && <p className="agent-failure">{execution.safe_failure_reason}</p>}<details><summary>Technical details</summary><p>Attempt {execution?.attempt ?? 0} · {execution?.status ?? "queued"}</p></details></div></article>; })}</div>{agents?.executions.some((item) => item.status === "failed") && <button className="primary-action" type="button" disabled={state === "running"} onClick={() => void retryFailedAgent()}>Retry failed stage</button>}<p className="quiet-state">Latest successful checkpoint {time(agents?.latest_success_at ?? null)}.</p></section>
      <details className="settings-section"><summary>Pipeline failures ({value.failures.length})</summary>{value.failures.length === 0 ? <p className="quiet-state">No retained pipeline or report failures.</p> : <div className="failure-list">{value.failures.map((failure) => <article key={`${failure.kind}-${failure.run_id}`}><strong>{failure.kind}</strong><span>{time(failure.occurred_at)}</span><p>{failure.safe_detail}</p></article>)}</div>}</details>
    </>}
  </main>;
}
