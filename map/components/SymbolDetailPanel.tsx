"use client";

import { useState } from "react";
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

function SymbolLogo({ symbol, size = 32 }: { symbol: string; size?: number }) {
  const [failed, setFailed] = useState(false);
  if (failed) {
    return (
      <div
        className="rounded-full flex items-center justify-center font-bold text-white flex-shrink-0"
        style={{ width: size, height: size, fontSize: size * 0.36, background: "rgba(255,255,255,0.10)" }}
      >
        {symbol[0]}
      </div>
    );
  }
  return (
    <img
      src={`https://financialmodelingprep.com/image-stock/${symbol}.png`}
      alt={symbol}
      width={size}
      height={size}
      onError={() => setFailed(true)}
      className="rounded-full object-contain flex-shrink-0"
      style={{ width: size, height: size, background: "rgba(255,255,255,0.06)" }}
    />
  );
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

export default function SymbolDetailPanel({
  symbol, chainId, chains, graphData, prices, allNodeLabels, onSelect, onClose,
}: Props) {
  const chain = chains.find(c => c.id === chainId) ?? chains[0];
  const stageIdx = chain.stages.findIndex(s => s.symbols.includes(symbol));
  const stageName = stageIdx !== -1 ? chain.stages[stageIdx].label : "";
  const price = prices[symbol];
  const label = allNodeLabels[symbol] ?? symbol;
  const node = graphData?.nodes.find(n => n.id === symbol);
  const description = node?.description ?? null;

  const edges = graphData?.edges ?? [];
  const seenSuppliers = new Set<string>();
  const seenCustomers = new Set<string>();
  const seenCompetitors = new Set<string>();
  const related: RelatedSymbol[] = [];

  const addRelated = (sym: string, relation: RelatedSymbol["relation"], seen: Set<string>, edgeLabel?: string) => {
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

  if (stageIdx > 0)
    for (const sym of chain.stages[stageIdx - 1].symbols) addRelated(sym, "supplier", seenSuppliers);
  if (stageIdx !== -1 && stageIdx < chain.stages.length - 1)
    for (const sym of chain.stages[stageIdx + 1].symbols) addRelated(sym, "customer", seenCustomers);
  for (const sym of (stageIdx !== -1 ? chain.stages[stageIdx].symbols : []))
    if (sym !== symbol) addRelated(sym, "competitor", seenCompetitors);

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

  const suppliers  = related.filter(r => r.relation === "supplier");
  const customers  = related.filter(r => r.relation === "customer");
  const competitors = related.filter(r => r.relation === "competitor");

  const pct = price?.change_pct;
  const hasChange = pct !== undefined && pct !== 0;
  const isUp = hasChange && pct! > 0;
  const changeColor = !hasChange ? "rgba(255,255,255,0.35)" : isUp ? "#10b981" : "#ef4444";

  function RelRow({ item }: { item: RelatedSymbol }) {
    const p = prices[item.symbol];
    const pc = p?.change_pct;
    const pcPos = pc && pc > 0;
    return (
      <button
        onClick={() => onSelect(item.symbol, item.chainId)}
        className="w-full text-left flex items-center gap-3 px-4 py-2.5 rounded-lg transition-all hover:bg-white/5"
      >
        <SymbolLogo symbol={item.symbol} size={28} />
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-0.5">
            <span className="text-[12px] font-bold font-mono text-white">{item.symbol}</span>
            {item.stageLabel && (
              <span className="text-[9px] px-1.5 py-0.5 rounded" style={{ color: item.chainColor, background: item.chainColor + "18" }}>
                {item.stageLabel}
              </span>
            )}
          </div>
          <div className="text-[10px] truncate" style={{ color: "rgba(255,255,255,0.40)" }}>
            {allNodeLabels[item.symbol] ?? item.symbol}
          </div>
          {item.edgeLabel && (
            <div className="text-[9px] mt-0.5 leading-snug truncate" style={{ color: "rgba(255,255,255,0.22)" }}>
              {item.edgeLabel}
            </div>
          )}
        </div>
        {p && (
          <div className="text-right flex-shrink-0">
            <div className="text-[10px] font-mono" style={{ color: "rgba(255,255,255,0.55)" }}>
              ${p.price.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
            </div>
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
        <div className="flex items-center gap-2 px-4 py-2">
          <span style={{ color: "rgba(255,255,255,0.25)" }}>{icon}</span>
          <span className="text-[9px] uppercase tracking-widest font-semibold" style={{ color: "rgba(255,255,255,0.35)" }}>
            {title}
          </span>
          <span className="text-[9px] ml-auto" style={{ color: "rgba(255,255,255,0.20)" }}>{items.length}</span>
        </div>
        {items.map(item => <RelRow key={item.symbol} item={item} />)}
      </div>
    );
  }

  return (
    <div
      className="h-full flex flex-col overflow-hidden"
      style={{ background: "#04080f", borderLeft: "1px solid rgba(255,255,255,0.08)", width: 360, flexShrink: 0 }}
    >
      {/* Header */}
      <div className="px-5 pt-5 pb-4 border-b border-white/8 flex-shrink-0">
        <div className="flex items-start justify-between mb-4">
          <div className="flex items-center gap-3">
            <SymbolLogo symbol={symbol} size={40} />
            <div>
              <div className="text-[22px] font-bold text-white font-mono leading-none mb-1">{symbol}</div>
              <div className="text-[12px]" style={{ color: "rgba(255,255,255,0.50)" }}>{label}</div>
            </div>
          </div>
          <button onClick={onClose} className="hover:text-white p-1 flex-shrink-0 mt-0.5" style={{ color: "rgba(255,255,255,0.30)" }}>
            <X size={14} />
          </button>
        </div>

        {/* Price row */}
        <div className="flex items-baseline gap-3 mb-3">
          {price?.price ? (
            <span className="text-[20px] font-mono font-semibold text-white">
              ${price.price.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
            </span>
          ) : (
            <span className="text-[16px] font-mono" style={{ color: "rgba(255,255,255,0.25)" }}>—</span>
          )}
          {hasChange && (
            <span className="text-[13px] font-semibold font-mono" style={{ color: changeColor }}>
              {isUp ? "+" : ""}{pct!.toFixed(2)}%
            </span>
          )}
        </div>

        {/* Chain breadcrumb */}
        <div className="flex items-center gap-1.5">
          <div className="w-1.5 h-1.5 rounded-full flex-shrink-0" style={{ background: chain.color }} />
          <span className="text-[10px]" style={{ color: chain.color + "cc" }}>{chain.label}</span>
          {stageName && (
            <>
              <span className="text-[10px]" style={{ color: "rgba(255,255,255,0.20)" }}>›</span>
              <span className="text-[10px]" style={{ color: "rgba(255,255,255,0.45)" }}>{stageName}</span>
            </>
          )}
        </div>
      </div>

      {/* Description */}
      {description && (
        <div className="px-5 py-4 border-b flex-shrink-0" style={{ borderColor: "rgba(255,255,255,0.05)" }}>
          <p className="text-[11px] leading-relaxed" style={{ color: "rgba(255,255,255,0.55)" }}>{description}</p>
        </div>
      )}

      {/* Relationships */}
      <div className="flex-1 overflow-y-auto py-2">
        <Section items={suppliers}   title="Suppliers"   icon={<ArrowLeft  size={10} />} />
        <Section items={customers}   title="Customers"   icon={<ArrowRight size={10} />} />
        <Section items={competitors} title="Same stage"  icon={<Minus      size={10} />} />
        {suppliers.length === 0 && customers.length === 0 && competitors.length === 0 && (
          <div className="px-5 py-4 text-[11px]" style={{ color: "rgba(255,255,255,0.25)" }}>
            No relationships mapped.
          </div>
        )}
      </div>
    </div>
  );
}
