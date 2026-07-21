export interface CatalogAuthor {
  readonly display_name: string;
  readonly order: number;
  readonly orcid: string | null;
}

export interface CatalogIdentity {
  readonly id_type: "doi" | "arxiv" | "openreview" | "github" | "huggingface" | "url" | "other";
  readonly value: string;
  readonly external_url: string | null;
}

export interface CatalogTopic {
  readonly key: string;
  readonly name: string;
}

export interface CatalogPaper {
  readonly id: string;
  readonly title: string;
  readonly abstract: string | null;
  readonly publication_status: "unknown" | "preprint" | "submitted" | "accepted" | "published" | "withdrawn";
  readonly published_at: string | null;
  readonly submitted_at: string | null;
  readonly arxiv_announced_at: string | null;
  readonly locally_ingested_at: string;
  readonly updated_at: string;
  readonly current_version: string;
  readonly authors: readonly CatalogAuthor[];
  readonly identities: readonly CatalogIdentity[];
  readonly topics: readonly CatalogTopic[];
  readonly source_key: string;
  readonly source_name: string;
  readonly external_url: string | null;
  readonly match_reason: string | null;
  readonly document_status: string;
  readonly evidence_count: number;
  readonly ranking: {
    readonly technical: number | null;
    readonly commercial: number | null;
    readonly deep_dive_priority: number | null;
    readonly technical_components: Readonly<Record<string, number>>;
    readonly calculated_at: string | null;
  };
}

export interface EvidenceItem {
  readonly id: string;
  readonly document_id: string;
  readonly source_url: string;
  readonly media_type: string;
  readonly document_sha256: string;
  readonly section_path: string | null;
  readonly page_start: number | null;
  readonly page_end: number | null;
  readonly span_text: string;
  readonly created_at: string;
}

export interface EvidencePage {
  readonly items: readonly EvidenceItem[];
  readonly total: number;
  readonly limit: number;
  readonly offset: number;
  readonly has_more: boolean;
}

export interface CatalogPaperPage {
  readonly items: readonly CatalogPaper[];
  readonly total: number;
  readonly limit: number;
  readonly offset: number;
  readonly has_more: boolean;
}

export interface CatalogFilterOptions {
  readonly topics: readonly CatalogTopic[];
  readonly sources: readonly { readonly key: string; readonly name: string }[];
}

export interface CatalogQuery {
  readonly q: string;
  readonly topic: string;
  readonly source: string;
  readonly publishedFrom: string;
  readonly publishedTo: string;
  readonly sort: "newest" | "oldest" | "title" | "updated" | "technical" | "commercial" | "deep_dive";
  readonly limit: number;
  readonly offset: number;
}

export interface SyncResult {
  readonly ingestion: {
    readonly status: "queued" | "running" | "succeeded" | "failed" | "cancelled" | "deferred";
    readonly records_seen: number;
    readonly records_created: number;
    readonly duplicate_records: number;
  };
  readonly records_normalized: number;
  readonly records_rejected: number;
}

type Fetcher = typeof fetch;

