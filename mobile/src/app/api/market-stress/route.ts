import { NextResponse } from "next/server";

const FMP_KEY = process.env.FMP_API_KEY;
const STABLE = "https://financialmodelingprep.com/stable";

// ── Public types ──────────────────────────────────────────────────────────────

export type StressLabel =
  | "CALM"
  | "STRESS_BUILDING"
  | "ELEVATED_STRESS"
  | "MULTI_DIMENSION_STRESS"
  | "PANIC_CONDITIONS";

export interface StressDimension {
  score: number;
  maxScore: number;
  z_score: number | null;
  data_quality: "direct" | "proxy" | "unavailable";
  signal: string;
  confirming: boolean;
}

export type StructuralRiskLevel = "normal" | "watch" | "elevated" | "critical";
export type StructuralRiskOverall = "low" | "moderate" | "elevated" | "high";

export interface StructuralRiskItem {
  label: string;
  reading: string;
  status: StructuralRiskLevel;
}

export interface StructuralRisk {
  overall: StructuralRiskOverall;
  overall_color: string;
  items: StructuralRiskItem[];
  context: string;
}

export interface MarketStressPayload {
  market_stress_score: number;  // 0–10
  stress_label: StressLabel;
  stress_color: string;
  confidence: "high" | "medium" | "low";
  confirmation_count: number;
  structural_risk: StructuralRisk;
  dimensions: {
    // Tier 1 — Core Stress (max 2.0 each)
    credit: StressDimension;
    volatility: StressDimension;
    // Tier 2 — Breadth / Confirmation (max 1.5 each)
    breadth: StressDimension;
    stress_confirmation: StressDimension;
    // Tier 3 — Technical / Leading (max 1.0 each)
    trend: StressDimension;
    carry: StressDimension;
    // Tier 4 — Context / Early Warning (max 0.5 each)
    copper_gold: StressDimension;
    news_velocity: StressDimension;
  };
  liquidation_bonus: number;
  explanation: string;
  shadow_mode: true;
  ts: string;
}

// ── Math helpers ──────────────────────────────────────────────────────────────

function meanOf(arr: number[]): number {
  return arr.reduce((s, v) => s + v, 0) / arr.length;
}

function stdOf(arr: number[]): number {
  const m = meanOf(arr);
  return Math.sqrt(arr.reduce((s, v) => s + (v - m) ** 2, 0) / arr.length);
}

function zScore(value: number, history: number[]): number | null {
  if (history.length < 10) return null;
  const s = stdOf(history);
  if (s === 0) return null;
  return (value - meanOf(history)) / s;
}

function ret5d(closes: number[]): number | null {
  const n = closes.length;
  if (n < 6 || closes[n - 6] === 0) return null;
  return closes[n - 1] / closes[n - 6] - 1;
}

function ret10d(closes: number[]): number | null {
  const n = closes.length;
  if (n < 11 || closes[n - 11] === 0) return null;
  return closes[n - 1] / closes[n - 11] - 1;
}

function all5dRets(closes: number[]): number[] {
  const rets: number[] = [];
  for (let i = 5; i < closes.length; i++) {
    if (closes[i - 5] !== 0) rets.push(closes[i] / closes[i - 5] - 1);
  }
  return rets;
}

function all10dRets(closes: number[]): number[] {
  const rets: number[] = [];
  for (let i = 10; i < closes.length; i++) {
    if (closes[i - 10] !== 0) rets.push(closes[i] / closes[i - 10] - 1);
  }
  return rets;
}

function clamp(v: number, lo: number, hi: number): number {
  return Math.max(lo, Math.min(hi, v));
}

// ── Data fetching ─────────────────────────────────────────────────────────────

// Core ETF sensors + USDJPY (carry) + HG (copper)
const SYMBOLS = [
  "SPY", "QQQ", "IWM", "HYG", "LQD", "IEF", "TLT", "GLD", "UUP", "UVXY",
  "USDJPY", "HG",
];

type ClosesMap = Record<string, number[]>;
type FmpEodRow = { close: number };

