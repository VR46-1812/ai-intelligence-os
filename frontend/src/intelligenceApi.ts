export interface TopicPaper { readonly work_id: string; readonly title: string; readonly score: number; }
export interface TopicOverview { readonly key: string; readonly label: string; readonly paper_count: number; readonly daily_change: number; readonly papers: readonly TopicPaper[]; }
export interface Opportunity { readonly kind: "engineering" | "commercial"; readonly work_id: string; readonly title: string; readonly headline: string; readonly detail: string; readonly evidence_ids: readonly string[]; readonly confidence: number; }

export class IntelligenceApiError extends Error {}

function record(value: unknown): value is Record<string, unknown> { return typeof value === "object" && value !== null; }
function topic(value: unknown): value is TopicOverview {
  return record(value) && typeof value.key === "string" && typeof value.label === "string" &&
    typeof value.paper_count === "number" && typeof value.daily_change === "number" && Array.isArray(value.papers) &&
    value.papers.every((paper) => record(paper) && typeof paper.work_id === "string" && typeof paper.title === "string" && typeof paper.score === "number");
}
function opportunity(value: unknown): value is Opportunity {
  return record(value) && (value.kind === "engineering" || value.kind === "commercial") &&
    typeof value.work_id === "string" && typeof value.title === "string" && typeof value.headline === "string" &&
    typeof value.detail === "string" && typeof value.confidence === "number" && Array.isArray(value.evidence_ids) &&
    value.evidence_ids.every((id) => typeof id === "string");
}
async function request(fetcher: typeof fetch, url: string, signal: AbortSignal): Promise<unknown> {
  const response = await fetcher(url, { signal, headers: { Accept: "application/json" } });
  if (!response.ok) throw new IntelligenceApiError("Local intelligence output is unavailable.");
  try { return await response.json(); } catch { throw new IntelligenceApiError("The local API returned unreadable data."); }
}
export async function fetchTopics(fetcher: typeof fetch, base: string, signal: AbortSignal): Promise<readonly TopicOverview[]> {
  const value = await request(fetcher, `${base}/topics/overview`, signal);
  if (!Array.isArray(value) || !value.every(topic)) throw new IntelligenceApiError("Topic data was invalid.");
  return value;
}
export async function fetchOpportunities(fetcher: typeof fetch, base: string, signal: AbortSignal): Promise<readonly Opportunity[]> {
  const value = await request(fetcher, `${base}/opportunities`, signal);
  if (!Array.isArray(value) || !value.every(opportunity)) throw new IntelligenceApiError("Opportunity data was invalid.");
  return value;
}
