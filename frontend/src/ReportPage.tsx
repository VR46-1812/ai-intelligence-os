import { useEffect, useState } from "react";

import { AnalysisApiError, fetchAnalysis, type AnalysisResult, type ReportClaim } from "./analysisApi";

interface ReportPageProps { readonly apiBaseUrl: string; readonly analysisId: string; }

function Claims({ claims }: { readonly claims: readonly ReportClaim[] }) {
  return <div className="report-claims">{claims.map((claim, index) => <article key={claim.id ?? `${claim.text}-${index}`}>
    <div><span className={`claim-kind ${claim.type}`}>{claim.type}</span><span>{claim.verification_status ?? (claim.evidence_ids.length ? "supported" : "hypothesis")}</span></div>
    <p>{claim.text}</p>
    <div className="citation-row">{claim.evidence_ids.length ? claim.evidence_ids.map((id) => <a key={id} href={`#evidence-${encodeURIComponent(id)}`}>Evidence {id.slice(-8)}</a>) : <span>No factual assertion · requires validation</span>}</div>
  </article>)}</div>;
}

export function ReportPage({ apiBaseUrl, analysisId }: ReportPageProps) {
  const [result, setResult] = useState<AnalysisResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    void fetchAnalysis(fetch, apiBaseUrl, analysisId, controller.signal).then(setResult).catch((reason: unknown) => {
      if (!controller.signal.aborted) setError(reason instanceof AnalysisApiError ? reason.message : "Report unavailable.");
    });
    return () => controller.abort();
  }, [analysisId, apiBaseUrl]);
  if (error) return <main className="report-main"><div className="state-panel error-panel" role="alert"><h2>Report unavailable</h2><p>{error}</p></div></main>;
  if (!result) return <main className="report-main"><div className="analysis-banner" role="status">Loading verified report…</div></main>;
  if (!result.output || !("title" in result.output)) return <main className="report-main"><div className="state-panel error-panel"><h2>Deep dive incomplete</h2><p>{result.safe_detail ?? "The report has no verified output."}</p></div></main>;
  const report = result.output;
  const sections = [["Executive significance", report.executive_significance], ["Problem and context", report.problem_context], ["Method", report.method], ["Evaluation", report.evaluation], ["Limitations", report.limitations]] as const;
  return <main className="report-main">
    <a className="back-link" href={`#explore/${encodeURIComponent(result.work_id)}`}>← Back to paper</a>
    <header className="report-header"><p className="eyebrow cyan">Scout deep dive / {report.publication_status}</p><h2>{report.title}</h2><div><span>{Math.round(result.citation_coverage * 100)}% citation coverage</span><span>{result.citations_verified} references verified</span><span>{(result.duration_ms ?? 0) / 1000}s generation</span>{result.cached && <span>Cached</span>}</div></header>
    <div className="report-layout"><article className="report-body">{sections.map(([title, section]) => <section key={title}><div className="report-section-title"><h3>{title}</h3><span>{Math.round(section.confidence * 100)}% confidence</span></div><p>{section.markdown}</p></section>)}</article><aside className="claims-drawer" aria-label="Verified claims"><p className="eyebrow">Claims and citations</p><Claims claims={report.claims} />{report.skeptic_findings.length > 0 && <section className="skeptic-box"><h3>Skeptic findings</h3>{report.skeptic_findings.map((finding, index) => <p key={`${finding.finding}-${index}`}><strong>{finding.severity}</strong> {finding.finding}</p>)}</section>}</aside></div>
  </main>;
}
