interface LogoMarkProps {
  size?: number;
  className?: string;
}

/**
 * DECIFER parent brand mark.
 * Two angle brackets (< >) with a vertical offset — a dyad between two
 * perspectives, interpreting complexity from both sides.
 * Left bracket (orange) sits lower; right bracket (white) sits higher.
 */
export function LogoMark({ size = 36, className = "" }: LogoMarkProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 40 40"
      fill="none"
      aria-hidden="true"
      className={className}
    >
      {/* < left bracket — lower position, orange */}
      <path
        d="M 20 30 L 7 24 L 20 18"
        stroke="#f97316"
        strokeWidth="2.6"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      {/* > right bracket — upper position, white */}
      <path
        d="M 20 22 L 33 16 L 20 10"
        stroke="#e8f0fa"
        strokeWidth="2.6"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

interface LogoProps {
  product?: "Trading" | "Learning" | null;
  size?: "sm" | "md" | "lg";
  className?: string;
}

export function Logo({ product = "Trading", size = "md", className = "" }: LogoProps) {
  const markSize = size === "sm" ? 26 : size === "lg" ? 46 : 34;
  const textClass =
    size === "sm"
      ? "text-sm font-bold tracking-tight"
      : size === "lg"
      ? "text-2xl font-bold tracking-tight"
      : "text-lg font-bold tracking-tight";

  return (
    <div className={`flex items-center gap-2 ${className}`}>
      <LogoMark size={markSize} />
      <span className={textClass} style={{ color: "var(--text-1)", letterSpacing: "-0.01em" }}>
        <span style={{ color: "var(--text-1)" }}>DECIFER</span>
        {product && (
          <span style={{ color: "var(--text-2)", fontWeight: 500 }}> {product}</span>
        )}
      </span>
    </div>
  );
}