async function fetchOneSym(symbol: string, fromDate: string): Promise<number[]> {
  const url = `${STABLE}/historical-price-eod/full?symbol=${encodeURIComponent(symbol)}&from=${fromDate}&apikey=${FMP_KEY}`;
  try {
    const res = await fetch(url, { next: { revalidate: 900 } });
    if (!res.ok) return [];
    const rows: FmpEodRow[] = await res.json();
    if (!Array.isArray(rows) || rows.length === 0) return [];
    return [...rows].reverse().map((r) => r.close).filter((c) => c > 0);
  } catch {
    return [];
  }
}

async function fetchCloses(fromDate: string): Promise<ClosesMap> {
  const results = await Promise.allSettled(
    SYMBOLS.map((sym) => fetchOneSym(sym, fromDate)),
  );
  const map: ClosesMap = {};
  results.forEach((r, i) => {
    if (r.status === "fulfilled" && r.value.length >= 10) {
      map[SYMBOLS[i]] = r.value;
    }
  });
  return map;
}

async function fetchVix(fromDate: string): Promise<{ closes: number[]; level: number | null }> {
  const rows = await fetchOneSym("^VIX", fromDate);
  if (rows.length === 0) return { closes: [], level: null };
  return { closes: rows, level: rows[rows.length - 1] };
}

type NewsArticle = { publishedDate?: string; title?: string; text?: string };

async function fetchNewsArticles(): Promise<NewsArticle[]> {
  try {
    const res = await fetch(
      `${STABLE}/news/general-latest?limit=250&apikey=${FMP_KEY}`,
      { cache: "no-store" },
    );
    if (!res.ok) return [];
    const data = await res.json();
    return Array.isArray(data) ? data : [];
  } catch {
    return [];
  }
}

// ── Dimension scorers ─────────────────────────────────────────────────────────

// TIER 1 — Core Stress

function scoreCredit(closes: ClosesMap): StressDimension {
  const hyg = closes["HYG"];
  const lqd = closes["LQD"];
  const ief = closes["IEF"];

  if (!hyg || !lqd) {
    return { score: 0, maxScore: 2, z_score: null, data_quality: "unavailable", signal: "HYG/LQD unavailable", confirming: false };
  }

  const hyg5d = ret5d(hyg);
  const hygAbsZ = hyg5d != null ? zScore(hyg5d, all5dRets(hyg)) : null;
  const hygActuallyFalling = hyg5d != null && hyg5d < 0;

  // HYG-LQD spread (HY vs IG, duration-adjusted proxy)
  const minLen = Math.min(hyg.length, lqd.length);
  const hygLqdSpreads: number[] = [];
  for (let i = 5; i < minLen; i++) {
    if (hyg[i - 5] !== 0 && lqd[i - 5] !== 0) {
      hygLqdSpreads.push((hyg[i] / hyg[i - 5] - 1) - (lqd[i] / lqd[i - 5] - 1));
    }
  }
  const hygLqdCurrent = hygLqdSpreads.length > 0 ? hygLqdSpreads[hygLqdSpreads.length - 1] : null;
  const hygLqdZ = hygLqdCurrent != null && hygLqdSpreads.length > 10
    ? zScore(hygLqdCurrent, hygLqdSpreads.slice(0, -1)) : null;

  // LQD-IEF spread (IG vs risk-free)
  let lqdIefZ: number | null = null;
  if (ief) {
    const minLen2 = Math.min(lqd.length, ief.length);
    const lqdIefSpreads: number[] = [];
    for (let i = 5; i < minLen2; i++) {
      if (lqd[i - 5] !== 0 && ief[i - 5] !== 0) {
        lqdIefSpreads.push((lqd[i] / lqd[i - 5] - 1) - (ief[i] / ief[i - 5] - 1));
      }
    }
    const cur = lqdIefSpreads.length > 0 ? lqdIefSpreads[lqdIefSpreads.length - 1] : null;
    lqdIefZ = cur != null && lqdIefSpreads.length > 10
      ? zScore(cur, lqdIefSpreads.slice(0, -1)) : null;
  }

  let score = 0;
  let confirming = false;

  if (hygAbsZ != null && hygAbsZ <= -1.5) {
    score += 1.0; confirming = true;
    if (hygAbsZ <= -2.0) score += 0.5;
  }
  if (hygLqdZ != null && hygLqdZ <= -1.5 && hygActuallyFalling) {
    score += 0.5; confirming = true;
  }
  if (lqdIefZ != null && lqdIefZ <= -1.5) {
    score += 0.75; confirming = true;
  }

  score = clamp(score, 0, 2);

  const absStr = hyg5d != null ? `HYG ${(hyg5d * 100).toFixed(1)}% (z=${hygAbsZ?.toFixed(1) ?? "n/a"})` : "HYG n/a";
  const spreadStr = hygLqdCurrent != null ? `, HY-IG ${(hygLqdCurrent * 100).toFixed(2)}% (z=${hygLqdZ?.toFixed(1) ?? "n/a"})` : "";
  const igStr = lqdIefZ != null ? `, LQD-IEF z=${lqdIefZ.toFixed(1)}` : "";

  return { score, maxScore: 2, z_score: hygAbsZ, data_quality: "proxy", signal: `${absStr}${spreadStr}${igStr}`, confirming };
}

