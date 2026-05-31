"use client";

import { useState, useEffect, FormEvent, useMemo } from "react";
import { useRouter } from "next/navigation";

interface UniverseSymbol {
  symbol: string;
  label: string;
  theme_id: string;
  theme_label: string;
  exposure_type: string;
  confidence: number;
  driver_id: string;
  driver_active: boolean;
  in_feed: boolean;
  conviction_score: number;
  conviction_tier: "high" | "medium" | "watchlist";
}

const TIER_STYLES: Record<string, { card: string; score: string; label: string }> = {
  high: {
    card: "border-emerald-800 bg-emerald-950/20 hover:border-emerald-600",
    score: "text-emerald-400",
    label: "High conviction",
  },
  medium: {
    card: "border-amber-800/60 bg-amber-950/10 hover:border-amber-600",
    score: "text-amber-400",
    label: "Building",
  },
  watchlist: {
    card: "border-border bg-surface hover:border-accent/50",
    score: "text-text-muted",
    label: "Watchlist",
  },
};

function SymbolCard({ s, onClick }: { s: UniverseSymbol; onClick: () => void }) {
  const tier = TIER_STYLES[s.conviction_tier] ?? TIER_STYLES.watchlist;
  return (
    <button
      onClick={onClick}
      className={`w-full text-left border rounded-sm p-4 transition-colors group ${tier.card}`}
    >
      <div className="flex items-start justify-between gap-2 mb-1.5">
        <span className="font-mono text-base text-accent group-hover:text-orange-400 transition-colors leading-none">
          {s.symbol}
        </span>
        <span className={`font-mono text-sm font-semibold ${tier.score}`}>
          {s.conviction_score}
        </span>
      </div>
      <p className="text-text text-xs leading-snug mb-2.5 line-clamp-1">{s.label}</p>
      <div className="flex items-center justify-between gap-2">
        <span className={`font-mono text-[9px] tracking-widest uppercase ${tier.score}`}>
          {tier.label}
        </span>
        {s.driver_active && (
          <span className="font-mono text-[9px] text-emerald-400 border border-emerald-800 bg-emerald-950 rounded-sm px-1.5 py-0.5 tracking-wide">
            ACTIVE
          </span>
        )}
      </div>
    </button>
  );
}

type SortMode = "conviction" | "alpha";

