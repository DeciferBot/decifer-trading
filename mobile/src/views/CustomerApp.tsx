"use client";
// M11B.4 — Customer Market Intelligence App
// 4-tab experience: Today | Theme Map | Signals | Universe
// No internal trading concepts. No BottomNav. No operator views.

import { useState, useCallback, useEffect } from "react";
import { RefreshCw, AlertCircle } from "lucide-react";
import {
  fetchMarketNow,
  type MarketNowPayload,
  type RadarItem,
  type TtgSymbolCard,
} from "@/lib/customerApi";
import TodayTab from "./TodayTab";
import ThemeMapTab from "./ThemeMapTab";
import SignalsTab from "./SignalsTab";
import UniverseTab from "./UniverseTab";
import NameDetailPanel from "./NameDetailPanel";
import SymbolDetailSheet from "./SymbolDetailSheet";

type Tab = "today" | "themes" | "signals" | "universe";

const TABS: { id: Tab; label: string }[] = [
  { id: "today",    label: "Today"     },
  { id: "themes",   label: "Theme Map" },
  { id: "signals",  label: "Signals"   },
  { id: "universe", label: "Universe"  },
];

function FreshnessPill({ payload }: { payload: MarketNowPayload }) {
  const ts = payload.freshness_timestamp;
  const conf = (payload.confidence_label ?? "").toLowerCase();

  const getState = (): "fresh" | "delayed" | "stale" => {
    if (!ts || conf.includes("insufficient") || conf.includes("degraded")) return "stale";
    const ageMin = (Date.now() - new Date(ts).getTime()) / 60_000;
    if (isNaN(ageMin) || ageMin > 120) return "stale";
    if (ageMin > 30) return "delayed";
    return "fresh";
  };

  const state = getState();
  const styles = {
    fresh:   { color: "#10b981", dot: "bg-emerald-400 animate-pulse", label: "Fresh"   },
    delayed: { color: "#f59e0b", dot: "bg-amber-400",                 label: "Delayed" },
    stale:   { color: "#ef4444", dot: "bg-rose-400",                  label: "Stale"   },
  };
  const s = styles[state];

  return (
    <span className="flex items-center gap-1.5 text-[10px] font-semibold" style={{ color: s.color }}>
      <span className={`w-1.5 h-1.5 rounded-full ${s.dot}`} />
      {s.label}
    </span>
  );
}

function LoadingSkeleton() {
  return (
    <div className="flex-1 px-4 pt-4 space-y-3 animate-pulse">
      {[1, 2, 3, 4].map(i => (
        <div key={i} className="rounded-2xl" style={{ height: i === 1 ? "7rem" : "5rem", background: "rgba(255,255,255,0.04)" }} />
      ))}
    </div>
  );
}

