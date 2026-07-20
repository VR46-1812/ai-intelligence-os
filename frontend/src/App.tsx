import { useEffect, useState } from "react";

import {
  INITIAL_API_HEALTH_STATE,
  resolveApiHealthState,
  type ApiHealthState,
} from "./apiHealth";
import { ExplorePage } from "./ExplorePage";
import { TodayPage } from "./TodayPage";

const navigationItems = ["Today", "Explore", "Topics", "Opportunities", "System"] as const;
type ActivePage = "today" | "explore";

const healthCopy: Record<ApiHealthState, { label: string; detail: string }> = {
  loading: {
    label: "Checking local API",
    detail: "Connecting to the workspace service…",
  },
  healthy: {
    label: "Local API healthy",
    detail: "Private catalog and discovery are ready.",
  },
  unavailable: {
    label: "Local API unavailable",
    detail: "Start the backend, then refresh this page.",
  },
};

function routeFromHash(): { page: ActivePage; paperId: string | null } {
  const route = window.location.hash.replace(/^#/, "");
  if (route.startsWith("explore")) {
    const encodedPaperId = route.split("/")[1];
    return {
      page: "explore",
      paperId: encodedPaperId ? decodeURIComponent(encodedPaperId) : null,
    };
  }
  return { page: "today", paperId: null };
}

function App() {
  const [healthState, setHealthState] = useState<ApiHealthState>(INITIAL_API_HEALTH_STATE);
  const [route, setRoute] = useState(routeFromHash);
  const apiBaseUrl = import.meta.env.VITE_API_BASE_URL ?? "/api";
  const formattedDate = new Intl.DateTimeFormat("en", {
    weekday: "long",
    month: "short",
    day: "numeric",
  }).format(new Date());

  useEffect(() => {
    const onHashChange = () => setRoute(routeFromHash());
    window.addEventListener("hashchange", onHashChange);
    return () => window.removeEventListener("hashchange", onHashChange);
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    void resolveApiHealthState(fetch, `${apiBaseUrl}/health`, controller.signal).then(
      (nextState) => {
        if (!controller.signal.aborted) setHealthState(nextState);
      },
    );
    return () => controller.abort();
  }, [apiBaseUrl]);

  const currentHealth = healthCopy[healthState];

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <a className="brand" href="#today" aria-label="AI Intelligence OS home">
          <span className="brand-mark" aria-hidden="true">AI</span>
          <span>
            <strong>Intelligence OS</strong>
            <small>Local research workspace</small>
          </span>
        </a>

        <nav aria-label="Primary navigation">
          <p className="nav-label">Workspace</p>
          <ul>
            {navigationItems.map((item) => {
              const itemKey = item.toLowerCase();
              const implemented = itemKey === "today" || itemKey === "explore";
              return (
                <li key={item}>
                  <a
                    className={route.page === itemKey ? "active" : implemented ? undefined : "disabled"}
                    href={implemented ? `#${itemKey}` : undefined}
                    aria-disabled={!implemented}
                  >
                    <span className="nav-dot" aria-hidden="true" />
                    {item}
                  </a>
                </li>
              );
            })}
          </ul>
        </nav>

        <div className="local-note">
          <span className="lock" aria-hidden="true">LOCAL</span>
          <p>Your papers, metadata, and future analysis stay on this machine.</p>
        </div>
      </aside>

      <div className="workspace" id="workspace">
        <header className="topbar">
          <div>
            <p className="eyebrow">{formattedDate} · Local workspace</p>
            <h1>{route.page === "explore" ? "Explore" : "Today"}</h1>
          </div>
          <div className={`health-pill ${healthState}`} role="status" aria-live="polite">
            <span className="status-dot" aria-hidden="true" />
            <span>
              <strong>{currentHealth.label}</strong>
              <small>{currentHealth.detail}</small>
            </span>
          </div>
        </header>

        {route.page === "explore" ? (
          <ExplorePage apiBaseUrl={apiBaseUrl} initialPaperId={route.paperId} />
        ) : (
          <TodayPage healthState={healthState} healthLabel={currentHealth.label} />
        )}
      </div>
    </div>
  );
}

export default App;
