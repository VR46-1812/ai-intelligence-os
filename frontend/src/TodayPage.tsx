import { useEffect, useState } from "react";

import type { ApiHealthState } from "./apiHealth";
import { AnalysisApiError, fetchToday, type TodayReport } from "./analysisApi";
import { fetchDailyStatus, OperationsApiError, runDailyNow, type DailyRunStatus } from "./operationsApi";

interface TodayPageProps {
  readonly apiBaseUrl: string;
  readonly healthState: ApiHealthState;
  readonly healthLabel: string;
}

export function TodayPage({ apiBaseUrl, healthState, healthLabel }: TodayPageProps) {
  const [report, setReport] = useState<TodayReport | null>(null);
  const [state, setState] = useState<"loading" | "ready" | "error" | "generating">("loading");
  const [error, setError] = useState("Today could not reach the local analysis service.");
  const [daily, setDaily] = useState<DailyRunStatus | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    void Promise.all([
      fetchToday(fetch, apiBaseUrl, controller.signal),
      fetchDailyStatus(fetch, apiBaseUrl, controller.signal),
    ])
      .then(([value, status]) => { setReport(value); setDaily(status); setState("ready"); })
      .catch((reason: unknown) => {
        if (controller.signal.aborted) return;
        setError(reason instanceof AnalysisApiError ? reason.message : "Today could not reach the local analysis service.");
        setState("error");
      });
    return () => controller.abort();
  }, [apiBaseUrl]);

  async function runNow() {
    setState("generating");
    try {
      const result = await runDailyNow(fetch, apiBaseUrl);
      if (result.status === "failed") {
        setError(result.safe_detail ?? "The daily pipeline failed safely. Open System to retry.");
        setState("error");
        setDaily(await fetchDailyStatus(fetch, apiBaseUrl, new AbortController().signal));
        return;
      }
      const controller = new AbortController();
      const [nextReport, nextStatus] = await Promise.all([
        fetchToday(fetch, apiBaseUrl, controller.signal),
        fetchDailyStatus(fetch, apiBaseUrl, controller.signal),
      ]);
      setReport(nextReport);
      setDaily(nextStatus);
      setState("ready");
    } catch (reason) {
      setError(reason instanceof OperationsApiError ? reason.message : "The daily pipeline failed safely.");
      setState("error");
    }
  }

  return (
    <main className="today-main">
      <section className="hero intelligence-hero" aria-labelledby="today-heading">
        <div>
          <p className="eyebrow cyan">Today / Evidence-grounded intelligence</p>
          <h2 id="today-heading">The strongest local research signals.</h2>
          <p className="hero-copy">Ranked papers and cached Scout briefs stay on this machine. Every factual brief claim must point to stored evidence.</p>
        </div>
        <button className="sync-button" type="button" disabled={state === "generating" || daily?.running || healthState !== "healthy"} onClick={() => void runNow()}>
          {state === "generating" ? "Running local pipeline…" : "Run Now"}
        </button>
      </section>

      {state === "loading" && <div className="analysis-banner" aria-live="polite">Checking Ollama and ranked papers…</div>}
      {state === "error" && <div className="analysis-banner error" role="alert">{error}</div>}
      {report && (
        <>
          {daily && <section className="run-strip" aria-label="Daily pipeline status"><div><span className={daily.running ? "model-orb" : "model-orb ready"} /><strong>{daily.running ? "Daily run in progress" : daily.latest_run ? `Latest run ${daily.latest_run.status}` : "Daily scheduler ready"}</strong></div><span>Latest success {daily.latest_success_at ? new Date(daily.latest_success_at).toLocaleString() : "not yet"}</span>{daily.latest_run && <span>{daily.latest_run.counts.fetched} fetched · {daily.latest_run.counts.documents_processed} documents · {daily.latest_run.counts.works_ranked} ranked · {daily.latest_run.counts.briefs_generated + daily.latest_run.counts.briefs_cached} briefs</span>}<a href="#system">System details →</a></section>}
          <section className="model-strip" aria-label="Local model status">
            <span className={report.model.available && report.model.model_installed ? "model-orb ready" : "model-orb"} />
            <div><strong>{report.model.model}</strong><small>{report.model.detail}</small></div>
            <span>{report.model.active ? "Loaded" : "On demand"}</span>
            <span>{report.remaining_fast_briefs} briefs · {report.remaining_deep_dives} deep dives remaining</span>
          </section>
          <section className="section-block" aria-labelledby="ranked-today-heading">
            <div className="section-heading"><div><p className="eyebrow">Ranked pipeline</p><h2 id="ranked-today-heading">Technical priority</h2></div><span className="phase-badge">{report.ranked.length} papers</span></div>
            <div className="today-list">
              {report.ranked.map((item, index) => (
                <article className="today-card" key={item.work_id}>
                  <span className="today-rank">{String(index + 1).padStart(2, "0")}</span>
                  <div>
                    <a href={`#explore/${encodeURIComponent(item.work_id)}`}>{item.title}</a>
                    {item.brief?.output && "change" in item.brief.output ? (
                      <><p>{item.brief.output.change}</p><div className="citation-summary">{Math.round(item.brief.citation_coverage * 100)}% citation coverage · {item.brief.citations_verified} verified references{item.brief.cached ? " · cached" : ""}</div></>
                    ) : <p className="muted-copy">No Scout brief yet. Generation remains user-triggered.</p>}
                  </div>
                  <strong>{item.technical_score?.toFixed(1) ?? "—"}</strong>
                </article>
              ))}
            </div>
          </section>
        </>
      )}
      {!report && state !== "loading" && <p className="muted-copy">{healthLabel}</p>}
    </main>
  );
}
