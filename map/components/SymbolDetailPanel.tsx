"use client";

import { X } from "lucide-react";
import type { Chain } from "@/lib/chain-definitions";
import type { GraphData } from "@/lib/types";

interface Props {
  symbol: string;
  chainId: string;
  chains: Chain[];
  graphData: GraphData | null;
  prices: Record<string, { price: number; change_pct: number }>;
  allNodeLabels: Record<string, string>;
  onSelect: (symbol: string, chainId: string) => void;
  onClose: () => void;
}

function findSymbolChain(symbol: string, chains: Chain[]): { chain: Chain; stageIdx: number } | null {
  for (const chain of chains) {
    const idx = chain.stages.findIndex(s => s.symbols.includes(symbol));
    if (idx !== -1) return { chain, stageIdx: idx };
  }
  return null;
}

function PriceChip({ pct }: { pct?: number }) {
  if (pct === undefined) return null;
  if (pct === 0) return <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-white/5 text-gray-500">0.00%</span>;
  const pos = pct > 0;
  return (
    <span className={`text-[10px] px-1.5 py-0.5 rounded-full font-semibold ${pos ? "bg-emerald-500/15 text-emerald-400" : "bg-red-500/15 text-red-400"}`}>
      {pos ? "+" : ""}{pct.toFixed(2)}%
    </span>
  );
}

interface RelatedSymbol {
  symbol: string;
  chainId: string;
  chainColor: string;
  stageLabel: string;
  relation: "supplier" | "customer" | "competitor";
}

export default function SymbolDetailPanel({ symbol, chainId, chains, graphData, prices, allNodeLabels, onSelect, onClose }: Props) {
  const chain = chains.find(c => c.id === chainId) ?? chains[0];
  const stageIdx = chain.stages.findIndex(s => s.symbols.includes(symbol));
  const stageName = stageIdx !== -1 ? chain.stages[stageIdx].label : "Unknown";
  const price = prices[symbol];
  const label = allNodeLabels[symbol] ?? symbol;

  const edges = graphData?.edges ?? [];

  const seenSuppliers = new Set<string>();
  const seenCustomers = new Set<string>();
  const seenCompetitors = new Set<string>();

  const related: RelatedSymbol[] = [];

  const addRelated = (sym: string, relation: "supplier" | "customer" | "competitor", seen: Set<string>) => {
    if (sym === symbol || seen.has(sym)) return;
    seen.add(sym);
    const loc = findSymbolChain(sym, chains);
    related.push({
      symbol: sym,
      chainId: loc?.chain.id ?? chainId,
      chainColor: loc?.chain.color ?? chain.color,
      stageLabel: loc ? loc.chain.stages[loc.stageIdx].label : "",
      relation,
    });
  };

  if (stageIdx > 0) {
    for (const sym of chain.stages[stageIdx - 1].symbols) {
      addRelated(sym, "supplier", seenSuppliers);
    }
  }
  if (stageIdx !== -1 && stageIdx < chain.stages.length - 1) {
    for (const sym of chain.stages[stageIdx + 1].symbols) {
      addRelated(sym, "customer", seenCustomers);
    }
  }
  for (const sym of (stageIdx !== -1 ? chain.stages[stageIdx].symbols : [])) {
    if (sym !== symbol) addRelated(sym, "competitor", seenCompetitors);
  }

  for (const e of edges) {
    if (e.type === "supply_chain_up" && e.target === symbol) addRelated(e.source as string, "supplier", seenSuppliers);
    if (e.type === "customer" && e.source === symbol) addRelated(e.target as string, "customer", seenCustomers);
    if (e.type === "competition") {
      if (e.source === symbol) addRelated(e.target as string, "competitor", seenCompetitors);
      if (e.target === symbol) addRelated(e.source as string, "competitor", seenCompetitors);
    }
  }

  const suppliers = related.filter(r => r.relation === "supplier");
  const customers = related.filter(r => r.relation === "customer");
  const competitors = related.filter(r => r.relation === "competitor");

  function RelatedList({ items, title, arrow }: { items: RelatedSymbol[]; title: string; arrow: string }) {
    if (items.length === 0) return null;
    return (
      <div className="mb-4">
        <div className="text-[9px] uppercase tracking-widest text-gray-600 mb-1.5 flex items-center gap-1.5">
          <span style={{ color: chain.color }}>{arrow}</span>
          {title}
        </div>
        <div className="space-y-1">
          {items.map(item => (
            <button
              key={item.symbol}
              onClick={() => onSelect(item.symbol, item.chainId)}
              className="w-full text-left flex items-center gap-2 px-2 py-1.5 rounded-lg hover:bg-white/5 transition-colors"
            >
              <div className="w-2 h-2 rounded-full flex-shrink-0" style={{ background: item.chainColor }} />
              <span className="text-[12px] font-bold font-mono text-white flex-shrink-0">{item.symbol}</span>
              <span className="text-[10px] text-gray-500 truncate flex-1">{allNodeLabels[item.symbol] ?? item.symbol}</span>
              {prices[item.symbol] && <PriceChip pct={prices[item.symbol].change_pct} />}
            </button>
          ))}
        </div>
      </div>
    );
  }

  return (
    <div className="h-full flex flex-col overflow-hidden" style={{ background: "#050810", borderLeft: "1px solid rgba(255,255,255,0.08)", width: 340, flexShrink: 0 }}>
      <div className="p-4 border-b border-white/8 flex-shrink-0">
        <div className="flex items-start justify-between mb-2">
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 mb-0.5">
              <span className="text-xl font-bold text-white font-mono leading-none">{symbol}</span>
              {price && <PriceChip pct={price.change_pct} />}
            </div>
            <div className="text-[11px] text-gray-400 truncate">{label}</div>
            {price && (
              <div className="text-[11px] text-gray-500 mt-0.5">${price.price.toFixed(2)}</div>
            )}
          </div>
          <button onClick={onClose} className="text-gray-600 hover:text-gray-300 mt-0.5 flex-shrink-0">
            <X size={14} />
          </button>
        </div>
        <div className="flex items-center gap-2 mt-2">
          <div className="w-2 h-2 rounded-full flex-shrink-0" style={{ background: chain.color }} />
          <span className="text-[10px] text-gray-500">{chain.label}</span>
          <span className="text-[10px] text-gray-700">·</span>
          <span className="text-[10px]" style={{ color: chain.color + "cc" }}>{stageName}</span>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto p-4">
        <RelatedList items={suppliers} title="Suppliers" arrow="←" />
        <RelatedList items={customers} title="Customers" arrow="→" />
        <RelatedList items={competitors} title="Same stage" arrow="≈" />
        {suppliers.length === 0 && customers.length === 0 && competitors.length === 0 && (
          <div className="text-[11px] text-gray-600 mt-2">No related symbols found.</div>
        )}
      </div>
    </div>
  );
}
