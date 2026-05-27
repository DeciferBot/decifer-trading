"use client";
// Ask Decifer — streaming chat with conversation history.
// Calls /api/ask-decifer with full intelligence context.
// No execution, broker, account, or order exposure.

import { useState, useEffect, useRef, useCallback } from "react";
import { Sparkles, ArrowUp, ChevronRight, Zap, AlertCircle } from "lucide-react";
import type { MarketNowPayload } from "@/lib/customerApi";
import { buildContextualSuggestions } from "@/lib/customerBriefingModel";
import type { ChatMessage } from "@/lib/askDeciferModel";

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

// ── Message bubble ──────────────────────────────────────────────────────────

function UserBubble({ content }: { content: string }) {
  return (
    <div className="flex justify-end">
      <div
        className="max-w-[80%] rounded-2xl rounded-tr-sm px-4 py-2.5 text-[13px] leading-relaxed text-slate-100"
        style={{ background: "rgba(249,115,22,0.15)", border: "1px solid rgba(249,115,22,0.2)" }}
      >
        {content}
      </div>
    </div>
  );
}

function AssistantBubble({
  content,
  isStreaming = false,
}: {
  content: string;
  isStreaming?: boolean;
}) {
  return (
    <div className="flex items-start gap-2.5">
      <div
        className="shrink-0 w-6 h-6 rounded-full flex items-center justify-center mt-0.5"
        style={{ background: "rgba(249,115,22,0.1)", border: "1px solid rgba(249,115,22,0.2)" }}
      >
        <Sparkles size={11} style={{ color: "#f97316" }} />
      </div>
      <div
        className="flex-1 rounded-2xl rounded-tl-sm px-4 py-2.5"
        style={{ background: "#141b26", border: "1px solid rgba(255,255,255,0.07)" }}
      >
        <p className="text-[13px] text-slate-200 leading-relaxed whitespace-pre-wrap">
          {content}
          {isStreaming && (
            <span
              className="inline-block w-1.5 h-3.5 ml-0.5 rounded-sm align-text-bottom animate-pulse"
              style={{ background: "#f97316", opacity: 0.8 }}
            />
          )}
        </p>
      </div>
    </div>
  );
}

function ThinkingBubble() {
  return (
    <div className="flex items-start gap-2.5">
      <div
        className="shrink-0 w-6 h-6 rounded-full flex items-center justify-center mt-0.5"
        style={{ background: "rgba(249,115,22,0.1)", border: "1px solid rgba(249,115,22,0.2)" }}
      >
        <Sparkles size={11} style={{ color: "#f97316" }} />
      </div>
      <div
        className="rounded-2xl rounded-tl-sm px-4 py-3"
        style={{ background: "#141b26", border: "1px solid rgba(255,255,255,0.07)" }}
      >
        <div className="flex items-center gap-1.5">
          {[0, 150, 300].map(delay => (
            <span
              key={delay}
              className="inline-block w-1.5 h-1.5 rounded-full animate-pulse"
              style={{ background: "#f97316", opacity: 0.6, animationDelay: `${delay}ms` }}
            />
          ))}
        </div>
      </div>
    </div>
  );
}

// ── Main component ──────────────────────────────────────────────────────────

