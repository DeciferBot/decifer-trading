"use client";
// M13B — Customer journey refactor.
// 5-tab experience: Today | Forces | Ask Decifer | Themes | Names
// Hamburger menu for secondary areas.
// Uses shared useCustomerBriefing hook.
// No operator views. No execution, broker, or account logic.

import { useState, useCallback } from "react";
import { Menu, X, AlertCircle, RefreshCw, ChevronRight } from "lucide-react";
import type { RadarItem, TtgSymbolCard } from "@/lib/customerApi";
import {
  useCustomerBriefing,
  type FreshnessState,
} from "@/lib/useCustomerBriefing";
import CustomerBottomNav, { type CustomerTab } from "@/components/CustomerBottomNav";
import TodayTab from "./TodayTab";
import ThemeMapTab from "./ThemeMapTab";
import ForcesTab from "./ForcesTab";
import UniverseTab from "./UniverseTab";
import AskDeciferView from "./AskDeciferView";
import NameDetailPanel from "./NameDetailPanel";
import SymbolDetailSheet from "./SymbolDetailSheet";

// ── Freshness badge ───────────────────────────────────────────────────────────

const FRESHNESS_COLORS: Record<FreshnessState, { dot: string; text: string }> = {
  fresh:         { dot: "bg-emerald-400 animate-pulse", text: "#10b981" },
  updating:      { dot: "bg-blue-400",                  text: "#60a5fa" },
  stale:         { dot: "bg-rose-400",                  text: "#f87171" },
  market_closed: { dot: "bg-slate-500",                 text: "#6b7280" },
  unavailable:   { dot: "bg-slate-600",                 text: "#475569" },
};

function FreshnessBadge({ state, label }: { state: FreshnessState; label: string }) {
  const c = FRESHNESS_COLORS[state];
  return (
    <span className="flex items-center gap-1.5 text-[10px] font-semibold" style={{ color: c.text }}>
      <span className={`w-1.5 h-1.5 rounded-full ${c.dot}`} />
      {label}
    </span>
  );
}

// ── Loading skeleton ──────────────────────────────────────────────────────────

function LoadingSkeleton() {
  return (
    <div className="flex-1 px-4 pt-4 space-y-3 animate-pulse">
      {[96, 64, 64, 48].map((h, i) => (
        <div
          key={i}
          className="rounded-2xl"
          style={{ height: `${h}px`, background: "rgba(255,255,255,0.04)" }}
        />
      ))}
    </div>
  );
}

// ── Hamburger menu (bottom sheet) ─────────────────────────────────────────────

interface HamburgerMenuProps {
  onClose: () => void;
  onNavigate: (tab: CustomerTab) => void;
  freshnessLabel: string;
  freshnessState: FreshnessState;
}

const MENU_ITEMS: {
  label: string;
  sublabel: string;
  tab?: CustomerTab;
}[] = [
  {
    label: "Market Forces",
    sublabel: "Active and dormant forces moving market attention today",
    tab: "forces",
  },
  {
    label: "Theme Map",
    sublabel: "Full structural theme map and driver connections",
    tab: "themes",
  },
  {
    label: "Full Universe",
    sublabel: "Complete connected names catalogue",
    tab: "names",
  },
  {
    label: "Methodology",
    sublabel: "How Decifer intelligence is built from real market signals",
  },
  {
    label: "Data Freshness",
    sublabel: "Intelligence is refreshed every 5 minutes during market hours",
  },
  {
    label: "Disclaimers",
    sublabel: "Market intelligence only. Not financial advice. No trade execution.",
  },
  {
    label: "About Decifer",
    sublabel: "Paper trading intelligence platform",
  },
];

function HamburgerMenu({
  onClose,
  onNavigate,
  freshnessLabel,
  freshnessState,
}: HamburgerMenuProps) {
  return (
    <div
      className="fixed inset-0 z-50 flex flex-col justify-end"
      style={{ background: "rgba(0,0,0,0.65)" }}
      onClick={onClose}
    >
      <div
        className="w-full rounded-t-3xl overflow-hidden"
        style={{ background: "#141b26", border: "1px solid rgba(255,255,255,0.08)" }}
        onClick={(e) => e.stopPropagation()}
      >
        {/* Drag handle */}
        <div className="flex justify-center pt-3 pb-1">
          <div className="w-10 h-1 rounded-full" style={{ background: "rgba(255,255,255,0.1)" }} />
        </div>

        {/* Header row */}
        <div className="px-5 pt-2 pb-3 flex items-center justify-between">
          <div>
            <span className="text-[11px] font-black tracking-[0.15em] uppercase" style={{ color: "#f97316" }}>
              DECIFER
            </span>
            <span className="text-[10px] text-slate-500 ml-2">Menu</span>
          </div>
          <button
            onClick={onClose}
            className="p-1.5 rounded-full transition-all active:scale-90"
            style={{ background: "rgba(255,255,255,0.05)" }}
            aria-label="Close menu"
          >
            <X size={15} className="text-slate-400" />
          </button>
        </div>

        {/* Items */}
        <div className="px-4 pb-4 space-y-1">
          {MENU_ITEMS.map((item) => (
            <button
              key={item.label}
              onClick={() => {
                if (item.tab) onNavigate(item.tab);
                onClose();
              }}
              className="w-full flex items-center gap-3 rounded-xl px-4 py-3 text-left transition-all active:scale-[0.98]"
              style={{ background: "rgba(255,255,255,0.03)" }}
            >
              <div className="flex-1 min-w-0">
                <p className="text-[13px] font-semibold text-slate-200">{item.label}</p>
                <p className="text-[11px] text-slate-500 mt-0.5 leading-relaxed">{item.sublabel}</p>
              </div>
              {item.tab && <ChevronRight size={13} className="text-slate-600 shrink-0" />}
            </button>
          ))}
        </div>

        {/* Footer */}
        <div
          className="px-5 py-3 flex items-center justify-between"
          style={{ borderTop: "1px solid rgba(255,255,255,0.05)" }}
        >
          <p className="text-[9px] text-slate-700">
            Market intelligence only · Not financial advice · No trade execution
          </p>
          <FreshnessBadge state={freshnessState} label={freshnessLabel} />
        </div>

        {/* Safe-area spacer */}
        <div style={{ height: "max(env(safe-area-inset-bottom), 0.25rem)" }} />
      </div>
    </div>
  );
}

