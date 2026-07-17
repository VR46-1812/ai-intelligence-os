import { useEffect, useState } from "react";

type ApiHealthState = "loading" | "healthy" | "unavailable";

interface HealthResponse {
  service: string;
  status: "ok";
}

const navigationItems = ["Today", "Explore", "Topics", "Opportunities", "System"] as const;

const healthCopy: Record<ApiHealthState, { label: string; detail: string }> = {
  loading: {
    label: "Checking local API",
    detail: "Connecting to the workspace service…",
  },
  healthy: {
    label: "Local API healthy",
    detail: "The private workspace service is ready.",
  },
  unavailable: {
    label: "Local API unavailable",
    detail: "Start the backend, then refresh this page.",
  },
};

function isHealthResponse(value: unknown): value is HealthResponse {
  if (typeof value !== "object" || value === null) {
    return false;
  }

  const candidate = value as Record<string, unknown>;
  return candidate.service === "ai-intelligence-os" && candidate.status === "ok";
}

function App() {
  const [healthState, setHealthState] = useState<ApiHealthState>("loading");
  const formattedDate = new Intl.DateTimeFormat("en", {
    weekday: "long",
    month: "short",
    day: "numeric",
  }).format(new Date());

  useEffect(() => {
    const controller = new AbortController();
    const apiBaseUrl = import.meta.env.VITE_API_BASE_URL ?? "/api";

    async function checkHealth(): Promise<void> {
      try {
        const response = await fetch(`${apiBaseUrl}/health`, {
          headers: { Accept: "application/json" },
          signal: controller.signal,
        });

        if (!response.ok) {
          setHealthState("unavailable");
          return;
        }

        const payload: unknown = await response.json();
        setHealthState(isHealthResponse(payload) ? "healthy" : "unavailable");
      } catch (error: unknown) {
        if (error instanceof DOMException && error.name === "AbortError") {
          return;
        }
        setHealthState("unavailable");
      }
    }

    void checkHealth();
    return () => controller.abort();
  }, []);

  const currentHealth = healthCopy[healthState];

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <a className="brand" href="#workspace" aria-label="AI Intelligence OS home">
          <span className="brand-mark" aria-hidden="true">AI</span>
          <span>
            <strong>Intelligence OS</strong>
            <small>Local research workspace</small>
          </span>
        </a>

        <nav aria-label="Primary navigation">
          <p className="nav-label">Workspace</p>
          <ul>
            {navigationItems.map((item, index) => (
              <li key={item}>
                <a className={index === 0 ? "active" : undefined} href={`#${item.toLowerCase()}`}>
                  <span className="nav-dot" aria-hidden="true" />
                  {item}
                </a>
              </li>
            ))}
          </ul>
        </nav>

        <div className="local-note">
          <span className="lock" aria-hidden="true">LOCAL</span>
          <p>Your workspace is designed to keep data and inference on this machine.</p>
        </div>
      </aside>

      <div className="workspace" id="workspace">
        <header className="topbar">
          <div>
            <p className="eyebrow">{formattedDate} · Foundation status</p>
            <h1>Today</h1>
          </div>
          <div className={`health-pill ${healthState}`} role="status" aria-live="polite">
            <span className="status-dot" aria-hidden="true" />
            <span>
              <strong>{currentHealth.label}</strong>
              <small>{currentHealth.detail}</small>
            </span>
          </div>
        </header>

        <main>
          <section className="hero" aria-labelledby="foundation-heading">
            <div>
              <p className="eyebrow cyan">Foundation / M0.1</p>
              <h2 id="foundation-heading">Your intelligence workspace is taking shape.</h2>
              <p className="hero-copy">
                The local application shell and API health boundary are ready. Discovery,
                analysis, and reporting will arrive as bounded, testable milestones.
              </p>
            </div>
            <div className="foundation-score" aria-label="Foundation milestone one of one ready">
              <span>01</span>
              <small>Foundation slice</small>
            </div>
          </section>

          <section className="section-block" aria-labelledby="workspace-status-heading">
            <div className="section-heading">
              <div>
                <p className="eyebrow">System readiness</p>
                <h2 id="workspace-status-heading">Workspace status</h2>
              </div>
              <span className="phase-badge">Scaffold only</span>
            </div>

            <div className="status-grid">
              <article className="status-card featured">
                <p className="card-index">01 / API</p>
                <h3>Health boundary</h3>
                <p>A typed FastAPI endpoint confirms the local service is available.</p>
                <span className={`card-state ${healthState}`}>{currentHealth.label}</span>
              </article>
              <article className="status-card">
                <p className="card-index">02 / INTERFACE</p>
                <h3>Professional shell</h3>
                <p>A responsive foundation for the high-density research workspace.</p>
                <span className="card-state ready">Ready</span>
              </article>
              <article className="status-card muted-card">
                <p className="card-index">03 / PIPELINE</p>
                <h3>Intelligence pipeline</h3>
                <p>Reserved for later milestones after the platform baseline is verified.</p>
                <span className="card-state planned">Not implemented</span>
              </article>
            </div>
          </section>
        </main>
      </div>
    </div>
  );
}

export default App;