function scoreVolatility(closes: ClosesMap, vixLevel: number | null, vixCloses: number[]): StressDimension {
  let score = 0;
  let confirming = false;

  if (vixLevel != null) {
    if (vixLevel >= 30) { score += 1.5; confirming = true; }
    else if (vixLevel >= 25) { score += 1.0; confirming = true; }
    else if (vixLevel >= 20) score += 0.5;
  }

  const vix5d = vixCloses.length >= 6 ? ret5d(vixCloses) : null;
  const vixZ = vix5d != null ? zScore(vix5d, all5dRets(vixCloses)) : null;
  if (vixZ != null && vixZ >= 1.5) { score += 0.5; confirming = true; }

  const uvxy = closes["UVXY"];
  if (uvxy) {
    const uvxyZ = ret5d(uvxy) != null ? zScore(ret5d(uvxy)!, all5dRets(uvxy)) : null;
    if (uvxyZ != null && uvxyZ >= 1.5) score += 0.5;
  }

  score = clamp(score, 0, 2);
  const vStr = vixLevel != null ? `VIX ${vixLevel.toFixed(1)}` : "VIX n/a";
  const zStr = vixZ != null ? `, 5d z=${vixZ.toFixed(1)}` : "";
  return { score, maxScore: 2, z_score: vixZ, data_quality: vixLevel != null ? "direct" : "proxy", signal: `${vStr}${zStr}`, confirming };
}

// TIER 2 — Breadth / Confirmation

function scoreBreadth(closes: ClosesMap): StressDimension {
  const spy = closes["SPY"];
  const iwm = closes["IWM"];
  if (!spy || !iwm) {
    return { score: 0, maxScore: 1.5, z_score: null, data_quality: "unavailable", signal: "Breadth unavailable", confirming: false };
  }

  const spy5d = ret5d(spy);
  const iwm5d = ret5d(iwm);
  if (spy5d == null || iwm5d == null) {
    return { score: 0, maxScore: 1.5, z_score: null, data_quality: "proxy", signal: "Insufficient data", confirming: false };
  }

  const minLen = Math.min(spy.length, iwm.length);
  const spreadHist: number[] = [];
  for (let i = 5; i < minLen; i++) {
    const sr = spy[i - 5] !== 0 ? spy[i] / spy[i - 5] - 1 : null;
    const ir = iwm[i - 5] !== 0 ? iwm[i] / iwm[i - 5] - 1 : null;
    if (sr != null && ir != null) spreadHist.push(ir - sr);
  }

  const spread = iwm5d - spy5d;
  const spreadZ = spreadHist.length >= 10 ? zScore(spread, spreadHist) : null;

  let score = 0;
  let confirming = false;
  if (spreadZ != null && spreadZ <= -1.5) { score += 1.0; confirming = true; }

  const qqq = closes["QQQ"];
  if (qqq) {
    const qqq5d = ret5d(qqq);
    if (qqq5d != null && qqq5d < 0 && spy5d < 0 && qqq5d < spy5d - 0.01) score += 0.5;
  }
  if (confirming && score >= 1.5) score += 0.5;
  score = clamp(score, 0, 1.5);

  const zStr = spreadZ != null ? ` (z=${spreadZ.toFixed(1)})` : "";
  return { score, maxScore: 1.5, z_score: spreadZ, data_quality: "proxy", signal: `IWM vs SPY ${(spread * 100).toFixed(1)}%${zStr}`, confirming };
}