export default function HomePage() {
  const router = useRouter();
  const [query, setQuery] = useState("");
  const [activeTheme, setActiveTheme] = useState<string | null>(null);
  const [sort, setSort] = useState<SortMode>("conviction");
  const [symbols, setSymbols] = useState<UniverseSymbol[] | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetch("/api/universe")
      .then((r) => r.json())
      .then((d) => setSymbols(d.symbols ?? []))
      .catch(() => setSymbols([]))
      .finally(() => setLoading(false));
  }, []);

  const themes = useMemo(() => {
    if (!symbols) return [];
    const map = new Map<string, string>();
    for (const s of symbols) {
      if (s.theme_id && !map.has(s.theme_id)) map.set(s.theme_id, s.theme_label);
    }
    return Array.from(map.entries()).map(([id, label]) => ({ id, label }));
  }, [symbols]);

  const filtered = useMemo(() => {
    if (!symbols) return [];
    const q = query.trim().toUpperCase();
    const list = symbols.filter((s) => {
      const matchesTheme = !activeTheme || s.theme_id === activeTheme;
      const matchesQuery = !q || s.symbol.includes(q) || s.label.toUpperCase().includes(q);
      return matchesTheme && matchesQuery;
    });
    if (sort === "conviction") {
      return [...list].sort((a, b) => b.conviction_score - a.conviction_score);
    }
    return [...list].sort((a, b) => a.symbol.localeCompare(b.symbol));
  }, [symbols, activeTheme, query, sort]);

  function handleSearch(e: FormEvent) {
    e.preventDefault();
    const clean = query.trim().toUpperCase();
    if (clean) router.push(`/${clean}`);
  }

  const counts = useMemo(() => {
    if (!symbols) return { high: 0, medium: 0, watchlist: 0 };
    return {
      high: symbols.filter((s) => s.conviction_tier === "high").length,
      medium: symbols.filter((s) => s.conviction_tier === "medium").length,
      watchlist: symbols.filter((s) => s.conviction_tier === "watchlist").length,
    };
  }, [symbols]);

  return (
    <div className="max-w-4xl mx-auto px-4 py-10">
      {/* Header */}
      <div className="fade-up mb-8">
        <h1
          className="text-5xl mb-2 leading-tight"
          style={{ fontFamily: "'Instrument Serif', serif", fontStyle: "italic" }}
        >
          Symbol Intelligence
        </h1>
        <p className="text-text-muted text-sm font-mono tracking-wide">
          Theme membership · Conviction score · Macro context
        </p>
      </div>

      {/* Conviction summary strip */}
      {!loading && symbols && (
        <div className="fade-up fade-up-1 grid grid-cols-3 gap-3 mb-8">
          {[
            { tier: "high", label: "High conviction", count: counts.high, color: "text-emerald-400 border-emerald-800 bg-emerald-950/20" },
            { tier: "medium", label: "Building", count: counts.medium, color: "text-amber-400 border-amber-800/60 bg-amber-950/10" },
            { tier: "watchlist", label: "Watchlist", count: counts.watchlist, color: "text-text-muted border-border bg-surface" },
          ].map(({ tier, label, count, color }) => (
            <button
              key={tier}
              onClick={() => setActiveTheme(null)}
              className={`border rounded-sm p-3 text-left transition-opacity ${color}`}
            >
              <p className={`font-mono text-2xl font-semibold ${color.split(" ")[0]}`}>{count}</p>
              <p className="font-mono text-xs text-text-muted mt-0.5">{label}</p>
            </button>
          ))}
        </div>
      )}

      {/* Search */}
      <div className="fade-up fade-up-1 mb-6">
        <form onSubmit={handleSearch} className="relative">
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value.toUpperCase())}
            placeholder="Search by ticker or company name"
            maxLength={50}
            autoFocus
            className="w-full bg-surface border border-border rounded-sm px-5 py-4 text-text font-mono text-base tracking-widest placeholder:text-text-muted placeholder:text-sm placeholder:tracking-wide focus:outline-none focus:border-accent transition-colors"
          />
          <button
            type="submit"
            disabled={!query.trim()}
            className="absolute right-3 top-1/2 -translate-y-1/2 px-4 py-2 bg-accent text-background font-mono text-xs tracking-widest uppercase rounded-sm disabled:opacity-30 disabled:cursor-not-allowed hover:bg-orange-400 transition-colors"
          >
            Look up
          </button>
        </form>
      </div>

      {/* Theme filters */}
      {themes.length > 0 && (
        <div className="fade-up fade-up-2 mb-6 flex flex-wrap gap-2">
          <button
            onClick={() => setActiveTheme(null)}
            className={`font-mono text-xs tracking-wide px-3 py-1.5 rounded-sm border transition-colors ${
              activeTheme === null
                ? "bg-accent text-background border-accent"
                : "border-border text-text-muted hover:border-accent hover:text-accent"
            }`}
          >
            All ({symbols?.length ?? 0})
          </button>
          {themes.map((t) => {
            const count = symbols?.filter((s) => s.theme_id === t.id).length ?? 0;
            return (
              <button
                key={t.id}
                onClick={() => setActiveTheme(activeTheme === t.id ? null : t.id)}
                className={`font-mono text-xs tracking-wide px-3 py-1.5 rounded-sm border transition-colors ${
                  activeTheme === t.id
                    ? "bg-accent text-background border-accent"
                    : "border-border text-text-muted hover:border-accent hover:text-accent"
                }`}
              >
                {t.label} ({count})
              </button>
            );
          })}
        </div>
      )}

      {/* Grid controls */}
      {!loading && filtered.length > 0 && (
        <div className="fade-up fade-up-2 flex items-center justify-between mb-3">
          <p className="font-mono text-xs text-text-muted">
            {filtered.length} symbol{filtered.length !== 1 ? "s" : ""}
            {activeTheme ? ` in ${themes.find((t) => t.id === activeTheme)?.label}` : ""}
          </p>
          <div className="flex gap-1">
            {(["conviction", "alpha"] as SortMode[]).map((m) => (
              <button
                key={m}
                onClick={() => setSort(m)}
                className={`font-mono text-xs px-2 py-1 rounded-sm border transition-colors ${
                  sort === m
                    ? "bg-accent text-background border-accent"
                    : "border-border text-text-muted hover:border-accent hover:text-accent"
                }`}
              >
                {m === "conviction" ? "By score" : "A-Z"}
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Symbol grid */}
      <div className="fade-up fade-up-3">
        {loading ? (
          <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 gap-3">
            {Array.from({ length: 12 }).map((_, i) => (
              <div key={i} className="border border-border rounded-sm p-4 animate-pulse bg-surface h-24" />
            ))}
          </div>
        ) : filtered.length === 0 ? (
          <div className="text-center py-16">
            <p className="font-mono text-sm text-text-muted mb-3">No symbols match</p>
            <button
              onClick={() => { setQuery(""); setActiveTheme(null); }}
              className="font-mono text-xs text-accent border border-accent rounded-sm px-4 py-2 hover:bg-accent hover:text-background transition-colors"
            >
              Clear filters
            </button>
          </div>
        ) : (
          <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 gap-3">
            {filtered.map((s) => (
              <SymbolCard key={s.symbol} s={s} onClick={() => router.push(`/${s.symbol}`)} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
