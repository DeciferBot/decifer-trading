"use client";
import { useState } from "react";

const SIZE = 32;

export function SymbolLogo({ symbol, size = SIZE }: { symbol: string; size?: number }) {
  const [failed, setFailed] = useState(false);
  const src = `https://financialmodelingprep.com/image-stock/${symbol}.png`;

  if (failed) {
    return (
      <div style={{
        width: size, height: size, borderRadius: size * 0.25,
        background: "#1a1a1a", border: "1px solid #2a2a2a",
        display: "flex", alignItems: "center", justifyContent: "center",
        flexShrink: 0,
        fontSize: size <= 24 ? 8 : 10,
        fontWeight: 700,
        color: "#555",
        fontFamily: "var(--mono)",
        letterSpacing: "-0.02em",
      }}>
        {symbol.slice(0, 3)}
      </div>
    );
  }

  return (
    // eslint-disable-next-line @next/next/no-img-element
    <img
      src={src}
      alt={symbol}
      width={size}
      height={size}
      onError={() => setFailed(true)}
      style={{
        width: size, height: size,
        borderRadius: size * 0.25,
        objectFit: "contain",
        background: "transparent",
        flexShrink: 0,
        display: "block",
      }}
    />
  );
}
