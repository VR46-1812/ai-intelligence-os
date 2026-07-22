import { useEffect, useState } from "react";

import type { ApiHealthState } from "./apiHealth";
import { AnalysisApiError, fetchCompleteDailyReport, fetchToday, type DailyIntelligenceReport, type TodayReport } from "./analysisApi";
import { fetchDailyStatus, OperationsApiError, runDailyNow, type DailyRunStatus } from "./operationsApi";
import { fetchLinkedEvents, type LinkedEvent } from "./eventApi";

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
  const [complete, setComplete] = useState<DailyIntelligenceReport | null>(null);
  const [completeError, setCompleteError] = useState<string | null>(null);
  const [events, setEvents] = useState<readonly LinkedEvent[]>([]);
  const [eventsError, setEventsError] = useState<string | null>(null);

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
    void fetchCompleteDailyReport(fetch, apiBaseUrl, controller.signal)
      .then((value) => { setComplete(value); setCompleteError(null); })
      .catch((reason: unknown) => {
        if (!controller.signal.aborted) setCompleteError(reason instanceof AnalysisApiError ? reason.message : "No final daily report is available yet.");
      });
    void fetchLinkedEvents(fetch, apiBaseUrl, controller.signal)
      .then((value) => { setEvents(value.items); setEventsError(null); })
      .catch(() => { if (!controller.signal.aborted) setEventsError("No linked-source events are available yet."); });
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
      setComplete(await fetchCompleteDailyReport(fetch, apiBaseUrl, controller.signal));
      setEvents((await fetchLinkedEvents(fetch, apiBaseUrl, controller.signal)).items);
      setCompleteError(null);
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
          {daily && <section className="run-strip" aria-label="Daily pipeline status"><div><span className={daily.running ? "model-orb" : "model-orb ready"} /><strong>{daily.running ? "Daily run in progress" : daily.latest_run ? `Latest run ${daily.latest_run.status}` : "Daily scheduler ready"}</strong></div><span>Latest success {daily.latest_success_at ? new Date(daily.latest_success_at).toLocaleString() : "not yet"}</span>{daily.latest_run && <span>{daily.latest_run.counts.fetched} fetched · {daily.latest_run.counts.documents_processed} documents · {daily.latest_run.counts.works_ranked} ranked · {daily.latest_run.counts.briefs_generated + daily.latest_run.counts.briefs_cached} briefs · {daily.latest_run.counts.deep_dives_generated + daily.latest_run.counts.deep_dives_cached} deep dives</span>}<a href="#system">System details →</a></section>}
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
          <section className="section-block" aria-labelledby="daily-report-heading">
            <div className="section-heading"><div><p className="eyebrow">Final daily report</p><h2 id="daily-report-heading">Published intelligence</h2></div>{complete && <span className="phase-badge">{complete.report_date}</span>}</div>
            {complete ? <div className="daily-output-grid"><article className="intelligence-card"><h3>Pipeline</h3><p>{complete.pipeline.discovered} discovered · {complete.pipeline.filtered} ranked · {complete.pipeline.briefed} briefed · {complete.pipeline.analyzed} analyzed · {complete.pipeline.failed} failed</p>{complete.deep_dives.length ? <div className="deep-link-list">{complete.deep_dives.map((id) => <a key={id} href={`#report/${encodeURIComponent(id)}`}>Open verified deep dive {id.slice(-8)} →</a>)}</div> : <p className="muted-copy">No verified deep dive was published in this report.</p>}</article><article className="intelligence-card"><h3>Learning focus</h3>{complete.learning_focus.length ? <ul>{complete.learning_focus.map((item) => <li key={item}>{item}</li>)}</ul> : <p className="muted-copy">No learning focus was extracted.</p>}{complete.coverage_gaps.length > 0 && <div className="coverage-warning"><strong>Coverage gaps</strong>{complete.coverage_gaps.map((gap) => <p key={gap}>{gap}</p>)}</div>}</article></div> : <div className="state-panel"><h3>No final daily report yet</h3><p>{completeError ?? "Run the bounded local pipeline to publish today’s report."}</p></div>}
          </section>
          <section className="section-block" aria-labelledby="intelligence-sections-heading">
            <div className="section-heading"><div><p className="eyebrow">Multi-source synthesis</p><h2 id="intelligence-sections-heading">From change to action</h2></div><span className="phase-badge">{events.length} linked events</span></div>
            <div className="intelligence-lens-grid">
              <article><h3>What Happened</h3>{events.length ? events.slice(0, 5).map((event) => <p key={event.id}><a href={event.primary_work_id ? `#explore/${encodeURIComponent(event.primary_work_id)}` : "#explore"}>{event.title}</a><span>{event.sources.map((source) => source.source_key).join(" + ")} · {Math.round(event.corroboration * 100)}% corroboration</span></p>) : <p className="muted-copy">{eventsError ?? "Run discovery to build linked developments."}</p>}</article>
              <article><h3>Why it matters</h3>{complete?.top_technical.length ? complete.top_technical.slice(0, 3).map((item) => <p key={item.work_id}><strong>{item.title}</strong><span>{item.reason}</span></p>) : <p className="muted-copy">No ranked technical development is published yet.</p>}</article>
              <article><h3>Learn</h3>{complete?.learning_focus.length ? complete.learning_focus.map((item) => <p key={item}>{item}</p>) : <p className="muted-copy">No verified learning path is available yet.</p>}</article>
              <article><h3>Build</h3>{complete?.top_technical.length ? complete.top_technical.slice(0, 3).map((item) => <p key={item.work_id}><strong>{item.title}</strong><span>Interpretation · validate against linked primary evidence before implementation.</span></p>) : <p className="muted-copy">No evidence-backed build direction is available yet.</p>}</article>
              <article><h3>Earn</h3>{complete?.top_commercial.length ? complete.top_commercial.slice(0, 3).map((item) => <p key={item.work_id}><strong>{item.title}</strong><span>Commercial hypothesis · {item.reason}</span></p>) : <p className="muted-copy">No commercial hypothesis is published yet.</p>}</article>
            </div>
          </section>
        </>
      )}
      {!report && state !== "loading" && <p className="muted-copy">{healthLabel}</p>}
    </main>
  );
}
