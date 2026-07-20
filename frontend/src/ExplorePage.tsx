import { type FormEvent, useEffect, useMemo, useState } from "react";

import {
  CatalogApiError,
  type CatalogFilterOptions,
  type CatalogPaper,
  type CatalogPaperPage,
  type CatalogQuery,
  fetchCatalogFilters,
  fetchCatalogPage,
  fetchPaperDetail,
  safeExternalUrl,
  syncLatestResearch,
  type SyncResult,
} from "./catalogApi";

const PAGE_SIZE = 5;
const EMPTY_FILTERS: CatalogFilterOptions = { topics: [], sources: [] };

interface ExplorePageProps {
  readonly apiBaseUrl: string;
  readonly initialPaperId: string | null;
}

type LoadState = "loading" | "ready" | "empty" | "error";
type SyncState =
  | { readonly kind: "idle" }
  | { readonly kind: "running" }
  | { readonly kind: "success"; readonly result: SyncResult }
  | { readonly kind: "error"; readonly message: string };

const INITIAL_QUERY: CatalogQuery = {
  q: "",
  topic: "",
  source: "",
  publishedFrom: "",
  publishedTo: "",
  sort: "newest",
  limit: PAGE_SIZE,
  offset: 0,
};

function formatDate(value: string | null): string {
  if (value === null) return "Date unavailable";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "Date unavailable";
  return new Intl.DateTimeFormat("en", {
    month: "short",
    day: "numeric",
    year: "numeric",
    timeZone: "UTC",
  }).format(parsed);
}

function authorLine(paper: CatalogPaper): string {
  if (paper.authors.length === 0) return "Authors unavailable";
  const visible = paper.authors.slice(0, 3).map((author) => author.display_name);
  return paper.authors.length > 3
    ? `${visible.join(", ")} +${paper.authors.length - 3}`
    : visible.join(", ");
}

function LoadingCards() {
  return (
    <div className="paper-list" aria-label="Loading research papers">
      {[0, 1, 2].map((index) => (
        <div className="paper-card skeleton-card" key={index} aria-hidden="true">
          <span className="skeleton-line short" />
          <span className="skeleton-line title" />
          <span className="skeleton-line" />
          <span className="skeleton-line" />
        </div>
      ))}
    </div>
  );
}

interface PaperDetailProps {
  readonly paper: CatalogPaper | null;
  readonly state: "loading" | "ready" | "error";
  readonly onClose: () => void;
}

function PaperDetail({ paper, state, onClose }: PaperDetailProps) {
  return (
    <aside className="detail-panel" aria-label="Paper detail" aria-live="polite">
      <button className="detail-close" type="button" onClick={onClose} aria-label="Close paper detail">
        <span aria-hidden="true">×</span>
      </button>
      {state === "loading" && <p className="state-message">Loading paper detail…</p>}
      {state === "error" && (
        <div className="state-panel compact error-panel">
          <strong>Detail unavailable</strong>
          <p>The stored paper could not be opened. Try again.</p>
        </div>
      )}
      {state === "ready" && paper && (
        <>
          <div className="detail-kicker">
            <span>{paper.source_name}</span>
            <span>{paper.current_version}</span>
            <span>{paper.publication_status}</span>
          </div>
          <h2>{paper.title}</h2>
          <p className="detail-authors">{authorLine(paper)}</p>
          <div className="detail-meta">
            <span>Published {formatDate(paper.published_at)}</span>
            <span>{paper.identities.find((identity) => identity.id_type === "arxiv")?.value}</span>
          </div>
          <section aria-labelledby="detail-abstract-heading">
            <p className="eyebrow" id="detail-abstract-heading">Abstract</p>
            <p className="detail-abstract">{paper.abstract ?? "No abstract was supplied by the source."}</p>
          </section>
          <section aria-labelledby="detail-topics-heading">
            <p className="eyebrow" id="detail-topics-heading">Controlled topics</p>
            <div className="topic-row detail-topics">
              {paper.topics.length > 0 ? paper.topics.map((topic) => (
                <span className="topic-chip" key={topic.key}>{topic.name}</span>
              )) : <span className="muted-copy">No topic assignment</span>}
            </div>
          </section>
          {safeExternalUrl(paper.external_url) && (
            <a
              className="primary-link"
              href={safeExternalUrl(paper.external_url) ?? undefined}
              target="_blank"
              rel="noreferrer noopener"
            >
              View canonical paper <span aria-hidden="true">↗</span>
            </a>
          )}
        </>
      )}
    </aside>
  );
}