export default function AskDeciferView({ onAskContext, data }: Props) {
  const [input, setInput] = useState("");
  const [history, setHistory] = useState<ChatMessage[]>([]);
  const [streamingText, setStreamingText] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const lastContextRef = useRef<string | null>(null);

  const hasMessages = history.length > 0 || streamingText || isLoading;

  const suggestedQuestions: readonly string[] = data
    ? buildContextualSuggestions(data)
    : STATIC_SUGGESTED_QUESTIONS;

  const isContextual = Boolean(data && (data.key_drivers?.length ?? 0) > 0);

  // Auto-scroll to bottom when messages update
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [history, streamingText, isLoading]);

  const handleAsk = useCallback(async (question: string) => {
    if (!question.trim() || isLoading) return;

    setInput("");
    setErrorMsg(null);
    setIsLoading(true);
    setStreamingText("");

    // Snapshot history before adding new message (sent to API as prior context)
    const priorHistory = history;

    // Optimistically show user message
    setHistory(prev => [...prev, { role: "user", content: question }]);

    try {
      const res = await fetch("/api/ask-decifer", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question, history: priorHistory }),
      });

      if (!res.ok) {
        const errText = await res.text().catch(() => "Request failed");
        throw new Error(errText);
      }

      if (!res.body) throw new Error("No response body");

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let accumulated = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        accumulated += decoder.decode(value, { stream: true });
        setStreamingText(accumulated);
      }

      // Flush remaining bytes
      accumulated += decoder.decode();

      if (!accumulated.trim()) {
        throw new Error("Empty response from Decifer.");
      }

      setHistory(prev => [...prev, { role: "assistant", content: accumulated }]);
      setStreamingText("");
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Something went wrong.";
      setErrorMsg(msg);
      setHistory(prev => [
        ...prev,
        {
          role: "assistant",
          content: "I couldn't get a response right now. Please try again.",
        },
      ]);
      setStreamingText("");
    } finally {
      setIsLoading(false);
    }
  }, [history, isLoading]);

  // Auto-submit when a context is deep-linked from another tab
  useEffect(() => {
    if (onAskContext && onAskContext !== lastContextRef.current) {
      lastContextRef.current = onAskContext;
      handleAsk(onAskContext);
    }
  }, [onAskContext, handleAsk]);

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter" && input.trim() && !isLoading) {
      handleAsk(input.trim());
    }
  };

  return (
    <div className="flex flex-col">

      {/* ── Empty state: header + suggested questions ────────────────────── */}
      {!hasMessages && (
        <div className="px-4 pt-4 space-y-5">
          <div className="text-center pt-2 pb-2">
            <div
              className="inline-flex items-center justify-center w-12 h-12 rounded-2xl mb-3"
              style={{
                background: "rgba(249,115,22,0.1)",
                border: "1px solid rgba(249,115,22,0.2)",
              }}
            >
              <Sparkles size={22} style={{ color: "#f97316" }} />
            </div>
            <h2 className="text-lg font-bold text-slate-100 mb-1">Ask Decifer</h2>
            <p className="text-sm text-slate-400 leading-relaxed max-w-xs mx-auto">
              Ask anything about what is moving markets and why.
            </p>
          </div>

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
                  className="text-[10px] font-semibold px-1.5 py-0.5 rounded-full flex items-center gap-1"
                  style={{ background: "rgba(249,115,22,0.1)", color: "#fb923c" }}
                >
                  <Zap size={9} />
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
                  style={{
                    background: "#141b26",
                    border: "1px solid rgba(255,255,255,0.07)",
                  }}
                >
                  <span className="flex-1 text-[13px] text-slate-200 leading-snug">{q}</span>
                  <ChevronRight size={13} className="text-slate-400 shrink-0" />
                </button>
              ))}
            </div>
          </div>
        </div>
      )}

      {/* ── Conversation thread ───────────────────────────────────────────── */}
      {hasMessages && (
        <div className="px-4 pt-4 space-y-4">
          {history.map((msg, i) =>
            msg.role === "user" ? (
              <UserBubble key={i} content={msg.content} />
            ) : (
              <AssistantBubble key={i} content={msg.content} />
            ),
          )}

          {/* Streaming response */}
          {streamingText && (
            <AssistantBubble content={streamingText} isStreaming />
          )}

          {/* Waiting for first token */}
          {isLoading && !streamingText && <ThinkingBubble />}

          {/* Error banner */}
          {errorMsg && (
            <div
              className="flex items-start gap-2 rounded-xl px-3 py-2.5"
              style={{
                background: "rgba(239,68,68,0.06)",
                border: "1px solid rgba(239,68,68,0.15)",
              }}
            >
              <AlertCircle size={12} className="text-rose-400 shrink-0 mt-0.5" />
              <p className="text-[11px] text-rose-400">{errorMsg}</p>
            </div>
          )}
        </div>
      )}

      {/* Scroll anchor */}
      <div ref={bottomRef} className="h-2" />

      {/* ── Input — sticky to bottom of scroll container ─────────────────── */}
      <div
        className="sticky bottom-0 px-4 pt-3"
        style={{
          paddingBottom: "max(env(safe-area-inset-bottom), 0.75rem)",
          background: "#0c1117",
          borderTop: hasMessages ? "1px solid rgba(255,255,255,0.05)" : "none",
        }}
      >
        <div
          className="flex items-center gap-2 rounded-2xl px-4 py-2.5"
          style={{
            background: "#141b26",
            border: "1px solid rgba(255,255,255,0.10)",
          }}
        >
          <input
            ref={inputRef}
            type="text"
            value={input}
            onChange={e => {
              setInput(e.target.value);
            }}
            onKeyDown={handleKeyDown}
            placeholder={isLoading ? "Decifer is thinking…" : "Ask anything about markets…"}
            disabled={isLoading}
            className="flex-1 bg-transparent text-sm text-slate-200 placeholder:text-slate-500 outline-none disabled:opacity-50"
          />
          <button
            onClick={() => input.trim() && handleAsk(input.trim())}
            disabled={!input.trim() || isLoading}
            className="shrink-0 w-7 h-7 flex items-center justify-center rounded-full transition-all active:scale-90 disabled:opacity-30"
            style={{ background: "#f97316" }}
            aria-label="Send"
          >
            <ArrowUp size={13} className="text-white" />
          </button>
        </div>

        <p className="text-[10px] text-slate-600 text-center mt-2">
          Market intelligence only — not financial advice.
        </p>
      </div>
    </div>
  );
}
