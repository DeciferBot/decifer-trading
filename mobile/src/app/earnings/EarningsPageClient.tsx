"use client";
// Standalone earnings calendar page — mobile.decifertrading.com/earnings
// Fetches its own data, renders EarningsCalendarView without an overlay wrapper.

import { useState, useEffect } from "react";
import EarningsCalendarView from "@/views/EarningsCalendarView";
import type { EarningsEntry } from "@/app/api/earnings-calendar/route";
import { fetchTtgThemes, fetchTtgThemeDetail, fetchUniverseSymbols, type TtgThemeDetail } from "@/lib/customerApi";

// Build the TTG symbol map the same way TodayTab does
async function buildTtgSymbolMap(): Promise<Map<string, { theme_label: string }>> {
  const symbolMap = new Map<string, { theme_label: string }>();
  const [themesResult, rosterResult] = await Promise.allSettled([
    fetchTtgThemes(),
    fetchUniverseSymbols(),
  ]);
  const roster = rosterResult.status === "fulfilled" ? rosterResult.value : [];
  for (const u of roster) symbolMap.set(u.symbol, { theme_label: u.theme_label });

  const themes = themesResult.status === "fulfilled" ? themesResult.value : [];
  if (themes.length > 0) {
    try {
      const allDetails = await Promise.allSettled(
        themes.map((t: { theme_id: string }) => fetchTtgThemeDetail(t.theme_id))
      );
      for (const result of allDetails) {
        if (result.status !== "fulfilled" || !result.value) continue;
        const detail = result.value as TtgThemeDetail;
        const label: string = detail.label ?? detail.theme_id ?? "";
        for (const sym of (detail.symbols ?? [])) {
          if (sym && sym.symbol) symbolMap.set(sym.symbol, { theme_label: label });
        }
      }
    } catch { /* graceful */ }
  }
  return symbolMap;
}

export default function EarningsPage() {
  const [earnings, setEarnings] = useState<EarningsEntry[]>([]);
  const [ttgMap, setTtgMap] = useState<Map<string, { theme_label: string }>>(new Map());
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    Promise.allSettled([
      fetch("/api/earnings-calendar").then(r => r.ok ? r.json() : null),
      buildTtgSymbolMap(),
    ]).then(([earningsResult, mapResult]) => {
      if (cancelled) return;
      if (earningsResult.status === "fulfilled" && earningsResult.value?.earnings) {
        setEarnings(earningsResult.value.earnings);
      }
      if (mapResult.status === "fulfilled") {
        setTtgMap(mapResult.value);
      }
      setLoading(false);
    });
    return () => { cancelled = true; };
  }, []);

  if (loading) {
    return (
      <div className="fixed inset-0 flex flex-col items-center justify-center" style={{ background: "#080d15" }}>
        <div className="w-8 h-8 rounded-full border-2 border-t-transparent animate-spin"
          style={{ borderColor: "rgba(249,115,22,0.3)", borderTopColor: "#f97316" }} />
        <p className="text-[12px] mt-3" style={{ color: "#475569" }}>Loading calendar…</p>
      </div>
    );
  }

  return <EarningsCalendarView earnings={earnings} ttgSymbolMap={ttgMap} />;
}
