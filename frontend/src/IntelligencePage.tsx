import { useState } from "react";

import { OpportunitiesPage } from "./OpportunitiesPage";
import { TopicsPage } from "./TopicsPage";

export function IntelligencePage({ apiBaseUrl, initialTab }: { readonly apiBaseUrl: string; readonly initialTab: "topics" | "opportunities" }) {
  const [tab, setTab] = useState(initialTab);
  function select(next: "topics" | "opportunities") {
    setTab(next);
    window.history.replaceState(null, "", `#intelligence/${next}`);
  }
  return (
    <main className="intelligence-page page-frame">
      <header className="page-header compact">
        <div><p className="eyebrow">Accumulated intelligence</p><h1>Intelligence</h1><p>Follow technical themes and turn cited developments into testable build and commercial hypotheses.</p></div>
      </header>
      <div className="context-tabs" role="tablist" aria-label="Intelligence views">
        <button role="tab" aria-selected={tab === "topics"} onClick={() => select("topics")}>Topics</button>
        <button role="tab" aria-selected={tab === "opportunities"} onClick={() => select("opportunities")}>Opportunities</button>
      </div>
      {tab === "topics" ? <TopicsPage apiBaseUrl={apiBaseUrl} embedded /> : <OpportunitiesPage apiBaseUrl={apiBaseUrl} embedded />}
    </main>
  );
}
