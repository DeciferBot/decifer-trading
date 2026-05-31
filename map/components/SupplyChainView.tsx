"use client";

import type { Chain } from "@/lib/chain-definitions";
import type { GraphData } from "@/lib/types";

interface Props {
  chain: Chain;
  selectedSymbol: string | null;
  prices: Record<string, { price: number; change_pct: number }>;
  graphData: GraphData | null;
  onSelect: (symbol: string) => void;
  allNodeLabels: Record<string, string>;
}

function SymbolCard({
  symbol,
  name,
  price,
  isSelected,
  chainColor,
  onClick,
}: {
  symbol: string;
  name: string;
  price?: { price: number; change_pct: number };
  isSelected: boolean;
  chainColor: string;
  onClick: () => void;
}) {
  const pct = price?.change_pct;
  const hasChange = pct !== undefined && pct !== 0;
  const isUp = hasChange && pct! > 0;
  const isDown = hasChange && pct! < 0;

  const changeColor = isUp ? "#10b981" : isDown ? "#ef4444" : "#6b7280";
  const changeBg = isUp ? "rgba(16,185,129,0.10)" : isDown ? "rgba(239,68,68,0.10)" : "transparent";

  return (
    <button
      onClick={onClick}
      className="w-full text-left rounded-xl transition-all group"
      style={{
        background: isSelected ? chainColor + "18" : "rgba(255,255,255,0.03)",
        border: isSelected
          ? `1px solid ${chainColor}55`
          : "1px solid rgba(255,255,255,0.07)",
        borderLeft: isSelected
          ? `3px solid ${chainColor}`
          : `3px solid ${hasChange ? changeColor : "rgba(255,255,255,0.10)"}`,
        padding: "10px 10px 10px 10px",
      }}
    >
      {/* Top row: ticker + change */}
      <div className="flex items-center justify-between mb-1">
        <span
          className="font-bold font-mono leading-none"
          style={{ fontSize: 13, color: isSelected ? "#fff" : "#e2e8f0" }}
        >
          {symbol}
        </span>
        {hasChange && (
          <span
            className="text-[10px] font-semibold font-mono px-1.5 py-0.5 rounded-md leading-none"
            style={{ color: changeColor, background: changeBg }}
          >
            {isUp ? "+" : ""}{pct!.toFixed(2)}%
          </span>
        )}
      </div>

      {/* Company name */}
      <div
        className="truncate leading-tight mb-1.5"
        style={{ fontSize: 10, color: "#64748b" }}
      >
        {name}
      </div>

      {/* Price */}
      <div
        className="font-mono leading-none"
        style={{ fontSize: 11, color: price?.price ? "#94a3b8" : "#374151" }}
      >
        {price?.price ? `$${price.price.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}` : "—"}
      </div>
    </button>
  );
}

export default function SupplyChainView({ chain, selectedSymbol, prices, onSelect, allNodeLabels }: Props) {
  const stages = chain.stages.filter(s => s.symbols.length > 0);

  return (
    <div className="h-full overflow-x-auto overflow-y-hidden">
      <div
        className="flex h-full"
        style={{ padding: "20px 20px", gap: 12, width: "max-content", minWidth: "100%" }}
      >
        {stages.map((stage, stageIdx) => (
          <div key={stage.id} className="flex flex-col flex-shrink-0" style={{ width: 160 }}>
            {/* Column header */}
            <div className="mb-3 flex-shrink-0">
              <div
                className="text-[9px] uppercase tracking-widest font-bold mb-0.5 truncate"
                style={{ color: chain.color }}
              >
                {stage.label}
              </div>
              {stage.sublabel && (
                <div className="text-[9px] leading-tight" style={{ color: "#374151" }}>
                  {stage.sublabel}
                </div>
              )}
              <div className="mt-2 h-px" style={{ background: chain.color + "28" }} />
            </div>

            {/* Cards */}
            <div className="flex flex-col gap-2 overflow-y-auto flex-1 pr-1">
              {stage.symbols.map(symbol => (
                <SymbolCard
                  key={`${stageIdx}-${symbol}`}
                  symbol={symbol}
                  name={allNodeLabels[symbol] ?? symbol}
                  price={prices[symbol]}
                  isSelected={selectedSymbol === symbol}
                  chainColor={chain.color}
                  onClick={() => onSelect(symbol)}
                />
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
