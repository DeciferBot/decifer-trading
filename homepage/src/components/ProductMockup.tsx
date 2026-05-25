const TABS = ["Now", "Why", "Alpha", "Portfolio", "Ask"] as const;

interface CardProps {
  label: string;
  content: string;
  sub?: string;
  badge?: { text: string; color: "amber" | "slate" | "green" | "orange" };
}

function Card({ label, content, sub, badge }: CardProps) {
  const badgeColors = {
    amber: { bg: "rgba(245,158,11,0.12)", color: "#f59e0b", border: "rgba(245,158,11,0.25)" },
    slate: { bg: "rgba(100,116,139,0.12)", color: "#94a3b8", border: "rgba(100,116,139,0.25)" },
    green: { bg: "rgba(16,185,129,0.12)", color: "#10b981", border: "rgba(16,185,129,0.25)" },
    orange: { bg: "rgba(240,90,40,0.10)", color: "var(--orange)", border: "rgba(240,90,40,0.25)" },
  };

  return (
    <div
      style={{
        background: "rgba(17, 24, 38, 0.9)",
        border: "1px solid rgba(26,40,64,0.8)",
        borderRadius: "10px",
        padding: "12px 14px",
      }}
    >
      <div style={{ fontSize: "9px", fontWeight: 700, color: "#3d5166", letterSpacing: "0.08em", textTransform: "uppercase", marginBottom: "5px" }}>
        {label}
      </div>
      <div style={{ fontSize: "12px", fontWeight: 600, color: "#e8f0fa", lineHeight: 1.4, marginBottom: sub || badge ? "6px" : 0 }}>
        {content}
      </div>
      {sub && (
        <div style={{ fontSize: "10px", color: "#7a8fa8", lineHeight: 1.4 }}>{sub}</div>
      )}
      {badge && (
        <div
          style={{
            display: "inline-flex",
            alignItems: "center",
            marginTop: "6px",
            padding: "2px 7px",
            borderRadius: "100px",
            fontSize: "9px",
            fontWeight: 700,
            letterSpacing: "0.05em",
            background: badgeColors[badge.color].bg,
            color: badgeColors[badge.color].color,
            border: `1px solid ${badgeColors[badge.color].border}`,
          }}
        >
          {badge.text}
        </div>
      )}
    </div>
  );
}

export function ProductMockup() {
  return (
    <div
      style={{
        width: "100%",
        maxWidth: "340px",
        background: "#070a12",
        border: "1px solid rgba(26,40,64,0.9)",
        borderRadius: "24px",
        overflow: "hidden",
        boxShadow: "0 0 0 1px rgba(240,90,40,0.06), 0 40px 80px rgba(0,0,0,0.6), 0 0 60px rgba(240,90,40,0.04)",
      }}
    >
      {/* Device chrome */}
      <div
        style={{
          padding: "14px 18px 0",
          borderBottom: "1px solid rgba(26,40,64,0.6)",
          background: "rgba(7, 10, 18, 0.95)",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "12px" }}>
          <div style={{ display: "flex", alignItems: "center", gap: "7px" }}>
            {/* Mini mark */}
            <svg width="18" height="18" viewBox="0 0 40 40" fill="none" aria-hidden="true">
              <path d="M 20 30 L 7 24 L 20 18" stroke="var(--orange)" strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round" />
              <path d="M 20 22 L 33 16 L 20 10" stroke="#e8f0fa" strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
            <span style={{ fontSize: "11px", fontWeight: 700, color: "#e8f0fa", letterSpacing: "-0.01em" }}>
              DECIFER <span style={{ color: "#7a8fa8", fontWeight: 500 }}>Trading</span>
            </span>
          </div>
          <div
            style={{
              fontSize: "9px",
              fontWeight: 600,
              padding: "2px 7px",
              borderRadius: "100px",
              background: "rgba(16,185,129,0.12)",
              color: "#10b981",
              border: "1px solid rgba(16,185,129,0.25)",
            }}
          >
            Live
          </div>
        </div>

        {/* Tabs */}
        <div style={{ display: "flex", gap: "0" }}>
          {TABS.map((tab, i) => (
            <div
              key={tab}
              style={{
                flex: 1,
                textAlign: "center",
                paddingBottom: "10px",
                fontSize: "10px",
                fontWeight: i === 0 ? 700 : 500,
                color: i === 0 ? "var(--orange)" : "#3d5166",
                borderBottom: i === 0 ? "2px solid var(--orange)" : "2px solid transparent",
                cursor: "default",
              }}
            >
              {tab}
            </div>
          ))}
        </div>
      </div>

      {/* Content — "Now" tab */}
      <div style={{ padding: "12px", display: "flex", flexDirection: "column", gap: "8px" }}>
        <Card
          label="Market mood"
          content="Risk-on, but selective."
          sub="Growth appetite is leading. Breadth is not confirming yet."
          badge={{ text: "Active", color: "green" }}
        />
        <Card
          label="What changed"
          content="Semiconductors leading. Defensives retreating."
          sub="The shift began at the open and has held through the session."
        />
        <Card
          label="Why it matters"
          content="Growth appetite is driving sector rotation, not broad market strength."
          badge={{ text: "Context", color: "orange" }}
        />
        <Card
          label="Under review"
          content="AI infrastructure names watching for follow-through."
          badge={{ text: "Under review", color: "amber" }}
        />
        <Card
          label="Blocked for now"
          content="Extended moves with stretched risk/reward are paused."
          badge={{ text: "Blocked", color: "slate" }}
        />
      </div>

      {/* Ask bar */}
      <div
        style={{
          margin: "0 12px 12px",
          padding: "10px 12px",
          borderRadius: "10px",
          background: "rgba(240,90,40,0.06)",
          border: "1px solid rgba(240,90,40,0.15)",
          display: "flex",
          alignItems: "center",
          gap: "8px",
        }}
      >
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="var(--orange)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <circle cx="11" cy="11" r="8" /><path d="m21 21-4.35-4.35" />
        </svg>
        <span style={{ fontSize: "10px", color: "#7a8fa8" }}>
          Ask DECIFER — "Why are semiconductors moving?"
        </span>
      </div>
    </div>
  );
}
