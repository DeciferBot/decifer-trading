import { useState, useMemo } from "react";
import _ from "lodash";

// ── Spec data pulled from state/specs/ ──────────────────────────────────────
const SPECS = [
  {
    id: "feat-consensus-threshold",
    title: "Raise Consensus Threshold",
    status: "in_progress",
    priority: "P0",
    phase: "A",
    designed_date: "2026-03-25",
    started_date: "2026-03-27",
    completed_date: null,
    summary: "Raise the minimum signal consensus required to trigger entries — currently too permissive, letting marginal setups through.",
    files_affected: ["signals.py", "config.py", "bot.py"],
    dependencies: [],
    branch: "feat/consensus-threshold",
  },
  {
    id: "feat-short-scanner",
    title: "Short-Candidate Scanner",
    status: "spec_complete",
    priority: "P0",
    phase: "A",
    designed_date: "2026-03-26",
    started_date: null,
    completed_date: null,
    summary: "Build a scanner that identifies short candidates using relative weakness, breakdown patterns, and sector rotation signals.",
    files_affected: ["scanner.py", "signals.py", "universe.py"],
    dependencies: [],
    branch: "feat/short-scanner",
  },
  {
    id: "feat-directional-skew-dashboard",
    title: "Directional Skew Dashboard",
    status: "spec_complete",
    priority: "P1",
    phase: "A",
    designed_date: "2026-03-27",
    started_date: null,
    completed_date: null,
    summary: "Dashboard widget showing portfolio directional skew — long/short ratio, net exposure, bias drift over time.",
    files_affected: ["dashboard.py", "risk.py", "portfolio.py"],
    dependencies: ["feat-short-scanner"],
    branch: "feat/directional-skew",
  },
  {
    id: "feat-direction-agnostic-signals",
    title: "Direction-Agnostic Signals",
    status: "backlog",
    priority: "P1",
    phase: "B",
    designed_date: null,
    started_date: null,
    completed_date: null,
    summary: "Refactor 9-dimension signal scoring to be direction-agnostic — score setup strength regardless of direction, apply directional lens separately.",
    files_affected: ["signals.py", "agents.py", "scoring.py"],
    dependencies: ["feat-short-scanner"],
    branch: null,
  },
  {
    id: "feat-mean-reversion-dimension",
    title: "Mean-Reversion Dimension",
    status: "backlog",
    priority: "P1",
    phase: "B",
    designed_date: null,
    started_date: null,
    completed_date: null,
    summary: "Add a 10th signal dimension for mean-reversion setups — Bollinger squeeze, RSI divergence, Z-score deviation.",
    files_affected: ["signals.py", "indicators.py"],
    dependencies: ["feat-direction-agnostic-signals"],
    branch: null,
  },
  {
    id: "feat-alphalens-validation",
    title: "Signal Validation (Alphalens)",
    status: "backlog",
    priority: "P2",
    phase: "C",
    designed_date: null,
    started_date: null,
    completed_date: null,
    summary: "Validate each signal dimension's predictive power with Alphalens — IC scores, turnover, factor returns. Kill weak dimensions.",
    files_affected: ["validation.py", "signals.py", "backtest.py"],
    dependencies: ["feat-direction-agnostic-signals", "feat-mean-reversion-dimension"],
    branch: null,
  },
  {
    id: "feat-hmm-regime-detection",
    title: "HMM Regime Detection",
    status: "backlog",
    priority: "P2",
    phase: "D",
    designed_date: null,
    started_date: null,
    completed_date: null,
    summary: "Hidden Markov Model to classify market regime (bull/bear/neutral) and gate entries — suppress longs in bear regime.",
    files_affected: ["regime.py", "signals.py", "risk.py", "bot.py"],
    dependencies: ["feat-alphalens-validation"],
    branch: null,
  },
  {
    id: "feat-walk-forward-calibration",
    title: "Walk-Forward Calibration",
    status: "backlog",
    priority: "P2",
    phase: "D",
    designed_date: null,
    started_date: null,
    completed_date: null,
    summary: "Walk-forward optimization to calibrate signal dimension weights — rolling window optimization that adapts to changing conditions.",
    files_affected: ["calibration.py", "signals.py", "backtest.py"],
    dependencies: ["feat-hmm-regime-detection", "feat-alphalens-validation"],
    branch: null,
  },
  {
    id: "feat-019",
    title: "Multi-Account Position Aggregation",
    status: "backlog",
    priority: "P1",
    phase: "E",
    designed_date: "2026-03-28",
    started_date: null,
    completed_date: null,
    summary: "Aggregate positions across multiple IBKR accounts into a single view — unified P&L, exposure, and risk metrics.",
    files_affected: ["portfolio.py", "config.py", "dashboard.py"],
    dependencies: [],
    branch: null,
  },
  {
    id: "feat-020",
    title: "Per-Account Risk Limits",
    status: "backlog",
    priority: "P1",
    phase: "E",
    designed_date: "2026-03-28",
    started_date: null,
    completed_date: null,
    summary: "Enforce independent risk limits (max drawdown, position size, sector concentration) per account rather than globally.",
    files_affected: ["risk.py", "orders.py", "config.py"],
    dependencies: ["feat-019"],
    branch: null,
  },
  {
    id: "feat-021",
    title: "Account-Aware Order Router",
    status: "backlog",
    priority: "P1",
    phase: "E",
    designed_date: "2026-03-28",
    started_date: null,
    completed_date: null,
    summary: "Route orders to the correct IBKR account based on strategy assignment — each strategy maps to one or more accounts.",
    files_affected: ["orders.py", "bot.py", "config.py"],
    dependencies: ["feat-019", "feat-020"],
    branch: null,
  },
  {
    id: "feat-022",
    title: "Cross-Account Correlation Guard",
    status: "backlog",
    priority: "P2",
    phase: "E",
    designed_date: "2026-03-28",
    started_date: null,
    completed_date: null,
    summary: "Detect and flag when positions across accounts create unintended correlated exposure.",
    files_affected: ["risk.py", "portfolio.py", "dashboard.py"],
    dependencies: ["feat-019"],
    branch: null,
  },
  {
    id: "feat-023",
    title: "Unified Trade Journal",
    status: "backlog",
    priority: "P2",
    phase: "E",
    designed_date: "2026-03-28",
    started_date: null,
    completed_date: null,
    summary: "Single trade journal that logs entries/exits across all accounts with account tag, strategy tag, and P&L attribution.",
    files_affected: ["journal.py", "orders.py", "dashboard.py"],
    dependencies: ["feat-021"],
    branch: null,
  },
  {
    id: "feat-024",
    title: "Account Performance Comparison",
    status: "backlog",
    priority: "P2",
    phase: "E",
    designed_date: "2026-03-28",
    started_date: null,
    completed_date: null,
    summary: "Side-by-side performance comparison dashboard for all accounts — Sharpe, max drawdown, win rate, P&L curves.",
    files_affected: ["dashboard.py", "portfolio.py", "metrics.py"],
    dependencies: ["feat-019", "feat-023"],
    branch: null,
  },
];

