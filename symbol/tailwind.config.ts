import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./src/**/*.{js,ts,jsx,tsx,mdx}"],
  theme: {
    extend: {
      colors: {
        background: "#0A0A0A",
        surface: "#111111",
        "surface-2": "#1A1A1A",
        border: "#2A2A2A",
        text: "#E8E0D0",
        "text-muted": "#8A8278",
        accent: "#F97316",
        "accent-dim": "#7C3A0A",
      },
      fontFamily: {
        mono: ["'DM Mono'", "monospace"],
        sans: ["'DM Sans'", "sans-serif"],
        display: ["'Instrument Serif'", "serif"],
      },
    },
  },
  plugins: [],
};

export default config;