function scoreStressConfirmation(closes: ClosesMap): StressDimension {
  const tlt = closes["TLT"];
  const gld = closes["GLD"];
  const uup = closes["UUP"];
  const hyg = closes["HYG"];
  const ief = closes["IEF"];

  const tlt5d = tlt ? ret5d(tlt) : null;
  const gld5d = gld ? ret5d(gld) : null;
  const uup5d = uup ? ret5d(uup) : null;
  const hyg5d = hyg ? ret5d(hyg) : null;
  const ief5d = ief ? ret5d(ief) : null;

  const tltZ = tlt && tlt5d != null ? zScore(tlt5d, all5dRets(tlt)) : null;
  const gldZ = gld && gld5d != null ? zScore(gld5d, all5dRets(gld)) : null;
  const uupZ = uup && uup5d != null ? zScore(uup5d, all5dRets(uup)) : null;
  const iefZ = ief && ief5d != null ? zScore(ief5d, all5dRets(ief)) : null;

  const classicPath = (tltZ != null && tltZ >= 1.0) && (gldZ != null && gldZ >= 1.0);
  const rateShockPath = (iefZ != null && iefZ <= -1.5) && (hyg5d != null && hyg5d < -0.01);

  let score = 0;
  let confirming = false;
  let regime = "none";

  if (classicPath) { score += 1.5; confirming = true; regime = "classic_risk_off"; }
  else if (rateShockPath) { score += 1.5; confirming = true; regime = "rate_shock"; }
  if (uupZ != null && uupZ >= 1.0) score += 0.5;

  score = clamp(score, 0, 1.5);

  const parts: string[] = [];
  if (tlt5d != null) parts.push(`TLT ${(tlt5d * 100).toFixed(1)}%`);
  if (gld5d != null) parts.push(`GLD ${(gld5d * 100).toFixed(1)}%`);
  if (uup5d != null) parts.push(`USD ${(uup5d * 100).toFixed(1)}%`);
  if (regime !== "none") parts.push(`[${regime}]`);

  return { score, maxScore: 1.5, z_score: tltZ, data_quality: tlt ? "direct" : "proxy", signal: parts.join(", ") || "No haven signal", confirming };
}

// TIER 3 — Technical / Leading

function scoreTrend(closes: ClosesMap): StressDimension {
  const spy = closes["SPY"];
  if (!spy || spy.length < 11) {
    return { score: 0, maxScore: 1.0, z_score: null, data_quality: "unavailable", signal: "Trend unavailable", confirming: false };
  }

  const spy5d = ret5d(spy);
  const spy10d = ret10d(spy);
  const spyZ = spy5d != null ? zScore(spy5d, all5dRets(spy)) : null;

  let score = 0;
  let confirming = false;

  if (spyZ != null && spyZ <= -1.5) { score += 0.5; confirming = true; }
  if (spy10d != null && spy10d < -0.03 && (spyZ == null || spyZ <= -1.0)) {
    score += 0.5; confirming = true;
  }

  score = clamp(score, 0, 1.0);

  const s5 = spy5d != null ? `SPY 5d ${(spy5d * 100).toFixed(1)}%` : "SPY n/a";
  const s10 = spy10d != null ? `, 10d ${(spy10d * 100).toFixed(1)}%` : "";
  const zStr = spyZ != null ? ` (z=${spyZ.toFixed(1)})` : "";
  return { score, maxScore: 1.0, z_score: spyZ, data_quality: "direct", signal: `${s5}${s10}${zStr}`, confirming };
}

