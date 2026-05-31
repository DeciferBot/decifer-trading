"use client";

import { useEffect, useState, useCallback, useRef } from "react";
import { Activity, Search, X as XIcon } from "lucide-react";
import type { GraphData } from "@/lib/types";
import { CHAINS, EXTRA_SYMBOL_LABELS } from "@/lib/chain-definitions";
import SupplyChainView from "@/components/SupplyChainView";
import SymbolDetailPanel from "@/components/SymbolDetailPanel";

interface HistoryEntry { symbol: string; chainId: string }

interface SearchResult {
  symbol: string;
  chainId: string;
  chainLabel: string;
  chainColor: string;
  stageLabel: string;
  companyName: string;
}

export default function MapPage() {
  const [activeChain, setActiveChain] = useState("ai_infrastructure");
  const [selectedSymbol, setSelectedSymbol] = useState<{ symbol: string; chainId: string } | null>(null);
  const [history, setHistory] = useState<HistoryEntry[]>([]);
  const [prices, setPrices] = useState<Record<string, { price: number; change_pct: number }>>({});
  const [graphData, setGraphData] = useState<GraphData | null>(null);
  const [activeCandidates, setActiveCandidates] = useState<Set<string>>(new Set());

  // Search state
  const [searchOpen, setSearchOpen] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const searchInputRef = useRef<HTMLInputElement>(null);
  const searchContainerRef = useRef<HTMLDivElement>(null);

  const chain = CHAINS.find(c => c.id === activeChain) ?? CHAINS[0];

  const allNodeLabels: Record<string, string> = {
    ...EXTRA_SYMBOL_LABELS,
    ...(graphData ? Object.fromEntries(graphData.nodes.map(n => [n.id, n.label])) : {}),
  };

  useEffect(() => {
    fetch("/api/graph").then(r => r.json()).then(setGraphData).catch(() => {});
  }, []);

  useEffect(() => {
    fetch("/api/intelligence")
      .then(r => r.json())
      .then(data => {
        const symbols: string[] = data.active_candidate_symbols ?? [];
        setActiveCandidates(new Set(symbols));
      })
      .catch(() => {});
  }, []);

  const loadPrices = useCallback(async (c: typeof chain) => {
    const symbols = [...new Set(c.stages.flatMap(s => s.symbols))];
    if (symbols.length === 0) return;
    try {
      const { prices: p } = await fetch(`/api/prices?symbols=${symbols.join(",")}`).then(r => r.json());
      setPrices(p ?? {});
    } catch {
      setPrices({});
    }
  }, []);

  useEffect(() => {
    setPrices({});
    loadPrices(chain);
    const t = setInterval(() => loadPrices(chain), 120_000);
    return () => clearInterval(t);
  }, [activeChain, loadPrices, chain]);

  function handleSymbolSelect(symbol: string, chainId?: string) {
    const resolvedChainId = chainId ?? activeChain;
    // Push current selection to history before navigating
    if (selectedSymbol) {
      setHistory(prev => [...prev, { symbol: selectedSymbol.symbol, chainId: selectedSymbol.chainId }]);
    }
    setSelectedSymbol({ symbol, chainId: resolvedChainId });
    // Switch active chain to context chain
    setActiveChain(resolvedChainId);
  }

  function handleBack() {
    setHistory(prev => {
      if (prev.length === 0) return prev;
      const next = [...prev];
      const last = next.pop()!;
      setSelectedSymbol({ symbol: last.symbol, chainId: last.chainId });
      setActiveChain(last.chainId);
      return next;
    });
  }

  function handleChainChange(id: string) {
    setActiveChain(id);
    setSelectedSymbol(null);
    setHistory([]);
  }

  // Search logic
  const searchResults: SearchResult[] = (() => {
    const q = searchQuery.trim().toLowerCase();
    if (!q) return [];
    const results: SearchResult[] = [];
    const seen = new Set<string>();
    for (const c of CHAINS) {
      for (const stage of c.stages) {
        for (const sym of stage.symbols) {
          const key = `${sym}:${c.id}`;
          if (seen.has(key)) continue;
          const companyName = allNodeLabels[sym] ?? sym;
          if (sym.toLowerCase().includes(q) || companyName.toLowerCase().includes(q)) {
            seen.add(key);
            results.push({
              symbol: sym,
              chainId: c.id,
              chainLabel: c.label,
              chainColor: c.color,
              stageLabel: stage.label,
              companyName,
            });
          }
          if (results.length >= 12) break;
        }
        if (results.length >= 12) break;
      }
      if (results.length >= 12) break;
    }
    return results;
  })();

  function handleSearchSelect(result: SearchResult) {
    setSearchOpen(false);
    setSearchQuery("");
    setActiveChain(result.chainId);
    handleSymbolSelect(result.symbol, result.chainId);
  }

  // Close search on outside click
  useEffect(() => {
    if (!searchOpen) return;
    function onPointerDown(e: PointerEvent) {
      if (searchContainerRef.current && !searchContainerRef.current.contains(e.target as Node)) {
        setSearchOpen(false);
        setSearchQuery("");
      }
    }
    document.addEventListener("pointerdown", onPointerDown);
    return () => document.removeEventListener("pointerdown", onPointerDown);
  }, [searchOpen]);

  // Close on ESC
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") {
        setSearchOpen(false);
        setSearchQuery("");
      }
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  // Focus input when search opens
  useEffect(() => {
    if (searchOpen && searchInputRef.current) {
      searchInputRef.current.focus();
    }
  }, [searchOpen]);

  const showPanel = selectedSymbol !== null;

  return (
    <div className="h-screen w-screen flex flex-col overflow-hidden" style={{ background: "#080d1a", color: "#fff" }}>
      <header className="flex items-center gap-3 px-4 py-2.5 border-b border-white/8 flex-shrink-0">
        <Activity size={15} className="text-indigo-400 flex-shrink-0" />
        <span className="font-semibold text-white tracking-tight text-sm">Decifer</span>
        <div className="w-px h-4 bg-white/10 mx-1" />
        <div className="flex items-center gap-1 overflow-x-auto flex-1 no-scrollbar">
          {CHAINS.map(c => (
            <button
              key={c.id}
              onClick={() => handleChainChange(c.id)}
              className={`flex-shrink-0 text-[11px] px-3 py-1.5 rounded-full transition-all border ${
                activeChain === c.id
                  ? "border-opacity-50 font-semibold"
                  : "border-transparent text-gray-500 hover:text-gray-300 hover:bg-white/5"
              }`}
              style={activeChain === c.id ? {
                color: c.color,
                borderColor: c.color + "66",
                backgroundColor: c.color + "15",
              } : {}}
            >
              {c.label}
            </button>
          ))}
        </div>

        {/* Search */}
        <div ref={searchContainerRef} className="relative flex-shrink-0">
          {searchOpen ? (
            <div className="flex items-center gap-1">
              <input
                ref={searchInputRef}
                value={searchQuery}
                onChange={e => setSearchQuery(e.target.value)}
                placeholder="Search symbols…"
                className="text-[11px] px-2 py-1 rounded-lg outline-none"
                style={{
                  width: 200,
                  background: "rgba(255,255,255,0.07)",
                  border: "1px solid rgba(255,255,255,0.12)",
                  color: "#fff",
                }}
              />
              <button
                onClick={() => { setSearchOpen(false); setSearchQuery(""); }}
                className="p-1 hover:text-white"
                style={{ color: "rgba(255,255,255,0.30)" }}
              >
                <XIcon size={12} />
              </button>
            </div>
          ) : (
            <button
              onClick={() => setSearchOpen(true)}
              className="p-1.5 rounded-lg hover:bg-white/5 transition-all"
              style={{ color: "rgba(255,255,255,0.40)" }}
              title="Search symbols"
            >
              <Search size={14} />
            </button>
          )}

          {/* Search dropdown */}
          {searchOpen && searchQuery.trim().length > 0 && (
            <div
              className="absolute right-0 top-full mt-1 rounded-xl overflow-y-auto"
              style={{
                zIndex: 50,
                background: "#04080f",
                border: "1px solid rgba(255,255,255,0.10)",
                maxHeight: 400,
                width: 280,
              }}
            >
              {searchResults.length === 0 ? (
                <div className="px-4 py-3 text-[11px]" style={{ color: "rgba(255,255,255,0.35)" }}>
                  No results
                </div>
              ) : (
                searchResults.map((r, i) => (
                  <button
                    key={`${r.symbol}-${r.chainId}-${i}`}
                    onClick={() => handleSearchSelect(r)}
                    className="w-full text-left flex items-center gap-3 px-3 py-2.5 transition-all hover:bg-white/5"
                    style={{ borderBottom: i < searchResults.length - 1 ? "1px solid rgba(255,255,255,0.05)" : "none" }}
                  >
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 mb-0.5">
                        <span className="text-[12px] font-bold font-mono text-white">{r.symbol}</span>
                        <span
                          className="text-[8px] px-1.5 py-0.5 rounded"
                          style={{ color: r.chainColor, background: r.chainColor + "18" }}
                        >
                          {r.stageLabel}
                        </span>
                      </div>
                      <div className="text-[10px] truncate" style={{ color: "rgba(255,255,255,0.40)" }}>
                        {r.companyName}
                      </div>
                      <div className="text-[9px] mt-0.5" style={{ color: r.chainColor + "99" }}>
                        {r.chainLabel}
                      </div>
                    </div>
                  </button>
                ))
              )}
            </div>
          )}
        </div>

        <div className="flex items-center gap-1 px-2 py-1 rounded-full bg-emerald-500/10 border border-emerald-500/20 flex-shrink-0">
          <div className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />
          <span className="text-xs text-emerald-400">Live</span>
        </div>
      </header>

      <div className="flex flex-1 overflow-hidden">
        <div className="flex-1 overflow-hidden">
          <SupplyChainView
            chain={chain}
            selectedSymbol={selectedSymbol?.symbol ?? null}
            prices={prices}
            graphData={graphData}
            onSelect={sym => handleSymbolSelect(sym)}
            allNodeLabels={allNodeLabels}
            activeCandidates={activeCandidates}
          />
        </div>

        {showPanel && selectedSymbol && (
          <SymbolDetailPanel
            symbol={selectedSymbol.symbol}
            chainId={selectedSymbol.chainId}
            chains={CHAINS}
            graphData={graphData}
            prices={prices}
            allNodeLabels={allNodeLabels}
            onSelect={(sym, cid) => handleSymbolSelect(sym, cid)}
            onClose={() => { setSelectedSymbol(null); setHistory([]); }}
            onBack={handleBack}
            hasHistory={history.length > 0}
          />
        )}
      </div>
    </div>
  );
}
