import { ImageResponse } from "next/og";

export const runtime = "edge";
export const alt = "Decifer Macro Drivers";
export const size = { width: 1200, height: 630 };
export const contentType = "image/png";

export default function OGImage() {
  return new ImageResponse(
    (
      <div style={{ width: "100%", height: "100%", display: "flex", flexDirection: "column", justifyContent: "center", padding: "72px 80px", background: "#080d1a", fontFamily: "monospace" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 16, marginBottom: 40 }}>
          <span style={{ color: "#f97316", fontSize: 18, letterSpacing: "0.2em", textTransform: "uppercase" }}>DECIFER</span>
        </div>
        <div style={{ color: "#f8fafc", fontSize: 56, fontWeight: 700, lineHeight: 1.1, marginBottom: 24 }}>Macro Drivers</div>
        <div style={{ color: "#94a3b8", fontSize: 24, lineHeight: 1.5, maxWidth: 680 }}>Live macro driver state — what forces are active and why they matter.</div>
        <div style={{ position: "absolute", bottom: 0, left: 0, right: 0, height: 4, background: "linear-gradient(90deg, #f97316 0%, #ea580c 100%)" }} />
      </div>
    ),
    { ...size }
  );
}
