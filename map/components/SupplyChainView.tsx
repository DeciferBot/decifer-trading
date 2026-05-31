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

function ChangePill({ pct }: { pct: number | undefined }) {
  if (pct === undefined) return null;
  if (pct === 0) return (
    <span className="text-[9px] px-1 py-0.5 rounded bg-white/5 text-gray-600 font-mono leading-none">0.0%</span>
  );
  const pos = pct > 0;
  return (
    <span className={`text-[9px] px-1 py-0.5 rounded font-mono font-medium leading-none flex-shrink-0 ${pos ? "bg-emerald-500/15 text-emerald-400" : "bg-red-500/15 text-red-400"}`}>
      {pos ? "+" : ""}{pct.toFixed(1)}%
    </span>
  );
}

export default function SupplyChainView({ chain, selectedSymbol, prices, onSelect, allNodeLabels }: Props) {
  const stages = chain.stages.filter(s => s.symbols.length > 0);

  return (
    <div className="h-full overflow-x-auto overflow-y-hidden">
      <div className="flex h-full" style={{ paddingLeft: 16, paddingRight: 16, paddingTop: 16, paddingBottom: 16, gap: 10, width: "max-content", minWidth: "100%" }}>
        {stages.map((stage) => (
          <div
            key={stage.id}
            className="flex flex-col flex-shrink-0"
            style={{ width: 144 }}
          >
            <div className="mb-2 flex-shrink-0">
              <div
                className="text-[9px] uppercase tracking-widest font-semibold mb-0.5 truncate"
                style={{ color: chain.color }}
              >
                {stage.label}
              </div>
              {stage.sublabel && (
                <div className="text-[9px] text-gray-600 leading-tight truncate">{stage.sublabel}</div>
              )}
              <div className="mt-1.5 h-px" style={{ background: chain.color + "30" }} />
            </div>

            <div className="flex flex-col gap-1 overflow-y-auto flex-1">
              {stage.symbols.map(symbol => {
                const priceData = prices[symbol];
                const name = allNodeLabels[symbol] ?? symbol;
                const isSelected = selectedSymbol === symbol;

                return (
                  <button
                    key={`${stage.id}-${symbol}`}
                    onClick={() => onSelect(symbol)}
                    className="w-full text-left rounded-lg transition-all"
                    style={{
                      padding: "6px 8px",
                      border: isSelected
                        ? `1px solid ${chain.color}88`
                        : "1px solid rgba(255,255,255,0.06)",
                      background: isSelected
                        ? chain.color + "18"
                        : "rgba(255,255,255,0.02)",
                      boxShadow: isSelected ? `0 0 0 1px ${chain.color}33` : undefined,
                    }}
                    onMouseEnter={e => {
                      if (!isSelected) {
                        (e.currentTarget as HTMLButtonElement).style.background = "rgba(255,255,255,0.05)";
                        (e.currentTarget as HTMLButtonElement).style.borderColor = "rgba(255,255,255,0.10)";
                      }
                    }}
                    onMouseLeave={e => {
                      if (!isSelected) {
                        (e.currentTarget as HTMLButtonElement).style.background = "rgba(255,255,255,0.02)";
                        (e.currentTarget as HTMLButtonElement).style.borderColor = "rgba(255,255,255,0.06)";
                      }
                    }}
                  >
                    <div className="flex items-center justify-between gap-1 mb-0.5">
                      <span className="font-bold text-white font-mono leading-none" style={{ fontSize: 12 }}>{symbol}</span>
                      <ChangePill pct={priceData?.change_pct} />
                    </div>
                    <div className="text-gray-500 truncate leading-tight" style={{ fontSize: 10 }}>{name}</div>
                  </button>
                );
              })}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