function scoreCarry(closes: ClosesMap): StressDimension {
  // USD/JPY carry unwind: yen strengthening (USDJPY falling) = leveraged carry
  // positions unwinding globally. One of the largest latent stress triggers.
  const usdjpy = closes["USDJPY"];
  if (!usdjpy) {
    return { score: 0, maxScore: 1.0, z_score: null, data_quality: "unavailable", signal: "USDJPY unavailable", confirming: false };
  }

  const usd5d = ret5d(usdjpy);
  const usdHist = all5dRets(usdjpy);
  const usdZ = usd5d != null ? zScore(usd5d, usdHist) : null;

  let score = 0;
  let confirming = false;

  // USDJPY falling = yen strengthening = carry unwind risk
  if (usdZ != null && usdZ <= -1.5) { score += 0.75; confirming = true; }
  if (usdZ != null && usdZ <= -2.0) { score = 1.0; confirming = true; } // severity

  score = clamp(score, 0, 1.0);

  const level = usdjpy[usdjpy.length - 1];
  const retStr = usd5d != null ? `${(usd5d * 100).toFixed(2)}%` : "n/a";
  const zStr = usdZ != null ? ` (z=${usdZ.toFixed(1)})` : "";
  const dirStr = usdZ != null && usdZ <= -1.5 ? " — yen strengthening" : "";

  return {
    score, maxScore: 1.0, z_score: usdZ, data_quality: "direct",
    signal: `USD/JPY ${level?.toFixed(1) ?? "n/a"}, 5d ${retStr}${zStr}${dirStr}`,
    confirming,
  };
}

// TIER 4 — Context / Early Warning

function scoreCopperGold(closes: ClosesMap): StressDimension {
  // Copper/gold ratio: falling = demand destruction, gold outperforming =
  // flight to safety over industrial growth. Leads equity stress by weeks.
  const hg = closes["HG"];
  const gld = closes["GLD"];

  if (!hg || !gld) {
    return { score: 0, maxScore: 0.5, z_score: null, data_quality: "unavailable", signal: "Copper/Gold data unavailable", confirming: false };
  }

  // Copper absolute 10d return
  const hg10d = ret10d(hg);
  const hgZ = hg10d != null ? zScore(hg10d, all10dRets(hg)) : null;

  // Copper-Gold relative spread (copper underperforming gold = risk-off macro)
  const minLen = Math.min(hg.length, gld.length);
  const spreadHist: number[] = [];
  for (let i = 10; i < minLen; i++) {
    if (hg[i - 10] !== 0 && gld[i - 10] !== 0) {
      spreadHist.push((hg[i] / hg[i - 10] - 1) - (gld[i] / gld[i - 10] - 1));
    }
  }
  const spreadCurrent = spreadHist.length > 0 ? spreadHist[spreadHist.length - 1] : null;
  const spreadZ = spreadCurrent != null && spreadHist.length > 10
    ? zScore(spreadCurrent, spreadHist.slice(0, -1)) : null;

  let score = 0;
  let confirming = false;

  if (hgZ != null && hgZ <= -1.5) { score += 0.25; confirming = true; }
  if (spreadZ != null && spreadZ <= -1.5) { score += 0.25; confirming = true; }

  score = clamp(score, 0, 0.5);

  const copperStr = hg10d != null ? `Copper 10d ${(hg10d * 100).toFixed(1)}%` : "Copper n/a";
  const zStr = hgZ != null ? ` (z=${hgZ.toFixed(1)})` : "";
  const ratioStr = spreadZ != null ? `, Cu/Au spread z=${spreadZ.toFixed(1)}` : "";

  return { score, maxScore: 0.5, z_score: hgZ, data_quality: "proxy", signal: `${copperStr}${zStr}${ratioStr}`, confirming };
}

