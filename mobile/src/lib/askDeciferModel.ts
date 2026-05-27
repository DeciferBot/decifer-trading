// Ask Decifer — pure model layer.
// Builds the system prompt from Decifer's live intelligence context.
// No external dependencies — fully testable.

import type { MarketNowPayload, TtgTheme } from "@/lib/customerApi";

export interface AskNewsItem {
  title: string;
  symbol: string;
  minutesAgo: number;
  summary: string;
  source: string;
}

export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
}

// Symbols Decifer's universe covers — used for news filtering in the API route.
export function extractUniverseSymbols(marketNow: MarketNowPayload | null): Set<string> {
  if (!marketNow) return new Set();
  return new Set<string>([
    ...(marketNow.universe_snapshot?.map(u => u.symbol) ?? []),
    ...(marketNow.radar?.map(r => r.symbol) ?? []),
  ]);
}

// Build the full system prompt from all available intelligence context.
export function buildSystemPrompt(
  marketNow: MarketNowPayload | null,
  ttgThemes: TtgTheme[],
  news: AskNewsItem[],
): string {
  const lines: string[] = [];
  const today = new Date().toLocaleDateString("en-US", {
    weekday: "long", year: "numeric", month: "long", day: "numeric",
  });

  lines.push(`You are Decifer, a market intelligence assistant built on Decifer's live trading intelligence platform.`);
  lines.push(`Today: ${today}.`);
  lines.push(``);

  lines.push(`## Your scope`);
  lines.push(`You can discuss:`);
  lines.push(`- What is moving markets today and why`);
  lines.push(`- Active themes and structural drivers in Decifer's intelligence layer`);
  lines.push(`- Names (symbols) that appear in Decifer's universe — their theme connection, why they matter, and risks`);
  lines.push(`- Recent news about universe symbols`);
  lines.push(`- Market regime, mood, key events, and known conflicts`);
  lines.push(`- Sector conditions, risk factors, and what to watch next`);
  lines.push(``);
  lines.push(`You MUST NOT:`);
  lines.push(`- Discuss stocks, ETFs, or assets that are NOT in the universe snapshot or radar below`);
  lines.push(`- Provide trade recommendations, entry points, price targets, or buy/sell signals`);
  lines.push(`- Reference account information, portfolio positions, order history, or broker mechanics`);
  lines.push(`- Use execution-layer terms: trade-ready, entry candidate, position entry, payload, scanner`);
  lines.push(``);
  lines.push(`If asked about something outside this scope, respond: "That's outside what I cover — I'm focused on Decifer's live market intelligence, active themes, and the names connected to them. Ask me about any of those and I can help."`);
  lines.push(``);
  lines.push(`Keep answers concise and plain-English. Never fabricate data — only use what is in the context below.`);
  lines.push(``);

  if (!marketNow) {
    lines.push(`## Status`);
    lines.push(`Live market intelligence is temporarily unavailable. You can discuss general market structure, but note you cannot confirm what Decifer's intelligence layer currently shows.`);
    return lines.join("\n");
  }

  lines.push(`## Current Market State`);
  if (marketNow.market_regime_label) lines.push(`Regime: ${marketNow.market_regime_label}`);
  if (marketNow.market_mood) lines.push(`Mood: ${marketNow.market_mood}`);
  if (marketNow.confidence_label) lines.push(`Confidence: ${marketNow.confidence_label}`);
  if (marketNow.plain_english_summary) lines.push(`Summary: ${marketNow.plain_english_summary}`);
  lines.push(``);

  if (marketNow.key_drivers?.length) {
    lines.push(`### Active Drivers`);
    marketNow.key_drivers.forEach(d => lines.push(`- ${d}`));
    lines.push(``);
  }

  if (marketNow.what_changed?.length) {
    lines.push(`### What Changed`);
    marketNow.what_changed.forEach(w => lines.push(`- ${w}`));
    lines.push(``);
  }

  if (marketNow.known_conflicts?.length) {
    lines.push(`### Mixed Signals / Known Conflicts`);
    marketNow.known_conflicts.forEach(c => lines.push(`- ${c}`));
    lines.push(``);
  }

  const themes = marketNow.themes ?? [];
  if (themes.length) {
    lines.push(`### Themes`);
    themes.forEach(t => {
      const state = t.state ? ` [${t.state}]` : "";
      const signal = t.event_signal ? ` — ${t.event_signal}` : "";
      lines.push(`- ${t.theme}${state}${signal}`);
    });
    lines.push(``);
  } else if (marketNow.active_themes?.length) {
    lines.push(`### Active Themes`);
    lines.push(marketNow.active_themes.join(", "));
    lines.push(``);
  }

  if (marketNow.key_events?.length) {
    lines.push(`### Key Events`);
    marketNow.key_events.slice(0, 10).forEach(e => {
      const summary = e.summary_plain_english ? `: ${e.summary_plain_english}` : "";
      lines.push(`- ${e.title}${summary}`);
      if (e.likely_positive_exposures?.length) {
        lines.push(`  Positive exposure: ${e.likely_positive_exposures.join(", ")}`);
      }
      if (e.likely_negative_exposures?.length) {
        lines.push(`  Negative exposure: ${e.likely_negative_exposures.join(", ")}`);
      }
    });
    lines.push(``);
  }

  if (marketNow.sectors?.length) {
    lines.push(`### Sectors`);
    marketNow.sectors.forEach(s => {
      const mood = s.mood ? ` (${s.mood})` : "";
      const reasons = s.reasons?.length ? ` — ${s.reasons.join("; ")}` : "";
      lines.push(`- ${s.name}${mood}${reasons}`);
    });
    lines.push(``);
  }

  if (marketNow.opportunity_explanations?.length) {
    lines.push(`### Opportunity Explanations`);
    marketNow.opportunity_explanations.forEach(o => {
      lines.push(`- ${o.theme}: ${o.explanation}`);
    });
    lines.push(``);
  }

  if (marketNow.risk_notes?.length) {
    lines.push(`### Risk Notes`);
    marketNow.risk_notes.forEach(r => lines.push(`- ${r}`));
    lines.push(``);
  }

  if (marketNow.what_to_watch?.length) {
    lines.push(`### What to Watch`);
    marketNow.what_to_watch.forEach(w => lines.push(`- ${w}`));
    lines.push(``);
  }

  if (marketNow.radar?.length) {
    lines.push(`### Radar (names on watch)`);
    marketNow.radar.forEach(r => {
      lines.push(`- ${r.symbol}: ${r.reason_to_watch}`);
      if (r.confirmation_signal) lines.push(`  Confirms when: ${r.confirmation_signal}`);
      if (r.invalidation_signal) lines.push(`  Invalidated if: ${r.invalidation_signal}`);
    });
    lines.push(``);
  }

  if (marketNow.universe_snapshot?.length) {
    lines.push(`### Universe (Decifer's theme-connected names)`);
    marketNow.universe_snapshot.forEach(u => {
      const company = u.company_name ? ` (${u.company_name})` : "";
      const tx = u.transmission && u.transmission !== "none" ? ` [${u.transmission}]` : "";
      lines.push(`- ${u.symbol}${company} — ${u.why_connected}${tx}`);
    });
    lines.push(``);
  }

  // Theme Transmission Graph — active themes first, then structural
  const activeGraphThemes = ttgThemes.filter(t => t.driver_active);
  const dormantGraphThemes = ttgThemes.filter(t => !t.driver_active);

  if (activeGraphThemes.length) {
    lines.push(`## Theme Transmission Graph — Active`);
    activeGraphThemes.forEach(t => {
      lines.push(`**${t.label}** (${t.theme_id})`);
      lines.push(t.plain_english_description);
      if (t.risk_note) lines.push(`Risk: ${t.risk_note}`);
      lines.push(``);
    });
  }

  if (dormantGraphThemes.length) {
    lines.push(`## Theme Transmission Graph — Structural (not currently active)`);
    dormantGraphThemes.forEach(t => {
      lines.push(`- **${t.label}**: ${t.plain_english_description}`);
    });
    lines.push(``);
  }

  // Universe-filtered news
  if (news.length) {
    lines.push(`## Recent News (universe symbols only)`);
    news.forEach(n => {
      const age = n.minutesAgo < 60
        ? `${n.minutesAgo}m ago`
        : `${Math.round(n.minutesAgo / 60)}h ago`;
      lines.push(`- [${n.symbol}] ${n.title} (${n.source}, ${age})`);
      if (n.summary) lines.push(`  ${n.summary}`);
    });
    lines.push(``);
  }

  return lines.join("\n");
}