// ── Main shell ────────────────────────────────────────────────────────────────

export default function CustomerApp() {
  const {
    data,
    loading,
    error,
    isRefreshing,
    story,
    clock,
    freshnessState,
    freshnessLabel,
    sinceAway,
    ttgThemes,
    activeForces,
    dormantForces,
    connectionTree,
    refresh,
  } = useCustomerBriefing();

  const [activeTab, setActiveTab] = useState<CustomerTab>("today");
  const [selectedTheme, setSelectedTheme] = useState<string | null>(null);
  const [selectedName, setSelectedName] = useState<RadarItem | null>(null);
  const [selectedSymbol, setSelectedSymbol] = useState<TtgSymbolCard | null>(null);
  const [askContext, setAskContext] = useState<string | null>(null);
  const [menuOpen, setMenuOpen] = useState(false);

  const goToTheme = useCallback((themeId: string) => {
    setSelectedTheme(themeId);
    setActiveTab("themes");
  }, []);

  const handleTabChange = useCallback((tab: CustomerTab) => {
    setActiveTab(tab);
    if (tab !== "themes") setSelectedTheme(null);
    if (tab !== "ask") setAskContext(null);
  }, []);

  const handleAskAbout = useCallback((context: string) => {
    setAskContext(context);
    setActiveTab("ask");
  }, []);

  const sessionColor =
    clock.session === "open"
      ? "#10b981"
      : clock.session === "pre_market" || clock.session === "after_hours"
        ? "#f59e0b"
        : "#475569";

  return (
    <div className="flex flex-col h-[100dvh]" style={{ background: "#0c1117" }}>

      {/* ── Header ───────────────────────────────────────────────────────────── */}
      <header
        className="shrink-0 px-5 pb-3"
        style={{
          paddingTop: "max(env(safe-area-inset-top), 1rem)",
          borderBottom: "1px solid rgba(255,255,255,0.05)",
          background: "#0c1117",
        }}
      >
        <div className="flex items-start justify-between gap-3">

          {/* Branding + greeting */}
          <div>
            <div className="flex items-baseline gap-2 mb-1">
              <span className="text-sm font-black tracking-[0.15em] uppercase" style={{ color: "#f97316" }}>
                DECIFER
              </span>
              <span className="text-[10px] font-semibold tracking-wider uppercase text-slate-400">
                Market Intelligence
              </span>
              <span className="text-[9px] text-slate-500">
                v{process.env.NEXT_PUBLIC_APP_VERSION}
              </span>
            </div>
            {/* suppressHydrationWarning: clock values are computed from new Date()
                — server (UTC) and client (local timezone) always differ */}
            <p className="text-[13px] font-medium text-slate-200 leading-snug" suppressHydrationWarning>
              {clock.greeting}. Here is your market briefing.
            </p>
            <div className="flex items-center gap-2 mt-1 flex-wrap">
              <span className="text-[11px] text-slate-400" suppressHydrationWarning>{clock.localTime}</span>
              <span className="text-[10px] text-slate-600">·</span>
              <span className="text-[11px] text-slate-400" suppressHydrationWarning>
                {clock.newYorkTime} <span className="text-slate-500">ET</span>
              </span>
              <span className="text-[10px] text-slate-600">·</span>
              <span className="text-[11px] font-semibold" style={{ color: sessionColor }} suppressHydrationWarning>
                {clock.sessionLabel}
              </span>
            </div>
          </div>

          {/* Freshness + hamburger */}
          <div className="flex items-center gap-2 shrink-0 pt-0.5">
            {loading || isRefreshing ? (
              <RefreshCw size={12} className="text-slate-600 animate-spin" />
            ) : (
              <FreshnessBadge state={freshnessState} label={freshnessLabel} />
            )}
            <button
              onClick={() => setMenuOpen(true)}
              className="p-1.5 rounded-lg transition-all active:scale-90"
              style={{ background: "rgba(255,255,255,0.05)" }}
              aria-label="Open menu"
            >
              <Menu size={15} className="text-slate-400" />
            </button>
          </div>
        </div>

        {/* Stale: show refresh row */}
        {freshnessState === "stale" && !loading && (
          <button
            onClick={refresh}
            disabled={isRefreshing}
            className="mt-2 w-full flex items-center justify-center gap-1.5 py-1.5 rounded-xl text-[10px] font-semibold transition-all active:scale-[0.98]"
            style={{
              background: "rgba(239,68,68,0.07)",
              color: "#f87171",
              border: "1px solid rgba(239,68,68,0.15)",
            }}
          >
            <RefreshCw size={10} className={isRefreshing ? "animate-spin" : ""} />
            {isRefreshing ? "Refreshing your briefing..." : "Refresh view"}
          </button>
        )}
      </header>

      {/* ── Cached-data warning ──────────────────────────────────────────────── */}
      {error && data && (
        <div
          className="mx-4 mt-2 px-3 py-2 rounded-lg flex items-center gap-2"
          style={{
            background: "rgba(245,158,11,0.06)",
            border: "1px solid rgba(245,158,11,0.16)",
          }}
        >
          <AlertCircle size={11} className="text-amber-400 shrink-0" />
          <p className="text-[10px] text-amber-400">
            Showing cached briefing — refresh pending.
          </p>
        </div>
      )}

      {/* ── Content area ─────────────────────────────────────────────────────── */}
      <main className="flex-1 overflow-y-auto">
        {loading && <LoadingSkeleton />}

        {/* Fatal error (no cached data) */}
        {error && !data && !loading && (
          <div className="px-4 pt-4">
            <div
              className="rounded-2xl p-8 flex flex-col items-center gap-4 text-center"
              style={{
                background: "rgba(255,255,255,0.03)",
                border: "1px solid rgba(239,68,68,0.18)",
              }}
            >
              <AlertCircle size={28} className="text-rose-400" />
              <div>
                <p className="text-sm text-slate-200 font-semibold mb-1">
                  Intelligence temporarily unavailable
                </p>
                <p className="text-xs text-slate-500">{error}</p>
              </div>
              <button
                onClick={refresh}
                className="px-5 py-2 rounded-full text-[11px] font-bold text-white transition-all active:scale-95"
                style={{ background: "#f97316" }}
              >
                Try again
              </button>
            </div>
          </div>
        )}

        {/* Ask Decifer — available without data */}
        {activeTab === "ask" && (
          <AskDeciferView onAskContext={askContext} data={data} />
        )}

        {/* Data-dependent tabs */}
        {data && !loading && activeTab === "today" && (
          <TodayTab
            data={data}
            story={story}
            clock={clock}
            sinceAway={sinceAway}
            freshnessState={freshnessState}
            freshnessLabel={freshnessLabel}
            isRefreshing={isRefreshing}
            onRefresh={refresh}
            onThemeSelect={goToTheme}
            onAskAbout={handleAskAbout}
            onGoToDiscover={() => handleTabChange("themes")}
            onGoToUniverse={() => handleTabChange("names")}
            onGoToForces={() => handleTabChange("forces")}
          />
        )}

        {data && !loading && activeTab === "forces" && (
          <ForcesTab
            data={data}
            activeForces={activeForces}
            dormantForces={dormantForces}
            connectionTree={connectionTree}
            onThemeSelect={goToTheme}
            onAskAbout={handleAskAbout}
            onGoToNames={() => handleTabChange("names")}
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
              handleTabChange("names");
            }}
          />
        )}

        {data && !loading && activeTab === "names" && (
          <UniverseTab
            data={data}
            onNameSelect={setSelectedName}
            onThemeSelect={goToTheme}
            onSymbolSelect={setSelectedSymbol}
            onAskAbout={handleAskAbout}
          />
        )}
      </main>

      {/* ── Overlays ─────────────────────────────────────────────────────────── */}
      {selectedName && (
        <NameDetailPanel
          name={selectedName}
          data={data}
          onClose={() => setSelectedName(null)}
        />
      )}
      {selectedSymbol && (
        <SymbolDetailSheet
          card={selectedSymbol}
          onClose={() => setSelectedSymbol(null)}
        />
      )}

      {/* ── Bottom navigation ────────────────────────────────────────────────── */}
      <CustomerBottomNav activeTab={activeTab} onTabChange={handleTabChange} />

      {/* ── Hamburger menu ───────────────────────────────────────────────────── */}
      {menuOpen && (
        <HamburgerMenu
          onClose={() => setMenuOpen(false)}
          onNavigate={(tab) => {
            handleTabChange(tab);
            setMenuOpen(false);
          }}
          freshnessLabel={freshnessLabel}
          freshnessState={freshnessState}
        />
      )}
    </div>
  );
}
