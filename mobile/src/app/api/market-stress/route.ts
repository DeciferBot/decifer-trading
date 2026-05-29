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

export interface MarketStressPayload {
  market_stress_score: number;  // 0–10
  stress_label: StressLabel;
  stress_color: string;
  confidence: "high" | "medium" | "low";
  confirmation_count: number;
  dimensions: {
    credit: StressDimension;
    volatility: StressDimension;
    breadth: StressDimension;
    stress_confirmation: StressDimension;
    trend: StressDimension;
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

// closes must be sorted oldest-first
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

function clamp(v: number, lo: number, hi: number): number {
  return Math.max(lo, Math.min(hi, v));
}

// ── Data fetching ─────────────────────────────────────────────────────────────

const SYMBOLS = ["SPY", "QQQ", "IWM", "HYG", "LQD", "IEF", "TLT", "GLD", "UUP", "UVXY"];
type ClosesMap = Record<string, number[]>; // symbol → oldest-first closes

type FmpEodRow = { symbol: string; date: string; close: number };

async function fetchOneSym(symbol: string, fromDate: string): Promise<number[]> {
  const encoded = encodeURIComponent(symbol);
  const url = `${STABLE}/historical-price-eod/full?symbol=${encoded}&from=${fromDate}&apikey=${FMP_KEY}`;
  try {
    const res = await fetch(url, { next: { revalidate: 900 } });
    if (!res.ok) return [];
    const rows: FmpEodRow[] = await res.json();
    if (!Array.isArray(rows) || rows.length === 0) return [];
    // stable returns newest-first → reverse to oldest-first
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
  // rows are oldest-first; latest is last element
  return { closes: rows, level: rows[rows.length - 1] };
}

// ── Dimension scorers ─────────────────────────────────────────────────────────

function scoreCredit(closes: ClosesMap): StressDimension {
  const hyg = closes["HYG"];
  if (!hyg) {
    return { score: 0, maxScore: 2, z_score: null, data_quality: "unavailable", signal: "HYG unavailable", confirming: false };
  }

  const hyg5d = ret5d(hyg);
  const hygHist = all5dRets(hyg);
  const hygZ = hyg5d != null ? zScore(hyg5d, hygHist) : null;

  let score = 0;
  let confirming = false;

  if (hygZ != null && hygZ <= -1.5) {
    score += 1.0;
    confirming = true;
    if (hygZ <= -2.0) score += 0.5; // severity
  }

  // LQD underperforms IEF → credit spread widening confirmation
  const lqd5d = closes["LQD"] ? ret5d(closes["LQD"]!) : null;
  const ief5d = closes["IEF"] ? ret5d(closes["IEF"]!) : null;
  if (lqd5d != null && ief5d != null && lqd5d < ief5d - 0.005) {
    score += 0.5;
  }

  score = clamp(score, 0, 2);
  const pct = hyg5d != null ? `HYG ${(hyg5d * 100).toFixed(1)}%` : "HYG n/a";
  const zStr = hygZ != null ? ` (z=${hygZ.toFixed(1)})` : "";

  return { score, maxScore: 2, z_score: hygZ, data_quality: "proxy", signal: `${pct}${zStr}`, confirming };
}

function scoreVolatility(
  closes: ClosesMap,
  vixLevel: number | null,
  vixCloses: number[],
): StressDimension {
  let score = 0;
  let confirming = false;

  if (vixLevel != null) {
    if (vixLevel >= 30) { score += 1.5; confirming = true; }
    else if (vixLevel >= 25) { score += 1.0; confirming = true; }
    else if (vixLevel >= 20) score += 0.5;
  }

  const vix5d = vixCloses.length >= 6 ? ret5d(vixCloses) : null;
  const vixHist = all5dRets(vixCloses);
  const vixZ = vix5d != null ? zScore(vix5d, vixHist) : null;
  if (vixZ != null && vixZ >= 1.5) { score += 0.5; confirming = true; }

  const uvxy = closes["UVXY"];
  if (uvxy) {
    const uvxy5d = ret5d(uvxy);
    const uvxyZ = uvxy5d != null ? zScore(uvxy5d, all5dRets(uvxy)) : null;
    if (uvxyZ != null && uvxyZ >= 1.5) score += 0.5;
  }

  score = clamp(score, 0, 2);
  const vixStr = vixLevel != null ? `VIX ${vixLevel.toFixed(1)}` : "VIX n/a";
  const changeStr = vixZ != null ? `, 5d z=${vixZ.toFixed(1)}` : "";

  return {
    score,
    maxScore: 2,
    z_score: vixZ,
    data_quality: vixLevel != null ? "direct" : "proxy",
    signal: `${vixStr}${changeStr}`,
    confirming,
  };
}

function scoreBreadth(closes: ClosesMap): StressDimension {
  const spy = closes["SPY"];
  const iwm = closes["IWM"];
  if (!spy || !iwm) {
    return { score: 0, maxScore: 2, z_score: null, data_quality: "unavailable", signal: "Breadth data unavailable", confirming: false };
  }

  const spy5d = ret5d(spy);
  const iwm5d = ret5d(iwm);
  if (spy5d == null || iwm5d == null) {
    return { score: 0, maxScore: 2, z_score: null, data_quality: "proxy", signal: "Insufficient data", confirming: false };
  }

  // Build IWM-SPY spread history
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
  score = clamp(score, 0, 2);

  const zStr = spreadZ != null ? ` (z=${spreadZ.toFixed(1)})` : "";
  return {
    score,
    maxScore: 2,
    z_score: spreadZ,
    data_quality: "proxy",
    signal: `IWM vs SPY ${(spread * 100).toFixed(1)}%${zStr}`,
    confirming,
  };
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

  // Classic: bonds bid + gold bid
  const classicPath = (tltZ != null && tltZ >= 1.0) && (gldZ != null && gldZ >= 1.0);
  // Rate shock: bonds selling while credit also selling (2022-style)
  const rateShockPath = (iefZ != null && iefZ <= -1.5) && (hyg5d != null && hyg5d < -0.01);

  let score = 0;
  let confirming = false;
  let regime = "none";

  if (classicPath) { score += 1.5; confirming = true; regime = "classic_risk_off"; }
  else if (rateShockPath) { score += 1.5; confirming = true; regime = "rate_shock"; }

  if (uupZ != null && uupZ >= 1.0) score += 0.5;

  score = clamp(score, 0, 2);

  const parts: string[] = [];
  if (tlt5d != null) parts.push(`TLT ${(tlt5d * 100).toFixed(1)}%`);
  if (gld5d != null) parts.push(`GLD ${(gld5d * 100).toFixed(1)}%`);
  if (uup5d != null) parts.push(`USD ${(uup5d * 100).toFixed(1)}%`);
  if (regime !== "none") parts.push(`[${regime}]`);

  return {
    score,
    maxScore: 2,
    z_score: tltZ,
    data_quality: tlt ? "direct" : "proxy",
    signal: parts.join(", ") || "No haven signal",
    confirming,
  };
}

function scoreTrend(closes: ClosesMap): StressDimension {
  const spy = closes["SPY"];
  if (!spy || spy.length < 11) {
    return { score: 0, maxScore: 1.5, z_score: null, data_quality: "unavailable", signal: "Trend data unavailable", confirming: false };
  }

  const spy5d = ret5d(spy);
  const spy10d = ret10d(spy);
  const spyZ = spy5d != null ? zScore(spy5d, all5dRets(spy)) : null;

  let score = 0;
  let confirming = false;

  if (spyZ != null && spyZ <= -1.5) { score += 0.75; confirming = true; }
  if (spy10d != null && spy10d < -0.03 && (spyZ == null || spyZ <= -1.0)) {
    score += 0.75;
    confirming = true;
  }

  score = clamp(score, 0, 1.5);

  const s5 = spy5d != null ? `SPY 5d ${(spy5d * 100).toFixed(1)}%` : "SPY n/a";
  const s10 = spy10d != null ? `, 10d ${(spy10d * 100).toFixed(1)}%` : "";
  const zStr = spyZ != null ? ` (z=${spyZ.toFixed(1)})` : "";

  return { score, maxScore: 1.5, z_score: spyZ, data_quality: "direct", signal: `${s5}${s10}${zStr}`, confirming };
}

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
  if (dims.credit.confirming) confirming.push("credit stress");
  if (dims.volatility.confirming) confirming.push("volatility");
  if (dims.breadth.confirming) confirming.push("breadth deterioration");
  if (dims.stress_confirmation.confirming) confirming.push("cross-asset confirmation");
  if (dims.trend.confirming) confirming.push("trend damage");

  const quiet: string[] = [];
  if (!dims.credit.confirming) quiet.push("credit");
  if (!dims.volatility.confirming) quiet.push("volatility");
  if (!dims.breadth.confirming) quiet.push("breadth");
  if (!dims.stress_confirmation.confirming) quiet.push("cross-asset signals");
  if (!dims.trend.confirming) quiet.push("trend");

  if (count === 1) return `Early signal: ${confirming[0]} is showing stress. Other dimensions remain quiet.`;

  const confirmStr =
    confirming.length === 1
      ? confirming[0]
      : confirming.slice(0, -1).join(", ") + ` and ${confirming[confirming.length - 1]}`;
  const c0 = confirmStr.charAt(0).toUpperCase() + confirmStr.slice(1);

  const quietStr =
    quiet.length > 0
      ? ` ${quiet.join(" and ")} ${quiet.length === 1 ? "is" : "are"} not yet confirming.`
      : "";

  const liqStr =
    liqBonus > 0
      ? " Simultaneous broad selling across equities and bonds suggests liquidation pressure."
      : "";

  return `${c0} are confirming stress.${quietStr}${liqStr}`;
}

// ── Route handler ─────────────────────────────────────────────────────────────

export async function GET() {
  if (!FMP_KEY) return NextResponse.json({ error: "no_key" }, { status: 503 });

  const fromDate = new Date(Date.now() - 120 * 24 * 60 * 60 * 1000)
    .toISOString()
    .slice(0, 10);

  const [closesResult, vixResult] = await Promise.allSettled([
    fetchCloses(fromDate),
    fetchVix(fromDate),
  ]);

  const closes = closesResult.status === "fulfilled" ? closesResult.value : {};
  const { closes: vixCloses, level: vixLevel } =
    vixResult.status === "fulfilled" ? vixResult.value : { closes: [], level: null };

  const credit = scoreCredit(closes);
  const volatility = scoreVolatility(closes, vixLevel, vixCloses);
  const breadth = scoreBreadth(closes);
  const stress_confirmation = scoreStressConfirmation(closes);
  const trend = scoreTrend(closes);
  const liqBonus = liquidationBonus(closes);

  const dimensions = { credit, volatility, breadth, stress_confirmation, trend };
  const confirmation_count = Object.values(dimensions).filter((d) => d.confirming).length;

  const raw =
    credit.score + volatility.score + breadth.score + stress_confirmation.score + trend.score + liqBonus;
  const market_stress_score = clamp(parseFloat(raw.toFixed(1)), 0, 10);

  const { label: stress_label, color: stress_color } = labelFor(market_stress_score);

  const unavailableCount = Object.values(dimensions).filter(
    (d) => d.data_quality === "unavailable",
  ).length;
  const confidence: "high" | "medium" | "low" =
    unavailableCount >= 2 ? "low" : confirmation_count >= 3 ? "high" : "medium";

  const payload: MarketStressPayload = {
    market_stress_score,
    stress_label,
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
    headers: { "Cache-Control": "s-maxage=300, stale-while-revalidate=60" },
  });
}