const STRESS_WORDS = [
  "crash", "crisis", "panic", "recession", "default", "contagion",
  "collapse", "downgrade", "liquidity", "sell-off", "selloff",
  "turmoil", "meltdown", "fears", "plunge", "turmoil", "stagflation",
];

function scoreNewsVelocity(articles: NewsArticle[]): StressDimension {
  if (articles.length < 10) {
    return { score: 0, maxScore: 0.5, z_score: null, data_quality: "unavailable", signal: "Insufficient news data", confirming: false };
  }

  // Group by date, count stress hits per day
  const byDay: Record<string, { total: number; stress: number }> = {};
  for (const a of articles) {
    const day = (a.publishedDate ?? "").slice(0, 10);
    if (!day) continue;
    if (!byDay[day]) byDay[day] = { total: 0, stress: 0 };
    byDay[day].total++;
    const text = ((a.title ?? "") + " " + (a.text ?? "")).toLowerCase();
    if (STRESS_WORDS.some((w) => text.includes(w))) byDay[day].stress++;
  }

  const days = Object.keys(byDay).sort();
  if (days.length < 2) {
    return { score: 0, maxScore: 0.5, z_score: null, data_quality: "proxy", signal: "Insufficient news history", confirming: false };
  }

  // Stress density per day (fraction of articles with stress keywords)
  const densities = days.map((d) => byDay[d].stress / Math.max(byDay[d].total, 1));
  const today = densities[densities.length - 1];
  const histDensities = densities.slice(0, -1);
  const velocityZ = zScore(today, histDensities);

  let score = 0;
  let confirming = false;

  if (velocityZ != null && velocityZ >= 1.5) { score += 0.25; confirming = true; }
  if (velocityZ != null && velocityZ >= 2.0) { score = 0.5; confirming = true; }

  score = clamp(score, 0, 0.5);

  const todayDay = days[days.length - 1];
  const todayCount = byDay[todayDay].stress;
  const todayTotal = byDay[todayDay].total;
  const zStr = velocityZ != null ? ` (z=${velocityZ.toFixed(1)})` : "";

  return {
    score, maxScore: 0.5, z_score: velocityZ, data_quality: "proxy",
    signal: `${todayCount}/${todayTotal} stress articles today${zStr}`,
    confirming,
  };
}

// ── Structural Risk — background conditions regardless of active stress ────────

function statusColor(s: StructuralRiskLevel): string {
  if (s === "critical") return "#ef4444";
  if (s === "elevated") return "#f97316";
  if (s === "watch")    return "#eab308";
  return "#10b981";
}

function overallColor(o: StructuralRiskOverall): string {
  if (o === "high")     return "#ef4444";
  if (o === "elevated") return "#f97316";
  if (o === "moderate") return "#eab308";
  return "#10b981";
}

