import { useEffect, useState } from "react";

import { fetchSystemStatus, OperationsApiError, runDailyNow, type SystemStatus } from "./operationsApi";

interface SystemPageProps { readonly apiBaseUrl: string; }

function time(value: string | null): string {
  if (!value) return "Not yet";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? "Unavailable" : parsed.toLocaleString();
}

export function SystemPage({ apiBaseUrl }: SystemPageProps) {
  const [value, setValue] = useState<SystemStatus | null>(null);
  const [state, setState] = useState<"loading" | "ready" | "running" | "error">("loading");
  const [error, setError] = useState<string | null>(null);
  const [reload, setReload] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    void fetchSystemStatus(fetch, apiBaseUrl, controller.signal).then((next) => {
      setValue(next); setState("ready");
    }).catch((reason: unknown) => {
      if (!controller.signal.aborted) {
        setError(reason instanceof OperationsApiError ? reason.message : "System status is unavailable.");
        setState("error");
      }
    });
    return () => controller.abort();
  }, [apiBaseUrl, reload]);

  async function runNow() {
    setState("running"); setError(null);
    try {
      const result = await runDailyNow(fetch, apiBaseUrl);
      if (result.status === "failed") setError(result.safe_detail ?? "The daily run failed safely.");
      setReload((current) => current + 1);
    } catch (reason) {
      setError(reason instanceof OperationsApiError ? reason.message : "The daily run could not start.");
      setState("error");
    }
  }

  return <main className="system-main">
    <section className="explore-intro"><div><p className="eyebrow cyan">System / Local operations</p><h2>Everything running on this machine.</h2><p>Source checkpoints, model residency, resource ceilings, and safe pipeline failures.</p></div><button className="sync-button" type="button" disabled={state === "running" || value?.daily.running} onClick={() => void runNow()}>{state === "running" ? "Running daily pipeline…" : "Run Now"}</button></section>
    {state === "loading" && <div className="analysis-banner" aria-live="polite">Reading local operational state…</div>}
    {error && <div className="analysis-banner error" role="alert">{error}</div>}
    {value && <>
      <section className="system-grid">
        <article><p className="eyebrow">Daily schedule</p><h3>{value.daily.running ? "Running" : "Ready"}</h3><p>{value.daily.schedule}</p><dl><div><dt>Latest success</dt><dd>{time(value.daily.latest_success_at)}</dd></div><div><dt>Next run</dt><dd>{time(value.daily.next_run_at)}</dd></div></dl></article>
        <article><p className="eyebrow">arXiv source</p><h3>{value.source.health}</h3><p>Checkpoint {value.source.checkpoint ? JSON.stringify(value.source.checkpoint) : "not established"}</p><dl><div><dt>Last success</dt><dd>{time(value.source.last_success_at)}</dd></div></dl></article>
        <article><p className="eyebrow">Local Scout</p><h3>{value.model.model}</h3><p>{value.model.detail}</p><dl><div><dt>Residency</dt><dd>{value.model.active ? `Loaded · ${value.model.size_vram_mb} MB` : "Unloaded / on demand"}</dd></div></dl></article>
        <article><p className="eyebrow">Storage</p><h3>{(value.storage_bytes / 1024 ** 3).toFixed(2)} GiB</h3><p>{value.resources.maximum_storage_gib} GiB retention ceiling</p><dl><div><dt>Downloads</dt><dd>{value.resources.download_concurrency} concurrent max</dd></div><div><dt>Generation</dt><dd>{value.resources.generation_concurrency} at a time</dd></div></dl></article>
      </section>
      <section className="section-block"><div className="section-heading"><div><p className="eyebrow">Resource governor</p><h2>Hard local ceilings</h2></div></div><div className="budget-row"><span>{value.resources.non_llm_ram_mb} MB app RAM</span><span>{value.resources.normal_total_ram_mb} MB normal total</span><span>{value.resources.temporary_peak_ram_mb} MB peak</span><span>{value.resources.reserved_windows_ram_mb} MB reserved for Windows</span><span>{value.resources.vram_target_mb} MB VRAM target</span></div></section>
      <section className="section-block"><div className="section-heading"><div><p className="eyebrow">Actionable history</p><h2>Pipeline failures</h2></div><span className="phase-badge">{value.failures.length}</span></div>{value.failures.length === 0 ? <p className="muted-copy">No retained pipeline or report failures.</p> : <div className="failure-list">{value.failures.map((failure) => <article key={`${failure.kind}-${failure.run_id}`}><strong>{failure.kind}</strong><span>{time(failure.occurred_at)}</span><p>{failure.safe_detail}</p></article>)}</div>}</section>
    </>}
  </main>;
}
