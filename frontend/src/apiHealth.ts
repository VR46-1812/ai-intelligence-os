export type ApiHealthState = "loading" | "healthy" | "unavailable";

interface HealthResponse {
  service: string;
  status: "ok";
}

interface HealthHttpResponse {
  readonly ok: boolean;
  json(): Promise<unknown>;
}

export type HealthFetcher = (
  url: string,
  options: {
    headers: { Accept: "application/json" };
    signal: AbortSignal;
  },
) => Promise<HealthHttpResponse>;

export const INITIAL_API_HEALTH_STATE: ApiHealthState = "loading";

function isHealthResponse(value: unknown): value is HealthResponse {
  if (typeof value !== "object" || value === null) {
    return false;
  }

  const candidate = value as Record<string, unknown>;
  return candidate.service === "ai-intelligence-os" && candidate.status === "ok";
}

export async function resolveApiHealthState(
  fetcher: HealthFetcher,
  healthUrl: string,
  signal: AbortSignal,
): Promise<Exclude<ApiHealthState, "loading">> {
  try {
    const response = await fetcher(healthUrl, {
      headers: { Accept: "application/json" },
      signal,
    });

    if (!response.ok) {
      return "unavailable";
    }

    const payload = await response.json();
    return isHealthResponse(payload) ? "healthy" : "unavailable";
  } catch {
    return "unavailable";
  }
}
