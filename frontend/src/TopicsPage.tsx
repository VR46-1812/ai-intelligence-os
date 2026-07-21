import { useEffect, useState } from "react";
import { fetchTopics, IntelligenceApiError, type TopicOverview } from "./intelligenceApi";

export function TopicsPage({ apiBaseUrl }: { readonly apiBaseUrl: string }) {
  const [topics, setTopics] = useState<readonly TopicOverview[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { const controller = new AbortController(); void fetchTopics(fetch, apiBaseUrl, controller.signal).then(setTopics).catch((reason: unknown) => { if (!controller.signal.aborted) setError(reason instanceof IntelligenceApiError ? reason.message : "Topics unavailable."); }); return () => controller.abort(); }, [apiBaseUrl]);
  if (error) return <main className="intelligence-page"><div className="state-panel error-panel" role="alert"><h2>Topics unavailable</h2><p>{error}</p></div></main>;
  if (!topics) return <main className="intelligence-page"><div className="analysis-banner" role="status">Loading ranked topics…</div></main>;
  return <main className="intelligence-page"><header className="page-intro"><p className="eyebrow cyan">Controlled taxonomy · deterministic ranking</p><h2>Research topics</h2><p>Daily change reflects papers ingested locally today. Scout signals annotate ranking but never replace its deterministic score.</p></header>
    {topics.length === 0 ? <div className="state-panel"><h3>No classified papers yet</h3><p>Run the daily pipeline to classify stored research.</p></div> : <div className="topic-grid">{topics.map((item) => <article className="intelligence-card" key={item.key}><header><div><p className="eyebrow">{item.key}</p><h3>{item.label}</h3></div><span className="change-badge">+{item.daily_change} today</span></header><p>{item.paper_count} stored papers</p><ol>{item.papers.map((paper) => <li key={paper.work_id}><a href={`#explore/${encodeURIComponent(paper.work_id)}`}>{paper.title}</a><strong>{paper.score.toFixed(1)}</strong></li>)}</ol></article>)}</div>}
  </main>;
}
