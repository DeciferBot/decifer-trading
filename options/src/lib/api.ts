import type { FeedResponse, LeaderboardResponse, SymbolResponse } from "./types";

const BASE = process.env.NEXT_PUBLIC_INTELLIGENCE_API_URL ?? "";

async function fetchApi<T>(path: string): Promise<T | null> {
  try {
    const res = await fetch(`${BASE}${path}`, { cache: "no-store" });
    if (!res.ok) return null;
    return res.json() as Promise<T>;
  } catch {
    return null;
  }
}

export const getFeed = (params?: { limit?: number; side?: string; signal?: string }) => {
  const q = new URLSearchParams();
  if (params?.limit) q.set("limit", String(params.limit));
  if (params?.side) q.set("side", params.side);
  if (params?.signal) q.set("signal", params.signal);
  const qs = q.toString();
  return fetchApi<FeedResponse>(`/api/options/feed${qs ? `?${qs}` : ""}`);
};

export const getLeaderboard = (params?: { limit?: number; driver?: string }) => {
  const q = new URLSearchParams();
  if (params?.limit) q.set("limit", String(params.limit));
  if (params?.driver) q.set("driver", params.driver);
  const qs = q.toString();
  return fetchApi<LeaderboardResponse>(`/api/options/leaderboard${qs ? `?${qs}` : ""}`);
};

export const getSymbol = (ticker: string) =>
  fetchApi<SymbolResponse>(`/api/options/symbol/${ticker.toUpperCase()}`);
