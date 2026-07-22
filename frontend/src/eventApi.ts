import type { LinkedSourceEvidence } from "./catalogApi";

export interface LinkedEvent {
  readonly id: string;
  readonly title: string;
  readonly primary_work_id: string | null;
  readonly occurred_at: string | null;
  readonly corroboration: number;
  readonly source_count: number;
  readonly classification: "artifact" | "associated_event" | "corroborated_event";
  readonly corroboration_status: "single_source" | "associated" | "corroborated";
  readonly association_confidence: number;
  readonly linkage_reason: string;
  readonly sources: readonly LinkedSourceEvidence[];
}

export interface LinkedEventPage {
  readonly items: readonly LinkedEvent[];
  readonly total: number;
  readonly has_more: boolean;
}

export class EventApiError extends Error {}

function record(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function source(value: unknown): boolean {
  return record(value) && typeof value.artifact_id === "string" && typeof value.source_key === "string" &&
    typeof value.canonical_url === "string" && typeof value.relationship === "string" &&
    typeof value.content_class === "string" && typeof value.authority === "number";
}

function event(value: unknown): value is LinkedEvent {
  return record(value) && typeof value.id === "string" && typeof value.title === "string" &&
    (typeof value.primary_work_id === "string" || value.primary_work_id === null) &&
    (typeof value.occurred_at === "string" || value.occurred_at === null) &&
    typeof value.corroboration === "number" && typeof value.source_count === "number" &&
    typeof value.classification === "string" && typeof value.corroboration_status === "string" &&
    typeof value.association_confidence === "number" && typeof value.linkage_reason === "string" &&
    Array.isArray(value.sources) && value.sources.every(source);
}

export async function fetchLinkedEvents(fetcher: typeof fetch, base: string, signal: AbortSignal): Promise<LinkedEventPage> {
  const response = await fetcher(`${base}/events?limit=20&offset=0`, { signal, headers: { Accept: "application/json" } });
  if (!response.ok) throw new EventApiError("Linked-source events are unavailable.");
  const value: unknown = await response.json();
  if (!record(value) || !Array.isArray(value.items) || !value.items.every(event) ||
      typeof value.total !== "number" || typeof value.has_more !== "boolean") {
    throw new EventApiError("Linked-source event data was invalid.");
  }
  return value as unknown as LinkedEventPage;
}
