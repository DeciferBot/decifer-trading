"use client";

import { useState } from "react";
import { ChevronRight } from "lucide-react";
import type { Chain } from "@/lib/chain-definitions";
import { SYMBOL_CONVICTION } from "@/lib/chain-definitions";
import type { GraphData } from "@/lib/types";

interface Props {
  chain: Chain;
  selectedSymbol: string | null;
  prices: Record<string, { price: number; change_pct: number; change_5d?: number }>;
  graphData: GraphData | null;
  onSelect: (symbol: string) => void;
  allNodeLabels: Record<string, string>;
  activeCandidates?: Set<string>;
}

function SymbolLogo({ symbol, size = 28 }: { symbol: string; size?: number }) {
  const [failed, setFailed] = useState(false);
  if (failed) {
    return (
      <div
        className="rounded-full flex items-center justify-center font-bold text-white flex-shrink-0"
        style={{ width: size, height: size, fontSize: size * 0.36, background: "rgba(255,255,255,0.08)" }}
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

function SymbolCard({
  symbol,
  name,
  price,
  isSelected,
  isActive,
  chainColor,
  onClick,
}: {
  symbol: string;
  name: string;
  price?: { price: number; change_pct: number; change_5d?: number };
  isSelected: boolean;
  isActive: boolean;
  chainColor: string;
  onClick: () => void;
}) {
  const pct = price?.change_pct;
  const hasChange = pct !== undefined && pct !== 0;
  const isUp = hasChange && pct! > 0;
  const isDown = hasChange && pct! < 0;
  const changeColor = isUp ? "#10b981" : isDown ? "#ef4444" : "rgba(255,255,255,0.35)";
  const cv = SYMBOL_CONVICTION[symbol];
  const cvScore = cv ? Math.round(cv * 100) : null;
  const cvColor = cv
    ? cv >= 0.85 ? "#10b981" : cv >= 0.70 ? "#f59e0b" : "#94a3b8"
    : "#94a3b8";

  return (
    <button
      onClick={onClick}
      className="w-full text-left rounded-xl transition-all"
      style={{
        background: isSelected
          ? chainColor + "20"
          : isActive
          ? "rgba(16,185,129,0.06)"
          : "rgba(255,255,255,0.04)",
        border: isSelected
          ? `1px solid ${chainColor}66`
          : isActive
          ? "1px solid rgba(16,185,129,0.30)"
          : "1px solid rgba(255,255,255,0.08)",
        borderLeft: `3px solid ${isSelected ? chainColor : isActive ? "#10b981" : hasChange ? changeColor : "rgba(255,255,255,0.12)"}`,
        padding: "10px 10px",
        boxShadow: isActive && !isSelected ? "0 0 0 1px rgba(16,185,129,0.12) inset" : undefined,
      }}
    >
      {/* Logo + ticker row */}
      <div className="flex items-center gap-2 mb-2">
        <SymbolLogo symbol={symbol} size={24} />
        <span className="font-bold font-mono text-white leading-none" style={{ fontSize: 13 }}>
          {symbol}
        </span>
        {isActive && (
          <span className="w-[6px] h-[6px] rounded-full bg-emerald-400 animate-pulse flex-shrink-0" title="In active universe" />
        )}
        {hasChange && (
          <span
            className="ml-auto text-[10px] font-semibold font-mono leading-none px-1.5 py-0.5 rounded-md"
            style={{ color: changeColor, background: hasChange ? changeColor + "18" : "transparent" }}
          >
            {isUp ? "+" : ""}{pct!.toFixed(2)}%
          </span>
        )}
      </div>

      {/* Company name */}
      <div className="truncate leading-tight mb-1.5" style={{ fontSize: 10, color: "rgba(255,255,255,0.80)" }}>
        {name}
      </div>

      {/* Price + conviction row */}
      <div className="flex items-center justify-between gap-1">
        <div className="font-mono leading-none" style={{ fontSize: 11, color: price?.price ? "rgba(255,255,255,0.90)" : "rgba(255,255,255,0.45)" }}>
          {price?.price
            ? `$${price.price.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
            : "—"}
        </div>
        {cvScore !== null && (
          <span
            className="font-mono font-semibold leading-none px-1.5 py-0.5 rounded-md flex-shrink-0"
            style={{
              fontSize: 9,
              color: cvColor,
              background: cvColor + "18",
              border: `1px solid ${cvColor}30`,
            }}
            title={`Conviction: ${cvScore}`}
          >
            CV {cvScore}
          </span>
        )}
      </div>

      {/* 5-day return */}
      {price?.change_5d !== undefined && (
        <div className="mt-1 flex items-center gap-1">
          <span style={{ fontSize: 8, color: "rgba(255,255,255,0.30)" }}>5d</span>
          <span
            className="font-mono font-semibold leading-none"
            style={{
              fontSize: 9,
              color: price.change_5d > 0 ? "#10b981" : price.change_5d < 0 ? "#ef4444" : "rgba(255,255,255,0.35)",
            }}
          >
            {price.change_5d > 0 ? "+" : ""}{price.change_5d.toFixed(2)}%
          </span>
        </div>
      )}
    </button>
  );
}

export default function SupplyChainView({ chain, selectedSymbol, prices, onSelect, allNodeLabels, activeCandidates }: Props) {
  const stages = chain.stages.filter(s => s.symbols.length > 0);

  return (
    <div className="h-full overflow-x-auto overflow-y-hidden">
      <div className="flex h-full" style={{ padding: "20px", gap: 0, width: "max-content", minWidth: "100%" }}>
        {stages.flatMap((stage, stageIdx) => {
          const col = (
            <div key={stage.id} className="flex flex-col flex-shrink-0" style={{ width: 164 }}>
              <div className="mb-3 flex-shrink-0">
                <div className="text-[9px] uppercase tracking-widest font-bold mb-0.5 truncate" style={{ color: chain.color }}>
                  {stage.label}
                </div>
                {stage.sublabel && (
                  <div className="text-[9px] leading-tight" style={{ color: "rgba(255,255,255,0.60)" }}>
                    {stage.sublabel}
                  </div>
                )}
                <div className="mt-2 h-px" style={{ background: chain.color + "30" }} />
              </div>

              <div className="flex flex-col gap-2 overflow-y-auto flex-1 pr-1">
                {stage.symbols.map(symbol => (
                  <SymbolCard
                    key={`${stageIdx}-${symbol}`}
                    symbol={symbol}
                    name={allNodeLabels[symbol] ?? symbol}
                    price={prices[symbol]}
                    isSelected={selectedSymbol === symbol}
                    isActive={activeCandidates?.has(symbol) ?? false}
                    chainColor={chain.color}
                    onClick={() => onSelect(symbol)}
                  />
                ))}
              </div>
            </div>
          );
          if (stageIdx < stages.length - 1) {
            return [
              col,
              <div
                key={`arrow-${stage.id}`}
                className="flex-shrink-0 flex items-start justify-center"
                style={{ width: 16, paddingTop: 6 }}
              >
                <ChevronRight size={12} style={{ color: chain.color + "55" }} />
              </div>,
            ];
          }
          return [col];
        })}
      </div>
    </div>
  );
}