// ── Column definitions ──────────────────────────────────────────────────────
const COLUMNS = [
  { id: "backlog", label: "Backlog", color: "#868e96", bg: "#2a2d35", count_bg: "#3a3d45" },
  { id: "spec_complete", label: "Proposal", color: "#74c0fc", bg: "#1a2a3a", count_bg: "#1e3448" },
  { id: "in_progress", label: "In Progress", color: "#ffd43b", bg: "#2a2518", count_bg: "#3a3520" },
  { id: "complete", label: "Shipped", color: "#51cf66", bg: "#1a2a1e", count_bg: "#1e3a22" },
];

const PRIORITY_COLORS = {
  P0: { bg: "#ff6b6b22", text: "#ff6b6b", border: "#ff6b6b44" },
  P1: { bg: "#ffd43b22", text: "#ffd43b", border: "#ffd43b44" },
  P2: { bg: "#74c0fc22", text: "#74c0fc", border: "#74c0fc44" },
};

const PHASE_LABELS = {
  A: "Phase A — Immediate",
  B: "Phase B — Core Refactor",
  C: "Phase C — Validation",
  D: "Phase D — Intelligence",
  E: "Phase E — Multi-Account",
};

// ── Feature Card ────────────────────────────────────────────────────────────
function FeatureCard({ spec, allSpecs, onMove }) {
  const [expanded, setExpanded] = useState(false);
  const pri = PRIORITY_COLORS[spec.priority] || PRIORITY_COLORS.P2;

  const depNames = (spec.dependencies || []).map((depId) => {
    const dep = allSpecs.find((s) => s.id === depId);
    return dep ? dep.title : depId;
  });

  const colIdx = COLUMNS.findIndex((c) => c.id === spec.status);

  return (
    <div
      onClick={() => setExpanded(!expanded)}
      style={{
        backgroundColor: "#1e2a3a",
        borderRadius: "10px",
        padding: "14px 16px",
        marginBottom: "10px",
        borderLeft: `3px solid ${COLUMNS.find((c) => c.id === spec.status)?.color || "#555"}`,
        cursor: "pointer",
        transition: "all 0.2s ease",
        boxShadow: expanded ? "0 4px 20px rgba(0,0,0,0.3)" : "0 1px 4px rgba(0,0,0,0.15)",
      }}
    >
      {/* Header: priority + phase */}
      <div style={{ display: "flex", alignItems: "center", gap: "6px", marginBottom: "8px", flexWrap: "wrap" }}>
        <span
          style={{
            fontSize: "0.6rem",
            fontWeight: 700,
            padding: "2px 8px",
            borderRadius: "4px",
            backgroundColor: pri.bg,
            color: pri.text,
            border: `1px solid ${pri.border}`,
            letterSpacing: "0.5px",
          }}
        >
          {spec.priority}
        </span>
        <span
          style={{
            fontSize: "0.6rem",
            padding: "2px 8px",
            borderRadius: "4px",
            backgroundColor: "#ffffff0a",
            color: "#868e96",
            border: "1px solid #ffffff10",
          }}
        >
          Phase {spec.phase}
        </span>
        {spec.branch && (
          <span
            style={{
              fontSize: "0.55rem",
              padding: "2px 6px",
              borderRadius: "3px",
              backgroundColor: "#51cf6615",
              color: "#51cf66",
              fontFamily: "monospace",
            }}
          >
            {spec.branch.replace("feat/", "")}
          </span>
        )}
      </div>

      {/* Title */}
      <div style={{ fontWeight: 600, fontSize: "0.85rem", color: "#e9ecef", lineHeight: 1.4, marginBottom: "6px" }}>
        {spec.title}
      </div>

      {/* Summary */}
      <div style={{ fontSize: "0.72rem", color: "#868e96", lineHeight: 1.5, marginBottom: "8px" }}>
        {spec.summary}
      </div>

      {/* Dependencies */}
      {depNames.length > 0 && (
        <div style={{ display: "flex", alignItems: "center", gap: "4px", flexWrap: "wrap", marginBottom: "6px" }}>
          <span style={{ fontSize: "0.6rem", color: "#555" }}>Depends on:</span>
          {depNames.map((name, i) => (
            <span
              key={i}
              style={{
                fontSize: "0.58rem",
                padding: "1px 6px",
                borderRadius: "3px",
                backgroundColor: "#ffffff08",
                color: "#adb5bd",
                border: "1px solid #ffffff0a",
              }}
            >
              {name}
            </span>
          ))}
        </div>
      )}

      {/* Dates */}
      <div style={{ display: "flex", gap: "12px", flexWrap: "wrap" }}>
        {spec.designed_date && (
          <span style={{ fontSize: "0.6rem", color: "#555" }}>
            Designed: {spec.designed_date}
          </span>
        )}
        {spec.started_date && (
          <span style={{ fontSize: "0.6rem", color: "#ffd43b88" }}>
            Started: {spec.started_date}
          </span>
        )}
        {spec.completed_date && (
          <span style={{ fontSize: "0.6rem", color: "#51cf6688" }}>
            Shipped: {spec.completed_date}
          </span>
        )}
      </div>

      {/* Expanded: files + move buttons */}
      {expanded && (
        <div style={{ marginTop: "10px", paddingTop: "10px", borderTop: "1px solid #ffffff10" }}>
          {spec.files_affected?.length > 0 && (
            <div style={{ marginBottom: "10px" }}>
              <div style={{ fontSize: "0.6rem", color: "#555", marginBottom: "4px" }}>Files affected:</div>
              <div style={{ display: "flex", gap: "4px", flexWrap: "wrap" }}>
                {spec.files_affected.map((f, i) => (
                  <span
                    key={i}
                    style={{
                      fontSize: "0.6rem",
                      padding: "2px 8px",
                      borderRadius: "4px",
                      backgroundColor: "#ffffff08",
                      color: "#adb5bd",
                      fontFamily: "monospace",
                    }}
                  >
                    {f}
                  </span>
                ))}
              </div>
            </div>
          )}

          {/* Move buttons */}
          <div style={{ display: "flex", gap: "6px", flexWrap: "wrap" }}>
            {colIdx > 0 && (
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  onMove(spec.id, COLUMNS[colIdx - 1].id);
                }}
                style={{
                  fontSize: "0.65rem",
                  padding: "4px 10px",
                  borderRadius: "5px",
                  border: "1px solid #ffffff15",
                  backgroundColor: "#ffffff08",
                  color: "#adb5bd",
                  cursor: "pointer",
                  transition: "all 0.15s",
                }}
              >
                ← {COLUMNS[colIdx - 1].label}
              </button>
            )}
            {colIdx < COLUMNS.length - 1 && (
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  onMove(spec.id, COLUMNS[colIdx + 1].id);
                }}
                style={{
                  fontSize: "0.65rem",
                  padding: "4px 10px",
                  borderRadius: "5px",
                  border: `1px solid ${COLUMNS[colIdx + 1].color}33`,
                  backgroundColor: `${COLUMNS[colIdx + 1].color}15`,
                  color: COLUMNS[colIdx + 1].color,
                  cursor: "pointer",
                  transition: "all 0.15s",
                }}
              >
                {COLUMNS[colIdx + 1].label} →
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

// ── Kanban Column ───────────────────────────────────────────────────────────
function KanbanColumn({ column, specs, allSpecs, onMove }) {
  return (
    <div
      style={{
        flex: 1,
        minWidth: "260px",
        backgroundColor: column.bg,
        borderRadius: "12px",
        padding: "16px 14px",
        border: `1px solid ${column.color}20`,
      }}
    >
      {/* Column header */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "14px" }}>
        <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
          <div style={{ width: "8px", height: "8px", borderRadius: "50%", backgroundColor: column.color }} />
          <span style={{ fontWeight: 700, fontSize: "0.8rem", color: "#e9ecef", letterSpacing: "0.3px" }}>
            {column.label}
          </span>
        </div>
        <span
          style={{
            fontSize: "0.65rem",
            fontWeight: 700,
            padding: "2px 8px",
            borderRadius: "10px",
            backgroundColor: column.count_bg,
            color: column.color,
          }}
        >
          {specs.length}
        </span>
      </div>

      {/* Cards */}
      <div style={{ minHeight: "100px" }}>
        {specs.map((spec) => (
          <FeatureCard key={spec.id} spec={spec} allSpecs={allSpecs} onMove={onMove} />
        ))}
        {specs.length === 0 && (
          <div
            style={{
              padding: "24px 16px",
              textAlign: "center",
              fontSize: "0.72rem",
              color: "#555",
              border: "1px dashed #ffffff10",
              borderRadius: "8px",
            }}
          >
            No features here yet
          </div>
        )}
      </div>
    </div>
  );
}

// ── Main Dashboard ──────────────────────────────────────────────────────────
export default function FeaturePipelineKanban() {
  const [features, setFeatures] = useState(SPECS);
  const [phaseFilter, setPhaseFilter] = useState("all");

  const filtered = useMemo(() => {
    if (phaseFilter === "all") return features;
    return features.filter((f) => f.phase === phaseFilter);
  }, [features, phaseFilter]);

  const grouped = useMemo(() => {
    const g = {};
    COLUMNS.forEach((col) => {
      g[col.id] = filtered.filter((f) => f.status === col.id);
    });
    return g;
  }, [filtered]);

  const handleMove = (specId, newStatus) => {
    setFeatures((prev) =>
      prev.map((f) => {
        if (f.id !== specId) return f;
        const updated = { ...f, status: newStatus };
        if (newStatus === "in_progress" && !f.started_date) {
          updated.started_date = new Date().toISOString().split("T")[0];
        }
        if (newStatus === "complete" && !f.completed_date) {
          updated.completed_date = new Date().toISOString().split("T")[0];
        }
        return updated;
      })
    );
  };

  // Stats
  const total = features.length;
  const shipped = features.filter((f) => f.status === "complete").length;
  const inProg = features.filter((f) => f.status === "in_progress").length;
  const proposed = features.filter((f) => f.status === "spec_complete").length;

  const phases = ["all", "A", "B", "C", "D", "E"];

  return (
    <div
      style={{
        fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif",
        backgroundColor: "#0f1923",
        minHeight: "100vh",
        padding: "24px",
        color: "#e9ecef",
      }}
    >
      {/* Header */}
      <div style={{ marginBottom: "24px" }}>
        <div style={{ display: "flex", alignItems: "center", gap: "10px", marginBottom: "6px" }}>
          <div style={{ width: "8px", height: "8px", borderRadius: "50%", backgroundColor: "#51cf66" }} />
          <span style={{ fontSize: "1rem", fontWeight: 700, letterSpacing: "1px", color: "#e9ecef" }}>
            CHIEF DECIFER
          </span>
          <span style={{ fontSize: "0.7rem", color: "#555", letterSpacing: "0.5px" }}>FEATURE PIPELINE</span>
        </div>
        <div style={{ fontSize: "0.75rem", color: "#868e96" }}>
          Fixing structural bullish bias — {total} features across 4 phases
        </div>
      </div>

      {/* Stats bar */}
      <div style={{ display: "flex", gap: "16px", marginBottom: "20px", flexWrap: "wrap" }}>
        {[
          { label: "Total", value: total, color: "#e9ecef" },
          { label: "Shipped", value: shipped, color: "#51cf66" },
          { label: "In Progress", value: inProg, color: "#ffd43b" },
          { label: "Proposed", value: proposed, color: "#74c0fc" },
          { label: "Backlog", value: total - shipped - inProg - proposed, color: "#868e96" },
        ].map((stat) => (
          <div
            key={stat.label}
            style={{
              padding: "10px 18px",
              borderRadius: "8px",
              backgroundColor: "#1a2332",
              border: "1px solid #ffffff08",
              textAlign: "center",
              minWidth: "80px",
            }}
          >
            <div style={{ fontSize: "1.3rem", fontWeight: 700, color: stat.color }}>{stat.value}</div>
            <div style={{ fontSize: "0.6rem", color: "#555", letterSpacing: "0.5px", textTransform: "uppercase" }}>
              {stat.label}
            </div>
          </div>
        ))}

        {/* Progress bar */}
        <div style={{ flex: 1, minWidth: "200px", display: "flex", flexDirection: "column", justifyContent: "center" }}>
          <div style={{ fontSize: "0.6rem", color: "#555", marginBottom: "6px" }}>
            Pipeline progress: {shipped}/{total} shipped
          </div>
          <div style={{ height: "8px", borderRadius: "4px", backgroundColor: "#1a2332", overflow: "hidden", display: "flex" }}>
            <div style={{ width: `${(shipped / total) * 100}%`, backgroundColor: "#51cf66", transition: "width 0.3s" }} />
            <div style={{ width: `${(inProg / total) * 100}%`, backgroundColor: "#ffd43b", transition: "width 0.3s" }} />
            <div style={{ width: `${(proposed / total) * 100}%`, backgroundColor: "#74c0fc", transition: "width 0.3s" }} />
          </div>
        </div>
      </div>

      {/* Phase filter */}
      <div style={{ display: "flex", gap: "6px", marginBottom: "20px", flexWrap: "wrap" }}>
        {phases.map((p) => (
          <button
            key={p}
            onClick={() => setPhaseFilter(p)}
            style={{
              fontSize: "0.68rem",
              padding: "5px 14px",
              borderRadius: "6px",
              border: phaseFilter === p ? "1px solid #4dabf7" : "1px solid #ffffff10",
              backgroundColor: phaseFilter === p ? "#4dabf720" : "#ffffff05",
              color: phaseFilter === p ? "#4dabf7" : "#868e96",
              cursor: "pointer",
              fontWeight: phaseFilter === p ? 600 : 400,
              transition: "all 0.15s",
            }}
          >
            {p === "all" ? "All Phases" : PHASE_LABELS[p] || `Phase ${p}`}
          </button>
        ))}
      </div>

      {/* Kanban board */}
      <div style={{ display: "flex", gap: "16px", overflowX: "auto", paddingBottom: "20px" }}>
        {COLUMNS.map((col) => (
          <KanbanColumn
            key={col.id}
            column={col}
            specs={grouped[col.id] || []}
            allSpecs={features}
            onMove={handleMove}
          />
        ))}
      </div>

      {/* Footer */}
      <div style={{ marginTop: "20px", fontSize: "0.6rem", color: "#333", textAlign: "center" }}>
        Data source: Chief-Decifer/state/specs/ — Click any card to expand, use arrows to move between stages
      </div>
    </div>
  );
}
