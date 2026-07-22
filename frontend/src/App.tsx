import { useEffect, useRef, useState } from "react";

import { INITIAL_API_HEALTH_STATE, resolveApiHealthState, type ApiHealthState } from "./apiHealth";
import { ExplorePage } from "./ExplorePage";
import { IntelligencePage } from "./IntelligencePage";
import { runDailyNow } from "./operationsApi";
import { ReportPage } from "./ReportPage";
import { SystemPage } from "./SystemPage";
import { TodayPage } from "./TodayPage";

type PrimaryPage = "daily" | "discover" | "intelligence" | "settings";
type Route = {
  readonly page: PrimaryPage | "report";
  readonly detailId: string | null;
  readonly intelligenceTab: "topics" | "opportunities";
};

const navigation: readonly { key: PrimaryPage; label: string; icon: string }[] = [
  { key: "daily", label: "Daily Brief", icon: "D" },
  { key: "discover", label: "Discover", icon: "⌕" },
  { key: "intelligence", label: "Intelligence", icon: "I" },
  { key: "settings", label: "Settings", icon: "⚙" },
];

function routeFromHash(): Route {
  const raw = window.location.hash.replace(/^#/, "");
  if (raw.startsWith("report/")) {
    return { page: "report", detailId: decodeURIComponent(raw.split("/")[1] ?? ""), intelligenceTab: "topics" };
  }
  if (raw.startsWith("discover/") || raw.startsWith("explore/")) {
    return { page: "discover", detailId: decodeURIComponent(raw.split("/")[1] ?? ""), intelligenceTab: "topics" };
  }
  if (raw === "discover" || raw === "explore") return { page: "discover", detailId: null, intelligenceTab: "topics" };
  if (raw === "intelligence/opportunities" || raw === "opportunities") {
    return { page: "intelligence", detailId: null, intelligenceTab: "opportunities" };
  }
  if (raw === "intelligence" || raw === "intelligence/topics" || raw === "topics") {
    return { page: "intelligence", detailId: null, intelligenceTab: "topics" };
  }
  if (raw === "settings" || raw === "system") return { page: "settings", detailId: null, intelligenceTab: "topics" };
  return { page: "daily", detailId: null, intelligenceTab: "topics" };
}

function CommandPalette({ close, apiBaseUrl }: { readonly close: () => void; readonly apiBaseUrl: string }) {
  const input = useRef<HTMLInputElement>(null);
  const [query, setQuery] = useState("");
  const commands = [
    { label: "Search stored research", hint: "Discover", href: "#discover" },
    { label: "Run daily pipeline", hint: "Daily Brief", href: "#daily", action: "run" },
    { label: "Open latest brief", hint: "Daily Brief", href: "#daily" },
    { label: "Discover by source or topic", hint: "Filters", href: "#discover" },
    { label: "View opportunities", hint: "Intelligence", href: "#intelligence/opportunities" },
    { label: "Inspect operations", hint: "Settings", href: "#settings" },
  ].filter((item) => item.label.toLowerCase().includes(query.toLowerCase()));

  useEffect(() => input.current?.focus(), []);

  return (
    <div className="palette-backdrop" role="presentation" onMouseDown={close}>
      <section className="command-palette" role="dialog" aria-modal="true" aria-label="Command palette" onMouseDown={(event) => event.stopPropagation()}>
        <label>
          <span aria-hidden="true">⌕</span>
          <input ref={input} value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search or run a command…" aria-label="Search commands" />
          <kbd>Esc</kbd>
        </label>
        <ul>
          {commands.map((item) => (
            <li key={item.label}>
              <a href={item.href} onClick={() => { close(); if (item.action === "run") void runDailyNow(fetch, apiBaseUrl); }}>
                <span>{item.label}</span><small>{item.hint}</small>
              </a>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}

function App() {
  const [route, setRoute] = useState(routeFromHash);
  const [health, setHealth] = useState<ApiHealthState>(INITIAL_API_HEALTH_STATE);
  const [railCollapsed, setRailCollapsed] = useState(true);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const apiBaseUrl = import.meta.env.VITE_API_BASE_URL ?? "/api";

  useEffect(() => {
    const onHash = () => setRoute(routeFromHash());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    void resolveApiHealthState(fetch, `${apiBaseUrl}/health`, controller.signal).then(setHealth);
    return () => controller.abort();
  }, [apiBaseUrl]);

  useEffect(() => {
    const keyboard = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setPaletteOpen((open) => !open);
      }
      if (event.key === "Escape") setPaletteOpen(false);
    };
    window.addEventListener("keydown", keyboard);
    return () => window.removeEventListener("keydown", keyboard);
  }, []);

  const active = route.page === "report" ? "discover" : route.page;
  return (
    <div className={`product-shell ${railCollapsed ? "rail-collapsed" : ""}`}>
      <aside className="icon-rail">
        <a className="product-mark" href="#daily" aria-label="AI Intelligence OS Daily Brief">AI</a>
        <nav aria-label="Primary navigation">
          {navigation.map((item) => (
            <a key={item.key} href={`#${item.key}`} className={active === item.key ? "active" : undefined} aria-current={active === item.key ? "page" : undefined}>
              <span aria-hidden="true">{item.icon}</span><strong>{item.label}</strong>
            </a>
          ))}
        </nav>
        <button className="rail-toggle" type="button" onClick={() => setRailCollapsed((value) => !value)} aria-label={railCollapsed ? "Expand navigation" : "Collapse navigation"}>⇤</button>
      </aside>

      <div className="product-workspace">
        <header className="product-topbar">
          <a href="#daily" className="mobile-brand">AI Intelligence OS</a>
          <button className="palette-trigger" type="button" onClick={() => setPaletteOpen(true)}>
            <span>⌕</span><span>Search or command</span><kbd>Ctrl K</kbd>
          </button>
          <span className={`api-indicator ${health}`} role="status" title={`Local API: ${health}`}><i />Local API {health}</span>
        </header>

        {route.page === "discover" ? <ExplorePage apiBaseUrl={apiBaseUrl} initialPaperId={route.detailId} />
          : route.page === "intelligence" ? <IntelligencePage apiBaseUrl={apiBaseUrl} initialTab={route.intelligenceTab} />
            : route.page === "settings" ? <SystemPage apiBaseUrl={apiBaseUrl} />
              : route.page === "report" && route.detailId ? <ReportPage apiBaseUrl={apiBaseUrl} analysisId={route.detailId} />
                : <TodayPage apiBaseUrl={apiBaseUrl} healthState={health} healthLabel={`Local API ${health}`} />}
      </div>

      <nav className="bottom-nav" aria-label="Mobile navigation">
        {navigation.map((item) => <a key={item.key} href={`#${item.key}`} className={active === item.key ? "active" : undefined}><span>{item.icon}</span>{item.label}</a>)}
      </nav>
      {paletteOpen && <CommandPalette close={() => setPaletteOpen(false)} apiBaseUrl={apiBaseUrl} />}
    </div>
  );
}

export default App;
