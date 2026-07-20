import type { ApiHealthState } from "./apiHealth";

interface TodayPageProps {
  readonly healthState: ApiHealthState;
  readonly healthLabel: string;
}

export function TodayPage({ healthState, healthLabel }: TodayPageProps) {
  return (
    <main>
      <section className="hero" aria-labelledby="foundation-heading">
        <div>
          <p className="eyebrow cyan">Local intelligence workspace</p>
          <h2 id="foundation-heading">Your research system is ready to explore.</h2>
          <p className="hero-copy">
            Authoritative source discovery now flows into a private, searchable catalog.
            Analysis and reporting remain intentionally separate bounded stages.
          </p>
        </div>
        <div className="foundation-score" aria-label="Local research catalog available">
          <span>AI</span>
          <small>Local first</small>
        </div>
      </section>

      <section className="section-block" aria-labelledby="workspace-status-heading">
        <div className="section-heading">
          <div>
            <p className="eyebrow">System readiness</p>
            <h2 id="workspace-status-heading">Workspace status</h2>
          </div>
          <span className="phase-badge">Catalog online</span>
        </div>

        <div className="status-grid">
          <article className="status-card featured">
            <p className="card-index">01 / API</p>
            <h3>Local service</h3>
            <p>A typed FastAPI boundary keeps the workspace available on this machine.</p>
            <span className={`card-state ${healthState}`}>{healthLabel}</span>
          </article>
          <article className="status-card">
            <p className="card-index">02 / CATALOG</p>
            <h3>Explore research</h3>
            <p>Stored papers are searchable by metadata, source, topic, and publication date.</p>
            <a className="card-link" href="#explore">Open Explore <span aria-hidden="true">→</span></a>
          </article>
          <article className="status-card muted-card">
            <p className="card-index">03 / ANALYSIS</p>
            <h3>Intelligence pipeline</h3>
            <p>Ranking and evidence-backed reports remain later, explicitly bounded stages.</p>
            <span className="card-state planned">Not implemented</span>
          </article>
        </div>
      </section>
    </main>
  );
}
