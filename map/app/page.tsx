"use client";

import { useEffect, useState, useCallback } from "react";
import { Activity } from "lucide-react";
import type { GraphData } from "@/lib/types";
import { CHAINS, EXTRA_SYMBOL_LABELS } from "@/lib/chain-definitions";
import SupplyChainView from "@/components/SupplyChainView";
import SymbolDetailPanel from "@/components/SymbolDetailPanel";

export default function MapPage() {
  const [activeChain, setActiveChain] = useState("ai_infrastructure");
  const [selectedSymbol, setSelectedSymbol] = useState<{ symbol: string; chainId: string } | null>(null);
  const [prices, setPrices] = useState<Record<string, { price: number; change_pct: number }>>({});
  const [graphData, setGraphData] = useState<GraphData | null>(null);

  const chain = CHAINS.find(c => c.id === activeChain) ?? CHAINS[0];

  const allNodeLabels: Record<string, string> = {
    ...EXTRA_SYMBOL_LABELS,
    ...(graphData ? Object.fromEntries(graphData.nodes.map(n => [n.id, n.label])) : {}),
  };

  useEffect(() => {
    fetch("/api/graph").then(r => r.json()).then(setGraphData).catch(() => {});
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
    setSelectedSymbol({ symbol, chainId: chainId ?? activeChain });
  }

  function handleChainChange(id: string) {
    setActiveChain(id);
    setSelectedSymbol(null);
  }

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
            onClose={() => setSelectedSymbol(null)}
          />
        )}
      </div>
    </div>
  );
}
