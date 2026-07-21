import { useEffect, useState } from "react";

import type { ApiHealthState } from "./apiHealth";
import { AnalysisApiError, fetchToday, generateToday, type TodayReport } from "./analysisApi";

interface TodayPageProps {
  readonly apiBaseUrl: string;
  readonly healthState: ApiHealthState;
  readonly healthLabel: string;
}

export function TodayPage({ apiBaseUrl, healthState, healthLabel }: TodayPageProps) {
  const [report, setReport] = useState<TodayReport | null>(null);
  const [state, setState] = useState<"loading" | "ready" | "error" | "generating">("loading");
  const [error, setError] = useState("Today could not reach the local analysis service.");

  useEffect(() => {
    const controller = new AbortController();
    void fetchToday(fetch, apiBaseUrl, controller.signal)
      .then((value) => { setReport(value); setState("ready"); })
      .catch((reason: unknown) => {
        if (controller.signal.aborted) return;
        setError(reason instanceof AnalysisApiError ? reason.message : "Today could not reach the local analysis service.");
        setState("error");
      });
    return () => controller.abort();
  }, [apiBaseUrl]);

  async function createBrief() {
    setState("generating");
    try {
      setReport(await generateToday(fetch, apiBaseUrl));
      setState("ready");
    } catch (reason) {
      setError(reason instanceof AnalysisApiError ? reason.message : "Scout generation failed safely.");
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
        <button className="sync-button" type="button" disabled={state === "generating" || healthState !== "healthy"} onClick={() => void createBrief()}>
          {state === "generating" ? "Scout is analyzing…" : "Generate top brief"}
        </button>
      </section>

      {state === "loading" && <div className="analysis-banner" aria-live="polite">Checking Ollama and ranked papers…</div>}
      {state === "error" && <div className="analysis-banner error" role="alert">{error}</div>}
      {report && (
        <>
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
