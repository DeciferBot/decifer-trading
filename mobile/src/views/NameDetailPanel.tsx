"use client";
// Name / ticker detail panel — slide-up overlay.
// Explains why a name is on the intelligence map.
// No trading language: no entry/exit/position/P&L/order/broker.

import { X } from "lucide-react";
import type { MarketNowPayload, RadarItem } from "@/lib/customerApi";
import { translateTheme, themeDescription } from "@/lib/translate";

function formatTs(iso: string | undefined): string {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  } catch {
    return "—";
  }
}

interface Props {
  name: RadarItem;
  data: MarketNowPayload | null;
  onClose: () => void;
}

export default function NameDetailPanel({ name, data, onClose }: Props) {
  const relatedTheme = name.theme_link
    ? data?.themes?.find(t => t.theme === name.theme_link) ?? null
    : null;
  const themeDesc = name.theme_link ? themeDescription(name.theme_link) : null;
  const watchNext = data?.watch_next ?? data?.what_to_watch ?? [];

  return (
    <div
      className="fixed inset-0 z-50 flex flex-col justify-end"
      style={{ background: "rgba(0,0,0,0.65)" }}
      onClick={onClose}
    >
      <div
        className="flex flex-col overflow-hidden rounded-t-3xl"
        style={{
          background: "#ffffff",
          maxHeight: "88vh",
          paddingBottom: "env(safe-area-inset-bottom)",
        }}
        onClick={e => e.stopPropagation()}
      >
        {/* Handle bar */}
        <div className="flex justify-center pt-3 pb-1">
          <div className="w-10 h-1 rounded-full" style={{ background: "#d1d5db" }} />
        </div>

        {/* Header */}
        <div
          className="px-5 pt-2 pb-4 flex items-start justify-between gap-3"
          style={{ borderBottom: "1px solid #f3f4f6" }}
        >
          <div className="flex-1 min-w-0">
            <p className="text-[9px] font-bold uppercase tracking-wider text-slate-400 mb-1">
              Intelligence Map · Detail
            </p>
            <h2 className="text-2xl font-black text-slate-900">{name.symbol}</h2>
            {name.theme_link && (
              <span
                className="inline-block mt-1.5 text-[10px] font-semibold px-2.5 py-1 rounded-full"
                style={{ background: "rgba(249,115,22,0.1)", color: "#c2410c" }}
              >
                {translateTheme(name.theme_link)}
              </span>
            )}
          </div>
          <button
            onClick={onClose}
            className="p-2 rounded-full mt-1 transition-colors"
            style={{ background: "#f3f4f6" }}
          >
            <X size={16} className="text-slate-600" />
          </button>
        </div>

        {/* Scrollable body */}
        <div className="flex-1 overflow-y-auto px-5 py-4 space-y-5">

          {/* Why Decifer is watching this */}
          <section>
            <p className="text-[9px] font-bold uppercase tracking-wider text-slate-400 mb-2">
              Why Decifer Is Watching This
            </p>
            <div
              className="rounded-xl p-3.5"
              style={{ background: "#f9fafb", border: "1px solid #e5e7eb" }}
            >
              <p className="text-sm text-slate-700 leading-relaxed">
                {name.reason_to_watch || "Connected to an active market theme."}
              </p>
            </div>
          </section>

          {/* Connected theme */}
          {themeDesc && name.theme_link && (
            <section>
              <p className="text-[9px] font-bold uppercase tracking-wider text-slate-400 mb-2">
                Connected Theme
              </p>
              <div
                className="rounded-xl p-3.5"
                style={{ background: "#fff7ed", border: "1px solid rgba(249,115,22,0.2)" }}
              >
                <p className="text-[11px] font-bold mb-1.5" style={{ color: "#c2410c" }}>
                  {translateTheme(name.theme_link)}
                </p>
                <p className="text-xs text-slate-700 leading-relaxed">{themeDesc}</p>
              </div>
            </section>
          )}

          {/* Recent evidence */}
          {relatedTheme?.from_events && relatedTheme.from_events.length > 0 && (
            <section>
              <p className="text-[9px] font-bold uppercase tracking-wider text-slate-400 mb-2">
                Recent Evidence
              </p>
              <ul className="space-y-2">
                {relatedTheme.from_events.map((ev, i) => (
                  <li key={i} className="flex items-start gap-2.5">
                    <span
                      className="w-1.5 h-1.5 rounded-full mt-1.5 shrink-0"
                      style={{ background: "#f97316" }}
                    />
                    <p className="text-xs text-slate-600 leading-relaxed">{ev}</p>
                  </li>
                ))}
              </ul>
            </section>
          )}

          {/* What changed */}
          {(data?.what_changed?.length ?? 0) > 0 && (
            <section>
              <p className="text-[9px] font-bold uppercase tracking-wider text-slate-400 mb-2">
                What Changed Today
              </p>
              <ul className="space-y-1.5">
                {(data?.what_changed ?? []).slice(0, 3).map((item, i) => (
                  <li key={i} className="flex items-start gap-2">
                    <span className="w-1 h-1 rounded-full mt-1.5 shrink-0 bg-slate-300" />
                    <p className="text-xs text-slate-600 leading-relaxed">{item}</p>
                  </li>
                ))}
              </ul>
            </section>
          )}

          {/* Watch next */}
          {watchNext.length > 0 && (
            <section>
              <p className="text-[9px] font-bold uppercase tracking-wider text-slate-400 mb-2">
                What to Watch Next
              </p>
              <ul className="space-y-1.5">
                {watchNext.slice(0, 3).map((item, i) => (
                  <li key={i} className="flex items-start gap-2">
                    <span
                      className="w-1 h-1 rounded-full mt-1.5 shrink-0"
                      style={{ background: "#f97316" }}
                    />
                    <p className="text-xs text-slate-600 leading-relaxed">{item}</p>
                  </li>
                ))}
              </ul>
            </section>
          )}

          {/* Intelligence freshness */}
          <section
            className="rounded-xl px-3.5 py-3"
            style={{ background: "#f9fafb", border: "1px solid #e5e7eb" }}
          >
            <p className="text-[9px] font-bold uppercase tracking-wider text-slate-400 mb-1">
              Intelligence Freshness
            </p>
            <p className="text-xs text-slate-500">
              Last updated: {formatTs(data?.freshness_timestamp)}
            </p>
          </section>

          {/* Disclaimer */}
          <section className="text-center pb-2">
            <p className="text-[10px] text-slate-400 leading-relaxed">
              This name appears on the Decifer intelligence map because it is connected to an active
              market theme. Market intelligence only. Not a recommendation. Not financial advice.
              No trade execution.
            </p>
          </section>
        </div>
      </div>
    </div>
  );
}
