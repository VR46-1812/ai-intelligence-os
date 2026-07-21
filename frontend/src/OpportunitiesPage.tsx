import { useEffect, useState } from "react";
import { fetchOpportunities, IntelligenceApiError, type Opportunity } from "./intelligenceApi";

export function OpportunitiesPage({ apiBaseUrl }: { readonly apiBaseUrl: string }) {
  const [items, setItems] = useState<readonly Opportunity[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { const controller = new AbortController(); void fetchOpportunities(fetch, apiBaseUrl, controller.signal).then(setItems).catch((reason: unknown) => { if (!controller.signal.aborted) setError(reason instanceof IntelligenceApiError ? reason.message : "Opportunities unavailable."); }); return () => controller.abort(); }, [apiBaseUrl]);
  if (error) return <main className="intelligence-page"><div className="state-panel error-panel" role="alert"><h2>Opportunities unavailable</h2><p>{error}</p></div></main>;
  if (!items) return <main className="intelligence-page"><div className="analysis-banner" role="status">Loading verified opportunities…</div></main>;
  return <main className="intelligence-page"><header className="page-intro"><p className="eyebrow cyan">Published deep dives · evidence required</p><h2>Opportunities</h2><p>Engineering applications and commercial hypotheses appear only when a verified deep dive links them to stored page evidence.</p></header>
    {items.length === 0 ? <div className="state-panel"><h3>No verified opportunities yet</h3><p>Publish a citation-verified deep dive to populate this view.</p></div> : <div className="opportunity-grid">{items.map((item, index) => <article className="intelligence-card opportunity" key={`${item.work_id}-${item.kind}-${index}`}><header><span className={`opportunity-kind ${item.kind}`}>{item.kind}</span><span>{Math.round(item.confidence * 100)}% confidence</span></header><h3>{item.headline}</h3><p>{item.detail}</p><a className="source-paper" href={`#explore/${encodeURIComponent(item.work_id)}`}>{item.title}</a><div className="citation-row">{item.evidence_ids.map((id) => <span key={id}>Evidence {id.slice(-8)}</span>)}</div></article>)}</div>}
  </main>;
}