export class CatalogApiError extends Error {
  constructor(message = "The local research catalog could not be loaded.") {
    super(message);
    this.name = "CatalogApiError";
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function isStringOrNull(value: unknown): value is string | null {
  return typeof value === "string" || value === null;
}

function isAuthor(value: unknown): value is CatalogAuthor {
  return (
    isRecord(value) &&
    typeof value.display_name === "string" &&
    typeof value.order === "number" &&
    isStringOrNull(value.orcid)
  );
}

function isIdentity(value: unknown): value is CatalogIdentity {
  return (
    isRecord(value) &&
    typeof value.id_type === "string" &&
    typeof value.value === "string" &&
    isStringOrNull(value.external_url)
  );
}

function isTopic(value: unknown): value is CatalogTopic {
  return isRecord(value) && typeof value.key === "string" && typeof value.name === "string";
}

function isPaper(value: unknown): value is CatalogPaper {
  if (!isRecord(value)) return false;
  const ranking = value.ranking;
  return (
    typeof value.id === "string" &&
    typeof value.title === "string" &&
    isStringOrNull(value.abstract) &&
    typeof value.publication_status === "string" &&
    isStringOrNull(value.published_at) &&
    isStringOrNull(value.submitted_at) &&
    isStringOrNull(value.arxiv_announced_at) &&
    typeof value.locally_ingested_at === "string" &&
    typeof value.updated_at === "string" &&
    typeof value.current_version === "string" &&
    Array.isArray(value.authors) && value.authors.every(isAuthor) &&
    Array.isArray(value.identities) && value.identities.every(isIdentity) &&
    Array.isArray(value.topics) && value.topics.every(isTopic) &&
    typeof value.source_key === "string" &&
    typeof value.source_name === "string" &&
    isStringOrNull(value.external_url) &&
    isStringOrNull(value.match_reason) &&
    typeof value.document_status === "string" &&
    typeof value.evidence_count === "number" &&
    isRecord(ranking) &&
    (typeof ranking.technical === "number" || ranking.technical === null) &&
    (typeof ranking.commercial === "number" || ranking.commercial === null) &&
    (typeof ranking.deep_dive_priority === "number" || ranking.deep_dive_priority === null) &&
    isRecord(ranking.technical_components) &&
    Object.values(ranking.technical_components).every((item) => typeof item === "number") &&
    isStringOrNull(ranking.calculated_at)
  );
}

function isEvidenceItem(value: unknown): value is EvidenceItem {
  return isRecord(value) && typeof value.id === "string" && typeof value.document_id === "string" &&
    typeof value.source_url === "string" && typeof value.media_type === "string" &&
    typeof value.document_sha256 === "string" && isStringOrNull(value.section_path) &&
    (typeof value.page_start === "number" || value.page_start === null) &&
    (typeof value.page_end === "number" || value.page_end === null) &&
    typeof value.span_text === "string" && typeof value.created_at === "string";
}

function isEvidencePage(value: unknown): value is EvidencePage {
  return isRecord(value) && Array.isArray(value.items) && value.items.every(isEvidenceItem) &&
    typeof value.total === "number" && typeof value.limit === "number" &&
    typeof value.offset === "number" && typeof value.has_more === "boolean";
}

function isPaperPage(value: unknown): value is CatalogPaperPage {
  if (!isRecord(value) || !Array.isArray(value.items)) return false;
  return (
    value.items.every(isPaper) &&
    typeof value.total === "number" &&
    typeof value.limit === "number" &&
    typeof value.offset === "number" &&
    typeof value.has_more === "boolean"
  );
}

function isFilterOptions(value: unknown): value is CatalogFilterOptions {
  return (
    isRecord(value) &&
    Array.isArray(value.topics) &&
    value.topics.every(isTopic) &&
    Array.isArray(value.sources) &&
    value.sources.every(
      (source) =>
        isRecord(source) && typeof source.key === "string" && typeof source.name === "string",
    )
  );
}

function isSyncResult(value: unknown): value is SyncResult {
  return (
    isRecord(value) &&
    isRecord(value.ingestion) &&
    typeof value.ingestion.status === "string" &&
    typeof value.ingestion.records_seen === "number" &&
    typeof value.ingestion.records_created === "number" &&
    typeof value.ingestion.duplicate_records === "number" &&
    typeof value.records_normalized === "number" &&
    typeof value.records_rejected === "number"
  );
}

async function readJson(response: Response): Promise<unknown> {
  if (!response.ok) throw new CatalogApiError();
  try {
    return await response.json();
  } catch {
    throw new CatalogApiError("The local API returned an unreadable response.");
  }
}

export function buildCatalogUrl(apiBaseUrl: string, query: CatalogQuery): string {
  const parameters = new URLSearchParams({
    sort: query.sort,
    limit: String(query.limit),
    offset: String(query.offset),
  });
  if (query.q) parameters.set("q", query.q);
  if (query.topic) parameters.set("topic", query.topic);
  if (query.source) parameters.set("source", query.source);
  if (query.publishedFrom) parameters.set("published_from", query.publishedFrom);
  if (query.publishedTo) parameters.set("published_to", query.publishedTo);
  return `${apiBaseUrl}/items?${parameters.toString()}`;
}

export async function fetchCatalogPage(
  fetcher: Fetcher,
  apiBaseUrl: string,
  query: CatalogQuery,
  signal: AbortSignal,
): Promise<CatalogPaperPage> {
  const payload = await readJson(
    await fetcher(buildCatalogUrl(apiBaseUrl, query), {
      headers: { Accept: "application/json" },
      signal,
    }),
  );
  if (!isPaperPage(payload)) throw new CatalogApiError("The catalog response was invalid.");
  return payload;
}

export async function fetchPaperDetail(
  fetcher: Fetcher,
  apiBaseUrl: string,
  paperId: string,
  signal: AbortSignal,
): Promise<CatalogPaper> {
  const payload = await readJson(
    await fetcher(`${apiBaseUrl}/items/${encodeURIComponent(paperId)}`, {
      headers: { Accept: "application/json" },
      signal,
    }),
  );
  if (!isPaper(payload)) throw new CatalogApiError("The paper response was invalid.");
  return payload;
}

export async function fetchPaperEvidence(
  fetcher: Fetcher,
  apiBaseUrl: string,
  paperId: string,
  signal: AbortSignal,
): Promise<EvidencePage> {
  const payload = await readJson(await fetcher(
    `${apiBaseUrl}/items/${encodeURIComponent(paperId)}/evidence?limit=12&offset=0`,
    { headers: { Accept: "application/json" }, signal },
  ));
  if (!isEvidencePage(payload)) throw new CatalogApiError("The evidence response was invalid.");
  return payload;
}

export async function fetchCatalogFilters(
  fetcher: Fetcher,
  apiBaseUrl: string,
  signal: AbortSignal,
): Promise<CatalogFilterOptions> {
  const payload = await readJson(
    await fetcher(`${apiBaseUrl}/catalog/filters`, {
      headers: { Accept: "application/json" },
      signal,
    }),
  );
  if (!isFilterOptions(payload)) throw new CatalogApiError("The filter response was invalid.");
  return payload;
}

export async function syncLatestResearch(
  fetcher: Fetcher,
  apiBaseUrl: string,
): Promise<SyncResult> {
  const payload = await readJson(
    await fetcher(`${apiBaseUrl}/sources/arxiv/sync`, {
      method: "POST",
      headers: { Accept: "application/json", "Content-Type": "application/json" },
      body: JSON.stringify({ maximum_records: 5, lookback_hours: 168 }),
    }),
  );
  if (!isSyncResult(payload)) throw new CatalogApiError("The sync response was invalid.");
  return payload;
}

export function safeExternalUrl(value: string | null): string | null {
  if (value === null) return null;
  try {
    const url = new URL(value);
    const allowedHosts = new Set(["arxiv.org", "www.arxiv.org", "doi.org", "dx.doi.org"]);
    return url.protocol === "https:" && allowedHosts.has(url.hostname.toLowerCase())
      ? url.toString()
      : null;
  } catch {
    return null;
  }
}
