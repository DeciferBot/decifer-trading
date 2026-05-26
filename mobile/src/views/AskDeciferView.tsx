"use client";
// Ask Decifer view — M13B.
// Entry point for plain-English market intelligence questions.
// Suggested questions are generated from current live context when data is available.
// No execution, broker, account, or order exposure.
// Backend not yet wired — safe placeholder response when submitted.

import { useState, useEffect } from "react";
import { Sparkles, ArrowRight, ChevronRight, Zap } from "lucide-react";
import type { MarketNowPayload } from "@/lib/customerApi";
import { buildContextualSuggestions } from "@/lib/customerBriefingModel";

// Fallback static questions used when no live data is available
export const STATIC_SUGGESTED_QUESTIONS = [
  "What changed since I was away?",
  "Why is AI infrastructure moving markets?",
  "Which names are connected to AI power demand?",
  "What is the risk to this theme?",
  "Explain today's market mood simply.",
  "Which themes are quiet today?",
  "What should I watch next?",
  "Why are small caps in focus?",
] as const;

// Backward-compatible alias for M13A safety audit test
export const SUGGESTED_QUESTIONS = STATIC_SUGGESTED_QUESTIONS;

interface Props {
  onAskContext?: string | null;
  data?: MarketNowPayload | null;
}

export default function AskDeciferView({ onAskContext, data }: Props) {
  const [input, setInput] = useState(onAskContext ?? "");
  const [submitted, setSubmitted] = useState(false);

  // Pre-fill and submit when a context is passed in (from "Ask about this" CTA)
  useEffect(() => {
    if (onAskContext) {
      setInput(onAskContext);
      setSubmitted(true);
    }
  }, [onAskContext]);

  const handleAsk = (question: string) => {
    setInput(question);
    setSubmitted(true);
  };

  // Contextual questions when data is available; fallback to static list
  const suggestedQuestions: readonly string[] = data
    ? buildContextualSuggestions(data)
    : STATIC_SUGGESTED_QUESTIONS;

  const isContextual = Boolean(data && (data.key_drivers?.length ?? 0) > 0);

  return (
    <div className="px-4 pb-8 pt-4 space-y-5">

      {/* Header */}
      <div className="text-center pt-2 pb-2">
        <div
          className="inline-flex items-center justify-center w-12 h-12 rounded-2xl mb-3"
          style={{ background: "rgba(249,115,22,0.1)", border: "1px solid rgba(249,115,22,0.2)" }}
        >
          <Sparkles size={22} style={{ color: "#f97316" }} />
        </div>
        <h2 className="text-lg font-bold text-slate-100 mb-1">Ask Decifer</h2>
        <p className="text-sm text-slate-400 leading-relaxed max-w-xs mx-auto">
          Get plain-English answers about what is moving markets and why.
        </p>
      </div>

      {/* Input */}
      <div
        className="rounded-2xl overflow-hidden"
        style={{ background: "#141b26", border: "1px solid rgba(255,255,255,0.08)" }}
      >
        <div className="flex items-center gap-2 px-4 py-3">
          <input
            type="text"
            value={input}
            onChange={(e) => {
              setInput(e.target.value);
              setSubmitted(false);
            }}
            placeholder="Ask anything about markets..."
            className="flex-1 bg-transparent text-sm text-slate-200 placeholder:text-slate-600 outline-none"
            onKeyDown={(e) => {
              if (e.key === "Enter" && input.trim()) handleAsk(input.trim());
            }}
          />
          {input.trim() && (
            <button
              onClick={() => handleAsk(input.trim())}
              className="shrink-0 w-7 h-7 flex items-center justify-center rounded-full transition-all active:scale-90"
              style={{ background: "#f97316" }}
              aria-label="Ask"
            >
              <ArrowRight size={13} className="text-white" />
            </button>
          )}
        </div>
      </div>

      {/* Response */}
      {submitted && (
        <div
          className="rounded-2xl p-4"
          style={{ background: "rgba(249,115,22,0.04)", border: "1px solid rgba(249,115,22,0.12)" }}
        >
          <div className="flex items-start gap-3">
            <Sparkles
              size={14}
              style={{ color: "#f97316", marginTop: "2px", flexShrink: 0 }}
            />
            <div>
              <p className="text-[12px] text-slate-300 leading-relaxed">
                Ask Decifer is being connected to the approved intelligence layer.
                Use the suggested questions below to explore your briefing sections.
              </p>
              <p className="text-[10px] text-slate-600 mt-2">
                Market intelligence only. Not financial advice.
              </p>
            </div>
          </div>
        </div>
      )}

      {/* Suggested questions */}
      <div>
        <div className="flex items-center gap-2 mb-3">
          <p
            className="text-[10px] font-bold uppercase tracking-[0.12em]"
            style={{ color: "#f97316" }}
          >
            {isContextual ? "Questions from today's briefing" : "Suggested questions"}
          </p>
          {isContextual && (
            <span
              className="text-[8px] font-semibold px-1.5 py-0.5 rounded-full flex items-center gap-1"
              style={{ background: "rgba(249,115,22,0.1)", color: "#fb923c" }}
            >
              <Zap size={7} />
              Live context
            </span>
          )}
        </div>
        <div className="space-y-2">
          {suggestedQuestions.map((q, i) => (
            <button
              key={i}
              onClick={() => handleAsk(q)}
              className="w-full text-left rounded-xl px-4 py-3 flex items-center gap-3 transition-all active:scale-[0.98]"
              style={{ background: "#141b26", border: "1px solid rgba(255,255,255,0.07)" }}
            >
              <span className="flex-1 text-[13px] text-slate-200 leading-snug">{q}</span>
              <ChevronRight size={13} className="text-slate-600 shrink-0" />
            </button>
          ))}
        </div>
      </div>

      {/* Disclaimer */}
      <div
        className="rounded-xl p-3 text-center"
        style={{ background: "rgba(255,255,255,0.02)", border: "1px solid rgba(255,255,255,0.04)" }}
      >
        <p className="text-[10px] text-slate-600 leading-relaxed">
          Decifer provides market intelligence only.
          Not financial advice. No trade recommendations.
        </p>
      </div>
    </div>
  );
}
