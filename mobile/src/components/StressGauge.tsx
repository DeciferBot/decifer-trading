"use client";

import type { MarketStressPayload } from "@/app/api/market-stress/route";

// ── SVG helpers ───────────────────────────────────────────────────────────────
// All angles are clockwise degrees from north (12 o'clock)

function polar(cx: number, cy: number, r: number, deg: number) {
  const rad = (deg * Math.PI) / 180;
  return { x: cx + r * Math.sin(rad), y: cy - r * Math.cos(rad) };
}

function arc(cx: number, cy: number, r: number, startDeg: number, endDeg: number): string {
  const s = polar(cx, cy, r, startDeg % 360);
  const e = polar(cx, cy, r, endDeg % 360);
  const sweep = endDeg - startDeg;
  if (Math.abs(sweep) < 0.3) return "";
  return `M ${s.x.toFixed(2)} ${s.y.toFixed(2)} A ${r} ${r} 0 ${sweep > 180 ? 1 : 0} 1 ${e.x.toFixed(2)} ${e.y.toFixed(2)}`;
}

// ── Gauge constants ───────────────────────────────────────────────────────────

const CX = 110;
const CY = 105;
const R = 76;
const SW = 11;          // stroke width of track
const NEEDLE_LEN = 62;
const TAIL_LEN = 13;
const START = 240;      // score=0 → 8 o'clock
const SWEEP = 240;      // total degrees

const ZONES = [
  { lo: 0, hi: 2,  color: "#10b981" },
  { lo: 2, hi: 4,  color: "#84cc16" },
  { lo: 4, hi: 6,  color: "#eab308" },
  { lo: 6, hi: 8,  color: "#f97316" },
  { lo: 8, hi: 10, color: "#ef4444" },
];

function scoreDeg(score: number) {
  return START + (score / 10) * SWEEP;
}

function activeColor(score: number): string {
  for (const z of [...ZONES].reverse()) {
    if (score >= z.lo) return z.color;
  }
  return ZONES[0].color;
}

// ── Sub-components ────────────────────────────────────────────────────────────

function DimBar({
  label,
  score,
  maxScore,
  confirming,
}: {
  label: string;
  score: number;
  maxScore: number;
  confirming: boolean;
}) {
  const pct = Math.min(100, (score / maxScore) * 100);
  return (
    <div className="flex flex-col gap-1">
      <div className="w-full rounded-full" style={{ height: 4, background: "rgba(255,255,255,0.07)" }}>
        <div
          style={{
            width: `${pct}%`,
            height: "100%",
            background: confirming ? "#f97316" : "#334155",
            borderRadius: "9999px",
            transition: "width 1s ease",
          }}
        />
      </div>
      <span
        className="text-[9px] font-semibold text-center"
        style={{ color: confirming ? "#fb923c" : "#475569" }}
      >
        {label}
      </span>
    </div>
  );
}

// ── Main gauge ────────────────────────────────────────────────────────────────

const LABEL_TEXT: Record<string, string> = {
  CALM: "Calm",
  STRESS_BUILDING: "Building",
  ELEVATED_STRESS: "Elevated",
  MULTI_DIMENSION_STRESS: "High Stress",
  PANIC_CONDITIONS: "Panic",
};