export function ExplorePage({ apiBaseUrl, initialPaperId }: ExplorePageProps) {
  const [draftQuery, setDraftQuery] = useState("");
  const [query, setQuery] = useState<CatalogQuery>(INITIAL_QUERY);
  const [page, setPage] = useState<CatalogPaperPage | null>(null);
  const [filters, setFilters] = useState<CatalogFilterOptions>(EMPTY_FILTERS);
  const [loadState, setLoadState] = useState<LoadState>("loading");
  const [reloadKey, setReloadKey] = useState(0);
  const [selectedId, setSelectedId] = useState<string | null>(initialPaperId);
  const [detail, setDetail] = useState<CatalogPaper | null>(null);
  const [detailState, setDetailState] = useState<"loading" | "ready" | "error">("loading");
  const [syncState, setSyncState] = useState<SyncState>({ kind: "idle" });

  useEffect(() => {
    const controller = new AbortController();
    void fetchCatalogFilters(fetch, apiBaseUrl, controller.signal)
      .then(setFilters)
      .catch(() => setFilters(EMPTY_FILTERS));
    return () => controller.abort();
  }, [apiBaseUrl]);

  useEffect(() => {
    const controller = new AbortController();
    void fetchCatalogPage(fetch, apiBaseUrl, query, controller.signal)
      .then((nextPage) => {
        setPage(nextPage);
        setLoadState(nextPage.items.length === 0 ? "empty" : "ready");
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        setLoadState("error");
        if (!(error instanceof CatalogApiError)) console.error("Unexpected catalog request failure");
      });
    return () => controller.abort();
  }, [apiBaseUrl, query, reloadKey]);

  useEffect(() => {
    if (selectedId === null) return;
    const controller = new AbortController();
    void fetchPaperDetail(fetch, apiBaseUrl, selectedId, controller.signal)
      .then((paper) => {
        setDetail(paper);
        setDetailState("ready");
      })
      .catch(() => {
        if (!controller.signal.aborted) setDetailState("error");
      });
    return () => controller.abort();
  }, [apiBaseUrl, selectedId]);

  const pageNumber = useMemo(
    () => Math.floor(query.offset / query.limit) + 1,
    [query.limit, query.offset],
  );
  const pageCount = page ? Math.max(1, Math.ceil(page.total / page.limit)) : 1;

  function submitSearch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setLoadState("loading");
    setQuery((current) => ({ ...current, q: draftQuery.trim(), offset: 0 }));
  }

  function updateFilter(field: keyof Pick<CatalogQuery, "topic" | "source" | "publishedFrom" | "publishedTo" | "sort">, value: string) {
    setLoadState("loading");
    setQuery((current) => ({ ...current, [field]: value, offset: 0 }));
  }

  function clearFilters() {
    setDraftQuery("");
    setLoadState("loading");
    setQuery(INITIAL_QUERY);
    setReloadKey((current) => current + 1);
  }

  function openDetail(paperId: string) {
    setDetail(null);
    setDetailState("loading");
    setSelectedId(paperId);
    window.history.replaceState(null, "", `#explore/${encodeURIComponent(paperId)}`);
  }

  function closeDetail() {
    setSelectedId(null);
    setDetail(null);
    window.history.replaceState(null, "", "#explore");
  }

  async function syncResearch() {
    setSyncState({ kind: "running" });
    try {
      const result = await syncLatestResearch(fetch, apiBaseUrl);
      if (result.ingestion.status !== "succeeded") {
        setSyncState({ kind: "error", message: "The source run finished with a failure." });
        return;
      }
      setSyncState({ kind: "success", result });
      setLoadState("loading");
      setQuery((current) => ({ ...current, offset: 0 }));
      setReloadKey((current) => current + 1);
    } catch {
      setSyncState({ kind: "error", message: "Sync could not reach the local discovery service." });
    }
  }

  return (
    <main className="explore-main">
      <section className="explore-intro" aria-labelledby="explore-heading">
        <div>
          <p className="eyebrow cyan">Knowledge base / Stored research</p>
          <h2 id="explore-heading">Explore papers worth understanding.</h2>
          <p>Search canonical metadata and inspect what is stored locally—without loading a model.</p>
        </div>
        <button
          className="sync-button"
          type="button"
          onClick={() => void syncResearch()}
          disabled={syncState.kind === "running"}
        >
          <span className={syncState.kind === "running" ? "sync-icon spinning" : "sync-icon"} aria-hidden="true">↻</span>
          {syncState.kind === "running" ? "Syncing arXiv…" : "Sync latest research"}
        </button>
      </section>

      {syncState.kind === "success" && (
        <div className="sync-result success" role="status">
          Sync complete: {syncState.result.ingestion.records_seen} fetched · {syncState.result.records_normalized} normalized · {syncState.result.ingestion.records_created} new
        </div>
      )}
      {syncState.kind === "error" && (
        <div className="sync-result error" role="alert">{syncState.message}</div>
      )}

      <section className="catalog-toolbar" aria-label="Catalog search and filters">
        <form className="search-form" onSubmit={submitSearch} role="search">
          <span aria-hidden="true">⌕</span>
          <label className="sr-only" htmlFor="catalog-search">Search stored papers</label>
          <input
            id="catalog-search"
            type="search"
            value={draftQuery}
            onChange={(event) => setDraftQuery(event.target.value)}
            placeholder="Search titles, abstracts, authors, topics…"
            maxLength={200}
          />
          <button type="submit">Search</button>
        </form>
        <div className="filter-grid">
          <label>
            <span>Topic</span>
            <select value={query.topic} onChange={(event) => updateFilter("topic", event.target.value)}>
              <option value="">All topics</option>
              {filters.topics.map((topic) => <option value={topic.key} key={topic.key}>{topic.name}</option>)}
            </select>
          </label>
          <label>
            <span>Source</span>
            <select value={query.source} onChange={(event) => updateFilter("source", event.target.value)}>
              <option value="">All sources</option>
              {filters.sources.map((source) => <option value={source.key} key={source.key}>{source.name}</option>)}
            </select>
          </label>
          <label>
            <span>From</span>
            <input type="date" value={query.publishedFrom} onChange={(event) => updateFilter("publishedFrom", event.target.value)} />
          </label>
          <label>
            <span>To</span>
            <input type="date" value={query.publishedTo} onChange={(event) => updateFilter("publishedTo", event.target.value)} />
          </label>
          <label>
            <span>Sort</span>
            <select value={query.sort} onChange={(event) => updateFilter("sort", event.target.value)}>
              <option value="newest">Newest first</option>
              <option value="oldest">Oldest first</option>
              <option value="updated">Recently updated</option>
              <option value="title">Title A–Z</option>
            </select>
          </label>
          <button className="clear-button" type="button" onClick={clearFilters}>Clear filters</button>
        </div>
      </section>

      <section className={selectedId ? "catalog-layout with-detail" : "catalog-layout"}>
        <div className="catalog-results">
          <div className="results-heading">
            <div>
              <p className="eyebrow">Research catalog</p>
              <h3>{page ? `${page.total} stored ${page.total === 1 ? "paper" : "papers"}` : "Stored papers"}</h3>
            </div>
            {page && page.total > 0 && <span>Page {pageNumber} of {pageCount}</span>}
          </div>

          {loadState === "loading" && <LoadingCards />}
          {loadState === "error" && (
            <div className="state-panel error-panel" role="alert">
              <span aria-hidden="true">!</span>
              <h3>Catalog unavailable</h3>
              <p>Confirm the local API is running, then retry this request.</p>
              <button type="button" onClick={() => {
                setLoadState("loading");
                setReloadKey((current) => current + 1);
              }}>Try again</button>
            </div>
          )}
          {loadState === "empty" && (
            <div className="state-panel empty-panel">
              <span aria-hidden="true">○</span>
              <h3>No papers match this view</h3>
              <p>Adjust the search or filters, or sync the latest arXiv metadata.</p>
              <button type="button" onClick={clearFilters}>Reset search</button>
            </div>
          )}
          {loadState === "ready" && page && (
            <div className="paper-list">
              {page.items.map((paper) => {
                const externalUrl = safeExternalUrl(paper.external_url);
                return (
                  <article className={selectedId === paper.id ? "paper-card selected" : "paper-card"} key={paper.id}>
                    <div className="paper-meta-row">
                      <span className="source-badge">{paper.source_name}</span>
                      <span>{formatDate(paper.published_at)}</span>
                      <span>{paper.current_version}</span>
                    </div>
                    <button className="paper-title" type="button" onClick={() => openDetail(paper.id)}>
                      {paper.title}
                    </button>
                    <p className="paper-authors">{authorLine(paper)}</p>
                    <p className="paper-abstract">{paper.abstract ?? "No abstract supplied by the source."}</p>
                    <div className="paper-footer">
                      <div className="topic-row">
                        {paper.topics.slice(0, 3).map((topic) => <span className="topic-chip" key={topic.key}>{topic.name}</span>)}
                      </div>
                      <div className="paper-actions">
                        <button type="button" onClick={() => openDetail(paper.id)}>Details</button>
                        {externalUrl && (
                          <a href={externalUrl} target="_blank" rel="noreferrer noopener" aria-label={`Open ${paper.title} on arXiv`}>
                            Source <span aria-hidden="true">↗</span>
                          </a>
                        )}
                      </div>
                    </div>
                    {paper.match_reason && <p className="match-reason">Matched by {paper.match_reason}</p>}
                  </article>
                );
              })}
            </div>
          )}

          {page && page.total > 0 && loadState === "ready" && (
            <nav className="pagination" aria-label="Catalog pagination">
              <button
                type="button"
                disabled={query.offset === 0}
                onClick={() => {
                  setLoadState("loading");
                  setQuery((current) => ({ ...current, offset: Math.max(0, current.offset - current.limit) }));
                }}
              >
                ← Previous
              </button>
              <span>{pageNumber} / {pageCount}</span>
              <button
                type="button"
                disabled={!page.has_more}
                onClick={() => {
                  setLoadState("loading");
                  setQuery((current) => ({ ...current, offset: current.offset + current.limit }));
                }}
              >
                Next →
              </button>
            </nav>
          )}
        </div>
        {selectedId && <PaperDetail paper={detail} state={detailState} onClose={closeDetail} />}
      </section>
    </main>
  );
}
