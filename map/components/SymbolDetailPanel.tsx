"use client";

import { X, ArrowLeft, ArrowRight, Minus } from "lucide-react";
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

interface RelatedSymbol {
  symbol: string;
  chainId: string;
  chainColor: string;
  stageLabel: string;
  relation: "supplier" | "customer" | "competitor";
  edgeLabel?: string;
}

export default function SymbolDetailPanel({ symbol, chainId, chains, graphData, prices, allNodeLabels, onSelect, onClose }: Props) {
  const chain = chains.find(c => c.id === chainId) ?? chains[0];
  const stageIdx = chain.stages.findIndex(s => s.symbols.includes(symbol));
  const stageName = stageIdx !== -1 ? chain.stages[stageIdx].label : "";
  const price = prices[symbol];
  const label = allNodeLabels[symbol] ?? symbol;

  // Description from graph node
  const node = graphData?.nodes.find(n => n.id === symbol);
  const description = node?.description ?? null;

  const edges = graphData?.edges ?? [];

  const seenSuppliers = new Set<string>();
  const seenCustomers = new Set<string>();
  const seenCompetitors = new Set<string>();
  const related: RelatedSymbol[] = [];

  const addRelated = (
    sym: string,
    relation: "supplier" | "customer" | "competitor",
    seen: Set<string>,
    edgeLabel?: string,
  ) => {
    if (sym === symbol || seen.has(sym)) return;
    seen.add(sym);
    const loc = findSymbolChain(sym, chains);
    related.push({
      symbol: sym,
      chainId: loc?.chain.id ?? chainId,
      chainColor: loc?.chain.color ?? chain.color,
      stageLabel: loc ? loc.chain.stages[loc.stageIdx].label : "",
      relation,
      edgeLabel,
    });
  };

  // Structural: adjacent stages in chain
  if (stageIdx > 0) {
    for (const sym of chain.stages[stageIdx - 1].symbols) addRelated(sym, "supplier", seenSuppliers);
  }
  if (stageIdx !== -1 && stageIdx < chain.stages.length - 1) {
    for (const sym of chain.stages[stageIdx + 1].symbols) addRelated(sym, "customer", seenCustomers);
  }
  for (const sym of (stageIdx !== -1 ? chain.stages[stageIdx].symbols : [])) {
    if (sym !== symbol) addRelated(sym, "competitor", seenCompetitors);
  }

  // Graph edges enrich with known explicit relationships
  for (const e of edges) {
    if (e.type === "supply_chain_up" && e.target === symbol)
      addRelated(e.source as string, "supplier", seenSuppliers, e.label as string);
    if (e.type === "customer" && e.source === symbol)
      addRelated(e.target as string, "customer", seenCustomers, e.label as string);
    if (e.type === "competition") {
      if (e.source === symbol) addRelated(e.target as string, "competitor", seenCompetitors, e.label as string);
      if (e.target === symbol) addRelated(e.source as string, "competitor", seenCompetitors, e.label as string);
    }
  }

  const suppliers = related.filter(r => r.relation === "supplier");
  const customers = related.filter(r => r.relation === "customer");
  const competitors = related.filter(r => r.relation === "competitor");

  const pct = price?.change_pct;
  const hasChange = pct !== undefined && pct !== 0;
  const isUp = hasChange && pct! > 0;
  const changeColor = !hasChange ? "#6b7280" : isUp ? "#10b981" : "#ef4444";

  function RelRow({ item }: { item: RelatedSymbol }) {
    const p = prices[item.symbol];
    const pc = p?.change_pct;
    const pcPos = pc && pc > 0;
    return (
      <button
        onClick={() => onSelect(item.symbol, item.chainId)}
        className="w-full text-left flex items-start gap-3 px-3 py-2.5 rounded-lg transition-all hover:bg-white/5"
      >
        <div className="w-1.5 h-1.5 rounded-full mt-1.5 flex-shrink-0" style={{ background: item.chainColor }} />
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-0.5">
            <span className="text-[12px] font-bold font-mono text-white">{item.symbol}</span>
            {item.stageLabel && (
              <span className="text-[9px] px-1.5 py-0.5 rounded" style={{ color: item.chainColor, background: item.chainColor + "18" }}>
                {item.stageLabel}
              </span>
            )}
          </div>
          <div className="text-[10px] text-gray-500 truncate">{allNodeLabels[item.symbol] ?? item.symbol}</div>
          {item.edgeLabel && (
            <div className="text-[9px] text-gray-700 mt-0.5 leading-snug truncate">{item.edgeLabel}</div>
          )}
        </div>
        {p && (
          <div className="text-right flex-shrink-0">
            <div className="text-[10px] text-gray-400 font-mono">${p.price.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</div>
            {pc !== undefined && pc !== 0 && (
              <div className="text-[9px] font-mono" style={{ color: pcPos ? "#10b981" : "#ef4444" }}>
                {pcPos ? "+" : ""}{pc.toFixed(2)}%
              </div>
            )}
          </div>
        )}
      </button>
    );
  }

  function Section({ items, title, icon }: { items: RelatedSymbol[]; title: string; icon: React.ReactNode }) {
    if (items.length === 0) return null;
    return (
      <div className="mb-1">
        <div className="flex items-center gap-2 px-3 py-2">
          <span className="text-gray-600">{icon}</span>
          <span className="text-[9px] uppercase tracking-widest text-gray-600 font-semibold">{title}</span>
          <span className="text-[9px] text-gray-700 ml-auto">{items.length}</span>
        </div>
        {items.map(item => <RelRow key={item.symbol} item={item} />)}
      </div>
    );
  }

  return (
    <div
      className="h-full flex flex-col overflow-hidden"
      style={{ background: "#040810", borderLeft: "1px solid rgba(255,255,255,0.08)", width: 360, flexShrink: 0 }}
    >
      {/* Header */}
      <div className="p-5 border-b border-white/8 flex-shrink-0">
        <div className="flex items-start justify-between mb-3">
          <div className="flex-1 min-w-0">
            <div className="flex items-baseline gap-3 mb-1">
              <span className="text-2xl font-bold text-white font-mono tracking-tight">{symbol}</span>
              {hasChange && (
                <span className="text-sm font-semibold font-mono" style={{ color: changeColor }}>
                  {isUp ? "+" : ""}{pct!.toFixed(2)}%
                </span>
              )}
            </div>
            <div className="text-[13px] text-gray-400 truncate mb-1">{label}</div>
            {price?.price ? (
              <div className="text-[18px] font-mono font-semibold text-white/80">
                ${price.price.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
              </div>
            ) : null}
          </div>
          <button onClick={onClose} className="text-gray-600 hover:text-gray-300 mt-0.5 flex-shrink-0 p-1">
            <X size={14} />
          </button>
        </div>

        {/* Chain + stage breadcrumb */}
        <div className="flex items-center gap-1.5 mt-2">
          <div className="w-1.5 h-1.5 rounded-full flex-shrink-0" style={{ background: chain.color }} />
          <span className="text-[10px]" style={{ color: chain.color + "cc" }}>{chain.label}</span>
          {stageName && (
            <>
              <span className="text-gray-700 text-[10px]">›</span>
              <span className="text-[10px] text-gray-500">{stageName}</span>
            </>
          )}
        </div>
      </div>

      {/* Description */}
      {description && (
        <div className="px-5 py-4 border-b border-white/5 flex-shrink-0">
          <p className="text-[11px] text-gray-400 leading-relaxed">{description}</p>
        </div>
      )}

      {/* Relationships */}
      <div className="flex-1 overflow-y-auto py-2">
        <Section items={suppliers} title="Suppliers" icon={<ArrowLeft size={10} />} />
        <Section items={customers} title="Customers" icon={<ArrowRight size={10} />} />
        <Section items={competitors} title="Same stage" icon={<Minus size={10} />} />
        {suppliers.length === 0 && customers.length === 0 && competitors.length === 0 && (
          <div className="px-5 py-4 text-[11px] text-gray-700">No relationships mapped.</div>
        )}
      </div>
    </div>
  );
}