function computeStructuralRisk(
  closes: ClosesMap,
  vixLevel: number | null,
  vixCloses: number[],
  hygLqdSpreadZ: number | null,
): StructuralRisk {
  const items: StructuralRiskItem[] = [];

  // 1. Valuation stretch — SPY % above its 50-day SMA
  const spy = closes["SPY"];
  if (spy && spy.length >= 52) {
    const sma50 = spy.slice(-50).reduce((s, v) => s + v, 0) / 50;
    const current = spy[spy.length - 1];
    const pctAbove = (current / sma50 - 1) * 100;
    let status: StructuralRiskLevel = "normal";
    if (pctAbove > 12) status = "critical";
    else if (pctAbove > 7)  status = "elevated";
    else if (pctAbove > 3)  status = "watch";
    items.push({
      label: "Valuation stretch",
      reading: `SPY ${pctAbove >= 0 ? "+" : ""}${pctAbove.toFixed(1)}% above 50d avg`,
      status,
    });
  } else {
    items.push({ label: "Valuation stretch", reading: "Insufficient data", status: "normal" });
  }

  // 2. Complacency — VIX vs its period average
  if (vixLevel != null && vixCloses.length >= 20) {
    const vixAvg = meanOf(vixCloses);
    const pctBelowAvg = (vixAvg - vixLevel) / vixAvg * 100; // positive = VIX below avg = complacent
    let status: StructuralRiskLevel = "normal";
    if (pctBelowAvg > 30) status = "critical";
    else if (pctBelowAvg > 20) status = "elevated";
    else if (pctBelowAvg > 10) status = "watch";
    const dir = pctBelowAvg > 0
      ? `${pctBelowAvg.toFixed(0)}% below ${vixAvg.toFixed(1)} avg`
      : `${Math.abs(pctBelowAvg).toFixed(0)}% above avg`;
    items.push({
      label: "Complacency",
      reading: `VIX ${vixLevel.toFixed(1)} — ${dir}`,
      status,
    });
  } else {
    items.push({ label: "Complacency", reading: "VIX data unavailable", status: "normal" });
  }

  // 3. Credit thinness — HY-IG spread approaching threshold
  if (hygLqdSpreadZ != null) {
    let status: StructuralRiskLevel = "normal";
    if (hygLqdSpreadZ <= -2.0)      status = "critical";
    else if (hygLqdSpreadZ <= -1.5) status = "elevated";
    else if (hygLqdSpreadZ <= -1.0) status = "watch";
    items.push({
      label: "Credit thinness",
      reading: `HY-IG spread z=${hygLqdSpreadZ.toFixed(1)} (threshold −1.5)`,
      status,
    });
  } else {
    items.push({ label: "Credit thinness", reading: "Spread data unavailable", status: "normal" });
  }

  const elevated = items.filter(i => i.status === "elevated" || i.status === "critical").length;
  const watch    = items.filter(i => i.status === "watch").length;

  let overall: StructuralRiskOverall = "low";
  if (elevated >= 2) overall = "high";
  else if (elevated === 1) overall = "elevated";
  else if (watch >= 2) overall = "moderate";
  else if (watch === 1) overall = "moderate";

  const context =
    overall === "high"     ? "Multiple structural vulnerabilities present. Stress signals carry amplified downside potential." :
    overall === "elevated" ? "One structural vulnerability confirmed. Any stress signal warrants close attention." :
    overall === "moderate" ? "Background conditions are mildly stretched. Monitor for deterioration." :
    "Background conditions are within normal range.";

  return { overall, overall_color: overallColor(overall), items, context };
}

// BONUS

function liquidationBonus(closes: ClosesMap): number {
  const syms = ["SPY", "QQQ", "IWM", "HYG", "IEF"];
  for (const s of syms) {
    const c = closes[s];
    if (!c) return 0;
    const r = ret5d(c);
    if (r == null || r >= 0) return 0;
  }
  return 0.5;
}

// ── Label / explanation ───────────────────────────────────────────────────────

function labelFor(score: number): { label: StressLabel; color: string } {
  if (score <= 2.5) return { label: "CALM", color: "#10b981" };
  if (score <= 4.5) return { label: "STRESS_BUILDING", color: "#84cc16" };
  if (score <= 6.5) return { label: "ELEVATED_STRESS", color: "#eab308" };
  if (score <= 8.5) return { label: "MULTI_DIMENSION_STRESS", color: "#f97316" };
  return { label: "PANIC_CONDITIONS", color: "#ef4444" };
}

function buildExplanation(
  dims: MarketStressPayload["dimensions"],
  liqBonus: number,
  count: number,
): string {
  if (count === 0) return "No stress dimensions are confirming. Markets appear calm across all sensors.";

  const confirming: string[] = [];
  const LABELS: Record<string, string> = {
    credit: "credit stress",
    volatility: "volatility",
    breadth: "breadth deterioration",
    stress_confirmation: "cross-asset confirmation",
    trend: "trend damage",
    carry: "yen carry unwind risk",
    copper_gold: "copper/gold macro signal",
    news_velocity: "elevated news stress velocity",
  };

  for (const [k, v] of Object.entries(dims)) {
    if (v.confirming) confirming.push(LABELS[k] ?? k);
  }

  if (count === 1) return `Early signal: ${confirming[0]} is showing stress. Other dimensions remain quiet.`;

  const confirmStr =
    confirming.length === 1
      ? confirming[0]
      : confirming.slice(0, -1).join(", ") + ` and ${confirming[confirming.length - 1]}`;
  const c0 = confirmStr.charAt(0).toUpperCase() + confirmStr.slice(1);

  const liqStr = liqBonus > 0
    ? " Simultaneous broad selling across equities and bonds suggests liquidation pressure."
    : "";

  return `${c0} are confirming stress.${liqStr}`;
}