export default function CustomerApp() {
  const [activeTab, setActiveTab]     = useState<Tab>("today");
  const [data, setData]               = useState<MarketNowPayload | null>(null);
  const [loading, setLoading]         = useState(true);
  const [error, setError]             = useState<string | null>(null);
  const [selectedTheme, setSelectedTheme]   = useState<string | null>(null);
  const [selectedName, setSelectedName]     = useState<RadarItem | null>(null);
  const [selectedSymbol, setSelectedSymbol] = useState<TtgSymbolCard | null>(null);

  const load = useCallback(async () => {
    try {
      const payload = await fetchMarketNow();
      setData(payload);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Unable to load market intelligence.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
    const t = setInterval(load, 5 * 60_000);
    return () => clearInterval(t);
  }, [load]);

  const goToTheme = useCallback((themeId: string) => {
    setSelectedTheme(themeId);
    setActiveTab("themes");
  }, []);

  const handleTabChange = useCallback((tab: Tab) => {
    setActiveTab(tab);
    if (tab !== "themes") setSelectedTheme(null);
  }, []);

  return (
    <div className="flex flex-col min-h-screen" style={{ background: "#0c1427" }}>

      {/* ── Header ────────────────────────────────────────────────────────── */}
      <header
        className="shrink-0 px-5 pb-3"
        style={{ paddingTop: "max(env(safe-area-inset-top), 1.25rem)" }}
      >
        <div className="flex items-start justify-between gap-3">
          <div>
            <div className="flex items-baseline gap-2">
              <span className="text-sm font-black tracking-[0.15em] uppercase" style={{ color: "#f97316" }}>
                DECIFER
              </span>
              <span className="text-[10px] font-medium tracking-wider uppercase text-slate-500">
                Market Intelligence
              </span>
            </div>
            <p className="text-[11px] text-slate-500 mt-0.5">
              Signals, themes, and evidence. Not financial advice.
            </p>
          </div>
          <div className="shrink-0 pt-1">
            {loading
              ? <RefreshCw size={13} className="text-slate-600 animate-spin" />
              : data
                ? <FreshnessPill payload={data} />
                : null}
          </div>
        </div>
      </header>

      {/* ── Tab navigation ────────────────────────────────────────────────── */}
      <nav className="shrink-0 px-4 pb-3" style={{ borderBottom: "1px solid rgba(255,255,255,0.06)" }}>
        <div
          className="flex gap-1 p-1 rounded-xl"
          style={{ background: "rgba(255,255,255,0.05)" }}
        >
          {TABS.map(tab => {
            const active = activeTab === tab.id;
            return (
              <button
                key={tab.id}
                onClick={() => handleTabChange(tab.id)}
                className="flex-1 py-2 rounded-lg text-[11px] font-semibold tracking-wide transition-all duration-150"
                style={active ? { background: "#f97316", color: "#fff" } : { color: "#64748b" }}
              >
                {tab.label}
              </button>
            );
          })}
        </div>
      </nav>

      {/* ── Non-fatal error banner ─────────────────────────────────────────── */}
      {error && data && (
        <div
          className="mx-4 mb-2 px-3 py-2 rounded-lg flex items-center gap-2"
          style={{ background: "rgba(245,158,11,0.08)", border: "1px solid rgba(245,158,11,0.2)" }}
        >
          <AlertCircle size={11} className="text-amber-400 shrink-0" />
          <p className="text-[10px] text-amber-400">Showing cached data — refresh pending.</p>
        </div>
      )}

      {/* ── Content ───────────────────────────────────────────────────────── */}
      <main className="flex-1 overflow-y-auto">
        {loading && <LoadingSkeleton />}

        {error && !data && !loading && (
          <div className="px-4 pt-4">
            <div
              className="rounded-2xl p-8 flex flex-col items-center gap-4 text-center"
              style={{ background: "rgba(255,255,255,0.04)", border: "1px solid rgba(239,68,68,0.2)" }}
            >
              <AlertCircle size={28} className="text-rose-400" />
              <div>
                <p className="text-sm text-slate-200 font-semibold mb-1">Intelligence temporarily unavailable</p>
                <p className="text-xs text-slate-500">{error}</p>
              </div>
              <button
                onClick={load}
                className="px-5 py-2 rounded-full text-[11px] font-bold text-white"
                style={{ background: "#f97316" }}
              >
                Try again
              </button>
            </div>
          </div>
        )}

        {data && !loading && activeTab === "today" && (
          <TodayTab
            data={data}
            onThemeSelect={goToTheme}
            onGoToThemeMap={() => handleTabChange("themes")}
            onGoToUniverse={() => handleTabChange("universe")}
          />
        )}
        {data && !loading && activeTab === "themes" && (
          <ThemeMapTab
            data={data}
            selectedTheme={selectedTheme}
            onThemeSelect={setSelectedTheme}
            onNameSelect={setSelectedName}
            onGoToUniverseTheme={(ttgId) => {
              setSelectedTheme(ttgId);
              handleTabChange("universe");
            }}
          />
        )}
        {data && !loading && activeTab === "signals" && (
          <SignalsTab data={data} onThemeSelect={goToTheme} />
        )}
        {data && !loading && activeTab === "universe" && (
          <UniverseTab
            data={data}
            onNameSelect={setSelectedName}
            onThemeSelect={goToTheme}
            onSymbolSelect={setSelectedSymbol}
          />
        )}
      </main>

      {/* ── Name detail overlay ───────────────────────────────────────────── */}
      {selectedName && (
        <NameDetailPanel
          name={selectedName}
          data={data}
          onClose={() => setSelectedName(null)}
        />
      )}

      {/* ── Symbol deep-dive sheet ────────────────────────────────────────── */}
      {selectedSymbol && (
        <SymbolDetailSheet
          card={selectedSymbol}
          onClose={() => setSelectedSymbol(null)}
        />
      )}

      {/* ── Footer disclaimer ─────────────────────────────────────────────── */}
      <footer
        className="shrink-0 px-4 py-2.5 text-center"
        style={{
          borderTop: "1px solid rgba(255,255,255,0.05)",
          paddingBottom: "max(env(safe-area-inset-bottom), 0.625rem)",
        }}
      >
        <p className="text-[9px] text-slate-700">
          Market intelligence only · Not financial advice · No trade execution
        </p>
      </footer>
    </div>
  );
}
