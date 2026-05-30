import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./src/**/*.{js,ts,jsx,tsx,mdx}"],
  theme: {
    extend: {
      colors: {
        bg: "#0A0A0A",
        surface: "#111111",
        border: "#1E1E1E",
        "border-bright": "#2A2A2A",
        text: "#E8E0D0",
        "text-muted": "#6B6358",
        "text-dim": "#3D3830",
        accent: "#F97316",
        "accent-dim": "#7C3910",
        green: "#22C55E",
        "green-dim": "#14532D",
        amber: "#F59E0B",
        "amber-dim": "#78350F",
        red: "#EF4444",
        "red-dim": "#7F1D1D",
      },
      fontFamily: {
        mono: ["'IBM Plex Mono'", "monospace"],
        sans: ["'IBM Plex Sans'", "sans-serif"],
      },
      fontSize: {
        "2xs": ["10px", "14px"],
        xs: ["11px", "16px"],
        sm: ["12px", "18px"],
        base: ["13px", "20px"],
        lg: ["14px", "20px"],
        xl: ["16px", "22px"],
        "2xl": ["20px", "26px"],
        "3xl": ["28px", "34px"],
      },
    },
  },
  plugins: [],
};

export default config;