// ── Route handler ─────────────────────────────────────────────────────────────

export async function GET() {
  if (!FMP_KEY) return NextResponse.json({ error: "no_key" }, { status: 503 });

  const fromDate = new Date(Date.now() - 120 * 24 * 60 * 60 * 1000)
    .toISOString()
    .slice(0, 10);

  const [closesResult, vixResult, newsResult] = await Promise.allSettled([
    fetchCloses(fromDate),
    fetchVix(fromDate),
    fetchNewsArticles(),
  ]);

  const closes = closesResult.status === "fulfilled" ? closesResult.value : {};
  const { closes: vixCloses, level: vixLevel } =
    vixResult.status === "fulfilled" ? vixResult.value : { closes: [], level: null };
  const articles = newsResult.status === "fulfilled" ? newsResult.value : [];

  const credit          = scoreCredit(closes);
  const volatility      = scoreVolatility(closes, vixLevel, vixCloses);
  const breadth         = scoreBreadth(closes);
  const stress_confirmation = scoreStressConfirmation(closes);
  const trend           = scoreTrend(closes);
  const carry           = scoreCarry(closes);
  const copper_gold     = scoreCopperGold(closes);
  const news_velocity   = scoreNewsVelocity(articles);
  const liqBonus        = liquidationBonus(closes);

  // Structural risk uses credit's HY-IG z-score (already computed inside scoreCredit)
  // Re-derive it here cleanly from the raw closes for the structural panel.
  let hygLqdSpreadZ: number | null = null;
  const hyg = closes["HYG"], lqd = closes["LQD"];
  if (hyg && lqd) {
    const minL = Math.min(hyg.length, lqd.length);
    const spreads: number[] = [];
    for (let i = 5; i < minL; i++) {
      if (hyg[i - 5] !== 0 && lqd[i - 5] !== 0)
        spreads.push((hyg[i] / hyg[i - 5] - 1) - (lqd[i] / lqd[i - 5] - 1));
    }
    if (spreads.length > 10) {
      const cur = spreads[spreads.length - 1];
      hygLqdSpreadZ = zScore(cur, spreads.slice(0, -1));
    }
  }

  const structural_risk = computeStructuralRisk(closes, vixLevel, vixCloses, hygLqdSpreadZ);

  const dimensions = {
    credit, volatility, breadth, stress_confirmation,
    trend, carry, copper_gold, news_velocity,
  };

  const confirmation_count = Object.values(dimensions).filter((d) => d.confirming).length;

  const raw =
    credit.score + volatility.score + breadth.score + stress_confirmation.score +
    trend.score + carry.score + copper_gold.score + news_velocity.score + liqBonus;

  const market_stress_score = clamp(parseFloat(raw.toFixed(1)), 0, 10);
  const { label: stress_label, color: stress_color } = labelFor(market_stress_score);

  const unavailableCount = Object.values(dimensions).filter(
    (d) => d.data_quality === "unavailable",
  ).length;
  const confidence: "high" | "medium" | "low" =
    unavailableCount >= 3 ? "low" : confirmation_count >= 3 ? "high" : "medium";

  const payload: MarketStressPayload = {
    market_stress_score,
    stress_label,
    structural_risk,
    stress_color,
    confidence,
    confirmation_count,
    dimensions,
    liquidation_bonus: liqBonus,
    explanation: buildExplanation(dimensions, liqBonus, confirmation_count),
    shadow_mode: true,
    ts: new Date().toISOString(),
  };

  return NextResponse.json(payload, {
    headers: { "Cache-Control": "no-store" },
  });
}
