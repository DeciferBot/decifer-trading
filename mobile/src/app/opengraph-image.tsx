import { ImageResponse } from "next/og";

export const runtime = "edge";
export const alt = "Decifer Market Intelligence";
export const size = { width: 1200, height: 630 };
export const contentType = "image/png";

export default function RootOGImage() {
  return new ImageResponse(
    (
      <div
        style={{
          width: "100%",
          height: "100%",
          display: "flex",
          flexDirection: "column",
          justifyContent: "center",
          padding: "72px 80px",
          background: "#080d1a",
          fontFamily: "monospace",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 16, marginBottom: 40 }}>
          <div
            style={{
              width: 44,
              height: 44,
              borderRadius: 10,
              background: "#0a0a0a",
              border: "1.5px solid #f97316",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              fontSize: 22,
              color: "#f97316",
            }}
          >
            ⟨⟩
          </div>
          <span style={{ color: "#f97316", fontSize: 18, letterSpacing: "0.2em", textTransform: "uppercase" }}>
            DECIFER
          </span>
        </div>
        <div style={{ color: "#f8fafc", fontSize: 56, fontWeight: 700, lineHeight: 1.1, marginBottom: 24 }}>
          Market Intelligence
        </div>
        <div style={{ color: "#94a3b8", fontSize: 24, lineHeight: 1.5, maxWidth: 680 }}>
          Signals, themes, and evidence — for active investors.
        </div>
        <div
          style={{
            position: "absolute",
            bottom: 0,
            left: 0,
            right: 0,
            height: 4,
            background: "linear-gradient(90deg, #f97316 0%, #ea580c 100%)",
          }}
        />
      </div>
    ),
    { ...size }
  );
}
