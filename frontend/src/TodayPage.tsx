import { useEffect, useMemo, useState } from "react";

import type { ApiHealthState } from "./apiHealth";
import { AnalysisApiError, fetchCompleteDailyReport, fetchToday, type DailyIntelligenceReport, type TodayReport } from "./analysisApi";
import { fetchLinkedEvents, type LinkedEvent } from "./eventApi";
import { fetchDailyStatus, OperationsApiError, runDailyNow, type DailyRunStatus } from "./operationsApi";

type BriefTab = "overview" | "learn" | "build" | "earn" | "sources";
interface TodayPageProps { readonly apiBaseUrl: string; readonly healthState: ApiHealthState; readonly healthLabel: string; }

function time(value: string | null | undefined): string {
  if (!value) return "No successful run yet";
  return new Intl.DateTimeFormat("en-IN", { hour: "numeric", minute: "2-digit", day: "numeric", month: "short" }).format(new Date(value));
}
function eventLabel(event: LinkedEvent): string {
  if (event.corroboration_status === "single_source") return "Single source";
  if (event.corroboration_status === "corroborated") return `${event.source_count} independent sources`;
  return `${event.source_count} associated sources`;
}

export function TodayPage({ apiBaseUrl, healthState, healthLabel }: TodayPageProps) {
  const [today, setToday] = useState<TodayReport | null>(null);
  const [report, setReport] = useState<DailyIntelligenceReport | null>(null);
  const [daily, setDaily] = useState<DailyRunStatus | null>(null);
  const [events, setEvents] = useState<readonly LinkedEvent[]>([]);
  const [tab, setTab] = useState<BriefTab>("overview");
  const [state, setState] = useState<"loading" | "ready" | "running" | "error">("loading");
  const [error, setError] = useState<string | null>(null);

  function safeMessage(reason: unknown): string {
    if (reason instanceof AnalysisApiError || reason instanceof OperationsApiError) return reason.message;
    return "The daily brief is unavailable.";
  }

  async function load(signal: AbortSignal) {
    const [todayValue, dailyValue, reportValue, eventValue] = await Promise.all([
      fetchToday(fetch, apiBaseUrl, signal), fetchDailyStatus(fetch, apiBaseUrl, signal),
      fetchCompleteDailyReport(fetch, apiBaseUrl, signal), fetchLinkedEvents(fetch, apiBaseUrl, signal),
    ]);
    setToday(todayValue); setDaily(dailyValue); setReport(reportValue); setEvents(eventValue.items); setState("ready"); setError(null);
  }
  useEffect(() => {
    const controller = new AbortController();
    void Promise.allSettled([
      fetchToday(fetch, apiBaseUrl, controller.signal),
      fetchDailyStatus(fetch, apiBaseUrl, controller.signal),
      fetchCompleteDailyReport(fetch, apiBaseUrl, controller.signal),
      fetchLinkedEvents(fetch, apiBaseUrl, controller.signal),
    ]).then(([todayResult, dailyResult, reportResult, eventResult]) => {
      if (controller.signal.aborted) return;
      if (todayResult.status === "fulfilled") setToday(todayResult.value);
      if (dailyResult.status === "fulfilled") setDaily(dailyResult.value);
      if (eventResult.status === "fulfilled") setEvents(eventResult.value.items);
      if (reportResult.status === "fulfilled") {
        setReport(reportResult.value);
        setError(dailyResult.status === "rejected" ? safeMessage(dailyResult.reason) : null);
        setState("ready");
      } else {
        setError(safeMessage(reportResult.reason));
        setState("error");
      }
    });
    return () => controller.abort();
  }, [apiBaseUrl]);

  async function runNow() {
    setState("running"); setError(null);
    try {
      const result = await runDailyNow(fetch, apiBaseUrl);
      if (result.status === "failed") throw new OperationsApiError(result.safe_detail ?? "The daily run stopped safely.");
      await load(new AbortController().signal);
    } catch (reason) { setError(reason instanceof OperationsApiError ? reason.message : "The daily run stopped safely. Review Operations and retry."); setState("error"); }
  }

  const eventByWork = useMemo(() => new Map(events.filter((item) => item.primary_work_id).map((item) => [item.primary_work_id, item])), [events]);
  const briefByWork = useMemo(() => new Map(today?.ranked.map((item) => [item.work_id, item.brief]) ?? []), [today]);
  const developments = report?.top_technical.slice(0, 3) ?? [];
  if (state === "loading") return <main className="daily-brief page-frame"><div className="state-banner" role="status">Loading today’s local intelligence…</div></main>;

  return <main className="daily-brief page-frame">
    <header className="brief-header"><div><p className="eyebrow">Daily Brief</p><h1>{report ? new Intl.DateTimeFormat("en-IN", { weekday: "long", day: "numeric", month: "long" }).format(new Date(`${report.report_date}T12:00:00`)) : "Today"}</h1><p className="last-success">Last successful run {time(daily?.latest_success_at)}</p></div><div className="brief-actions"><span className={`pipeline-pill ${daily?.latest_run?.status ?? "idle"}`}><i />{daily?.running ? "Pipeline running" : daily?.latest_run ? `Pipeline ${daily.latest_run.status}` : "Ready"}</span><button className="primary-action" type="button" disabled={state === "running" || daily?.running || healthState !== "healthy"} onClick={() => void runNow()}>{state === "running" ? "Running…" : "Run / Refresh"}</button></div></header>
    {error && <div className="state-banner error" role="alert"><strong>Daily brief needs attention</strong><span>{error}</span><a href="#settings">Open Operations</a></div>}
    {!report ? <div className="empty-state"><h2>No published daily brief</h2><p>{error ?? healthLabel}</p><button type="button" onClick={() => void runNow()}>Run the bounded pipeline</button></div> : <>
      <section className="coverage-row" aria-label="Source coverage">{report.source_coverage.filter((item) => item.records > 0 || item.status !== "healthy").slice(0, 8).map((item) => <span className={item.status === "healthy" ? "source-chip" : "source-chip degraded"} key={item.source_key}><i />{item.source_key}<b>{item.records}</b></span>)}</section>
      <section className="executive-summary" aria-labelledby="executive-heading"><div><p className="eyebrow">Executive briefing</p><h2 id="executive-heading">What deserves attention</h2></div><p>{report.executive_briefing}</p></section>
      <div className="brief-tabs" role="tablist" aria-label="Daily Brief sections">{(["overview", "learn", "build", "earn", "sources"] as const).map((item) => <button key={item} role="tab" aria-selected={tab === item} onClick={() => setTab(item)}>{item[0]?.toUpperCase()}{item.slice(1)}</button>)}</div>
      <section className="top-developments" aria-labelledby="developments-heading"><div className="section-title"><div><p className="eyebrow">Top three</p><h2 id="developments-heading">Developments</h2></div><span>{report.pipeline.discovered} discovered · {report.pipeline.filtered} ranked</span></div><div className="development-grid">{developments.map((item, index) => {
        const event = eventByWork.get(item.work_id); const brief = briefByWork.get(item.work_id); const change = brief?.output && "change" in brief.output ? brief.output.change : item.title; const significance = brief?.output && "technical_relevance" in brief.output ? brief.output.technical_relevance : item.reason; const signal = item.model_signal ?? undefined; const related = event?.sources[0]; const deepDive = report.deep_dives[index];
        return <article className="development-card" key={item.work_id}><header><span className="development-number">0{index + 1}</span><span className="category-pill">Research</span></header><h3>{change}</h3><p>{significance}</p><div className="source-icons" aria-label="Related sources">{(event?.sources ?? []).slice(0, 4).map((source) => <span key={source.artifact_id} title={source.source_key}>{source.source_key.slice(0, 1).toUpperCase()}</span>)}{!event?.sources.length && <span title="Primary paper">P</span>}</div><dl className="signal-row"><div><dt>Novelty</dt><dd>{signal ? Math.round(signal.novelty * 100) : "—"}</dd></div><div><dt>Freshness</dt><dd>{event ? Math.round(Math.max(...event.sources.map((source) => source.freshness), 0) * 100) : "—"}</dd></div><div><dt>Trust</dt><dd>{signal ? `${Math.round(signal.confidence * 100)}%` : "Pending"}</dd></div></dl><div className="corroboration-line"><strong>{event ? eventLabel(event) : "Primary source only"}</strong><span>{event?.linkage_reason ?? "No cross-source association established."}</span></div>{related && <a className="related-link" href={related.canonical_url} target="_blank" rel="noreferrer">Related {(related.source_type ?? related.artifact_type).replaceAll("_", " ")} ↗</a>}<footer><a href={`#discover/${encodeURIComponent(item.work_id)}`}>Quick View</a>{deepDive ? <a href={`#report/${encodeURIComponent(deepDive)}`}>Deep Dive</a> : <span>Deep Dive pending</span>}</footer></article>;
      })}</div></section>
      <section className="brief-tab-panel" role="tabpanel">
        {tab === "overview" && <div className="overview-layout"><article><h2>What changed today</h2>{report.what_happened.slice(0, 4).map((item) => <p key={item}>{item}</p>)}</article><article><h2>Why it matters</h2>{developments.map((item) => <p key={item.work_id}>{briefByWork.get(item.work_id)?.output && "technical_relevance" in (briefByWork.get(item.work_id)?.output ?? {}) ? String((briefByWork.get(item.work_id)?.output as { technical_relevance: string }).technical_relevance) : item.reason}</p>)}</article><article><h2>Watch next</h2>{report.watchlist_changes.length ? report.watchlist_changes.slice(0, 4).map((item) => <p key={item}>{item}</p>) : <p>No material watchlist change was detected.</p>}</article><article><h2>Since the previous report</h2>{report.important_updates.length ? report.important_updates.slice(0, 4).map((item, index) => <p key={`${item.summary}-${index}`}>{item.summary}</p>) : <p>No paper revision or release change needs attention.</p>}</article></div>}
        {tab === "learn" && <div className="action-card-grid">{report.learning_plan.length ? report.learning_plan.map((item) => <article className="action-card" key={item.topic}><span className="action-label">Learning plan · {item.estimated_minutes} min</span><h2>{item.topic}</h2><p>{item.why_it_matters}</p><dl><div><dt>Prerequisites</dt><dd>{item.prerequisites.join(" · ")}</dd></div><div><dt>Resource</dt><dd>{item.recommended_item}</dd></div><div><dt>Exercise</dt><dd>{item.exercise}</dd></div><div><dt>Expected outcome</dt><dd>{item.expected_outcome}</dd></div></dl></article>) : <div className="empty-state compact"><h2>No cited learning plan yet</h2><p>A verified deep dive is required.</p></div>}</div>}
        {tab === "build" && <div className="action-card-grid">{report.what_to_build.length ? report.what_to_build.map((item) => <article className="action-card" key={item.work_id}><span className="action-label">Bounded prototype · {item.estimated_effort}</span><h2>{item.prototype}</h2><p>{item.user_problem}</p><dl><div><dt>Components</dt><dd>{item.architecture.join(" → ")}</dd></div><div><dt>Recommended resource</dt><dd>{item.recommended_resource}</dd></div><div><dt>Validation test</dt><dd>{item.validation_test}</dd></div><div><dt>Project relevance</dt><dd>{item.project_relevance.join(" · ")}</dd></div></dl></article>) : <div className="empty-state compact"><h2>No evidence-backed build plan yet</h2><p>Build directions appear only when primary evidence is available.</p></div>}</div>}
        {tab === "earn" && <div className="action-card-grid">{report.commercial_hypotheses.length ? report.commercial_hypotheses.map((item) => <article className="action-card commercial" key={`${item.work_id}-${item.problem}`}><span className="action-label amber">Commercial hypothesis · {Math.round(item.confidence * 100)}% confidence</span><h2>{item.problem}</h2><dl><div><dt>Target customer</dt><dd>{item.target_buyer}</dd></div><div><dt>Offer</dt><dd>{item.proposed_offer}</dd></div><div><dt>Provisional pricing</dt><dd>{item.pricing_hypothesis}</dd></div><div><dt>48-hour validation</dt><dd>{item.validation_experiment}</dd></div><div><dt>India relevance</dt><dd>{item.india_market_relevance}</dd></div><div><dt>Assumptions & risks</dt><dd>{[...item.assumptions, ...item.risks].join(" · ")}</dd></div><div><dt>Project relevance</dt><dd>{item.project_relevance.join(" · ")}</dd></div><div><dt>Supporting evidence</dt><dd>{item.supporting_evidence.length} cited spans</dd></div></dl></article>) : <div className="empty-state compact"><h2>No commercial hypothesis passed the evidence gate</h2><p>Rank alone does not create an opportunity.</p></div>}</div>}
        {tab === "sources" && <div className="source-list">{events.slice(0, 12).map((event) => <article key={event.id}><div><span className={`semantic-label ${event.classification}`}>{event.classification.replaceAll("_", " ")}</span><strong>{event.title}</strong><p>{event.linkage_reason}</p></div><span>{eventLabel(event)} · {Math.round(event.association_confidence * 100)}% association confidence</span></article>)}</div>}
      </section>
    </>}
  </main>;
}
