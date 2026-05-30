"use client";

import { useState, FormEvent } from "react";
import { useRouter } from "next/navigation";

export default function HomePage() {
  const router = useRouter();
  const [ticker, setTicker] = useState("");

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    const clean = ticker.trim().toUpperCase();
    if (clean) router.push(`/${clean}`);
  }

  return (
    <div className="flex flex-col items-center justify-center min-h-[calc(100vh-57px)] px-4">
      <div className="w-full max-w-md fade-up">
        <h1
          className="text-5xl mb-2 text-center leading-tight"
          style={{ fontFamily: "'Instrument Serif', serif", fontStyle: "italic" }}
        >
          Symbol Intelligence
        </h1>
        <p className="text-text-muted text-sm text-center mb-10 font-mono tracking-wide">
          Theme membership · Feed status · Macro context
        </p>

        <form onSubmit={handleSubmit} className="relative">
          <input
            type="text"
            value={ticker}
            onChange={(e) => setTicker(e.target.value.toUpperCase())}
            placeholder="Enter a ticker symbol"
            maxLength={10}
            autoFocus
            className="w-full bg-surface border border-border rounded-sm px-5 py-4 text-text font-mono text-lg tracking-widest placeholder:text-text-muted placeholder:text-sm placeholder:tracking-wide focus:outline-none focus:border-accent transition-colors"
          />
          <button
            type="submit"
            disabled={!ticker.trim()}
            className="absolute right-3 top-1/2 -translate-y-1/2 px-4 py-2 bg-accent text-background font-mono text-xs tracking-widest uppercase rounded-sm disabled:opacity-30 disabled:cursor-not-allowed hover:bg-orange-400 transition-colors"
          >
            Look up
          </button>
        </form>

        <p className="text-text-muted text-xs text-center mt-6 font-mono">
          125 curated symbols tracked
        </p>
      </div>
    </div>
  );
}