export default function StressGauge({ data }: { data: MarketStressPayload }) {
  const {
    market_stress_score: score,
    stress_label,
    stress_color,
    confirmation_count,
    dimensions,
    explanation,
    confidence,
  } = data;

  const needleDeg = scoreDeg(score);
  const tip = polar(CX, CY, NEEDLE_LEN, needleDeg);
  const tail = polar(CX, CY, -TAIL_LEN, needleDeg);
  const color = activeColor(score);

  return (
    <div
      className="rounded-2xl overflow-hidden"
      style={{ background: "#0c1520", border: "1px solid rgba(255,255,255,0.08)" }}
    >
      {/* Header */}
      <div className="px-4 pt-4 pb-1 flex items-center justify-between">
        <div>
          <p className="text-[10px] font-bold uppercase tracking-[0.15em]" style={{ color: "#f97316" }}>
            Market Stress Monitor
          </p>
          <p className="text-[11px] text-slate-500 mt-0.5">
            Multi-dimensional propagation tracker
          </p>
        </div>
        <div className="flex items-center gap-1.5">
          <span
            className="text-[9px] font-semibold px-2 py-0.5 rounded-full"
            style={{ background: "rgba(99,102,241,0.12)", color: "#818cf8" }}
          >
            Shadow
          </span>
          <span
            className="text-[9px] font-semibold px-2 py-0.5 rounded-full"
            style={{
              background: confidence === "high" ? "rgba(16,185,129,0.1)" : "rgba(255,255,255,0.05)",
              color: confidence === "high" ? "#34d399" : "#475569",
            }}
          >
            {confidence}
          </span>
        </div>
      </div>

      {/* Speedometer SVG */}
      <div className="flex justify-center px-2">
        <svg
          viewBox="0 0 220 152"
          style={{ width: "100%", maxWidth: 310, overflow: "visible" }}
          aria-label={`Market stress score ${score} out of 10`}
        >
          <defs>
            <filter id="msGlow" x="-50%" y="-50%" width="200%" height="200%">
              <feGaussianBlur stdDeviation="3" result="blur" />
              <feMerge>
                <feMergeNode in="blur" />
                <feMergeNode in="SourceGraphic" />
              </feMerge>
            </filter>
            <filter id="msNeedleGlow" x="-100%" y="-100%" width="300%" height="300%">
              <feGaussianBlur stdDeviation="1.5" result="blur" />
              <feMerge>
                <feMergeNode in="blur" />
                <feMergeNode in="SourceGraphic" />
              </feMerge>
            </filter>
          </defs>

          {/* ── Background track ── */}
          <path
            d={arc(CX, CY, R, START, START + SWEEP)}
            fill="none"
            stroke="rgba(255,255,255,0.04)"
            strokeWidth={SW + 4}
            strokeLinecap="round"
          />

          {/* ── Zone arcs ── */}
          {ZONES.map((z) => {
            const zStart = START + (z.lo / 10) * SWEEP;
            const zEnd = START + (z.hi / 10) * SWEEP;
            const reached = score >= z.hi;
            const partial = score > z.lo && score < z.hi;
            const opacity = reached ? 0.9 : partial ? 0.55 : 0.13;
            return (
              <path
                key={z.lo}
                d={arc(CX, CY, R, zStart, zEnd)}
                fill="none"
                stroke={z.color}
                strokeWidth={SW}
                strokeLinecap="butt"
                opacity={opacity}
              />
            );
          })}

          {/* ── Progress glow line ── */}
          {score > 0.3 && (
            <path
              d={arc(CX, CY, R, START, scoreDeg(score))}
              fill="none"
              stroke={color}
              strokeWidth={2.5}
              strokeLinecap="round"
              opacity={0.7}
              filter="url(#msGlow)"
            />
          )}

          {/* ── Tick marks at 0, 2, 4, 6, 8, 10 ── */}
          {[0, 2, 4, 6, 8, 10].map((v) => {
            const d = scoreDeg(v);
            const inner = polar(CX, CY, R - SW / 2 - 4, d);
            const outer = polar(CX, CY, R + SW / 2 + 4, d);
            return (
              <line
                key={v}
                x1={inner.x.toFixed(2)} y1={inner.y.toFixed(2)}
                x2={outer.x.toFixed(2)} y2={outer.y.toFixed(2)}
                stroke="rgba(255,255,255,0.22)"
                strokeWidth={1.5}
                strokeLinecap="round"
              />
            );
          })}

          {/* ── Tick labels at 0, 5, 10 ── */}
          {[0, 5, 10].map((v) => {
            const pt = polar(CX, CY, R + 17, scoreDeg(v));
            return (
              <text
                key={v}
                x={pt.x.toFixed(2)}
                y={pt.y.toFixed(2)}
                textAnchor="middle"
                dominantBaseline="middle"
                fontSize={8}
                fill="rgba(255,255,255,0.28)"
                fontFamily="system-ui,sans-serif"
              >
                {v}
              </text>
            );
          })}

          {/* ── Needle ── */}
          <line
            x1={tail.x.toFixed(2)} y1={tail.y.toFixed(2)}
            x2={tip.x.toFixed(2)} y2={tip.y.toFixed(2)}
            stroke="white"
            strokeWidth={2.2}
            strokeLinecap="round"
            filter="url(#msNeedleGlow)"
            opacity={0.92}
          />

          {/* ── Pivot ── */}
          <circle cx={CX} cy={CY} r={6} fill={color} opacity={0.9} />
          <circle cx={CX} cy={CY} r={3.5} fill="white" opacity={0.95} />

          {/* ── Score number ── */}
          <text
            x={CX}
            y={CY + 20}
            textAnchor="middle"
            dominantBaseline="middle"
            fontSize={28}
            fontWeight="700"
            fill="white"
            fontFamily="system-ui,sans-serif"
            letterSpacing="-0.5"
          >
            {score.toFixed(1)}
          </text>

          {/* ── Stress label ── */}
          <text
            x={CX}
            y={CY + 38}
            textAnchor="middle"
            dominantBaseline="middle"
            fontSize={9.5}
            fontWeight="700"
            fill={stress_color}
            fontFamily="system-ui,sans-serif"
            letterSpacing="0.8"
          >
            {(LABEL_TEXT[stress_label] ?? stress_label).toUpperCase()}
          </text>
        </svg>
      </div>

      {/* ── Dimension bars — 2 rows of 4 ── */}
      <div className="px-4 pb-3 space-y-2">
        {/* Tier 1 + 2 */}
        <div className="grid grid-cols-4 gap-2">
          <DimBar label="Credit"  score={dimensions.credit.score}              maxScore={dimensions.credit.maxScore}              confirming={dimensions.credit.confirming} />
          <DimBar label="Vol"     score={dimensions.volatility.score}          maxScore={dimensions.volatility.maxScore}          confirming={dimensions.volatility.confirming} />
          <DimBar label="Breadth" score={dimensions.breadth.score}             maxScore={dimensions.breadth.maxScore}             confirming={dimensions.breadth.confirming} />
          <DimBar label="Confirm" score={dimensions.stress_confirmation.score} maxScore={dimensions.stress_confirmation.maxScore} confirming={dimensions.stress_confirmation.confirming} />
        </div>
        {/* Tier 3 + 4 */}
        <div className="grid grid-cols-4 gap-2">
          <DimBar label="Trend"   score={dimensions.trend.score}         maxScore={dimensions.trend.maxScore}         confirming={dimensions.trend.confirming} />
          <DimBar label="Carry"   score={dimensions.carry.score}         maxScore={dimensions.carry.maxScore}         confirming={dimensions.carry.confirming} />
          <DimBar label="Cu/Au"   score={dimensions.copper_gold.score}   maxScore={dimensions.copper_gold.maxScore}   confirming={dimensions.copper_gold.confirming} />
          <DimBar label="News"    score={dimensions.news_velocity.score} maxScore={dimensions.news_velocity.maxScore} confirming={dimensions.news_velocity.confirming} />
        </div>
        {/* Tier labels */}
        <div className="flex justify-between px-0.5">
          <span className="text-[8px] text-slate-600">── Core ──────────────</span>
          <span className="text-[8px] text-slate-600">── Leading ───────────</span>
        </div>
      </div>

      {/* ── Explanation ── */}
      <div
        className="mx-4 mb-4 px-3 py-2.5 rounded-xl"
        style={{ background: "rgba(255,255,255,0.03)", border: "1px solid rgba(255,255,255,0.06)" }}
      >
        <p className="text-[11px] text-slate-300 leading-relaxed">{explanation}</p>
        <p className="text-[10px] text-slate-600 mt-1.5">
          {confirmation_count} of 5 dimensions confirming · shadow mode only
        </p>
      </div>
    </div>
  );
}
