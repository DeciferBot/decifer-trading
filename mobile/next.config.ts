import type { NextConfig } from "next";
import fs from "fs";
import path from "path";

// Read the deployed version for build-time injection into NEXT_PUBLIC_APP_VERSION.
// Resolution order:
//   1. version.json (mobile/ root) — present in Vercel deployment, always wins
//   2. ../version.py (monorepo root) — local dev when running from mobile/
//   3. "dev" fallback
function getSystemVersion(): string {
  try {
    const local = fs.readFileSync(path.join(__dirname, "version.json"), "utf8");
    const v = JSON.parse(local).version;
    if (v) return v;
  } catch {}
  try {
    const file = fs.readFileSync(path.join(__dirname, "../version.py"), "utf8");
    const match = file.match(/__version__ = "([^"]+)"/);
    return match ? match[1] : "dev";
  } catch {
    return "dev";
  }
}

const nextConfig: NextConfig = {
  output: "standalone",

  // Inject system version as a public build-time constant.
  // Consumed by CustomerApp.tsx as process.env.NEXT_PUBLIC_APP_VERSION.
  env: {
    NEXT_PUBLIC_APP_VERSION: getSystemVersion(),
  },

  async redirects() {
    return [
      {
        source: "/",
        destination: "/customer",
        permanent: false,
      },
    ];
  },

  async headers() {
    return [
      {
        // HTML shells must never be cached by Cloudflare.
        // CF free plan enforces edge_cache_ttl >= 7200s which overrides
        // Vercel's Cache-Control: max-age=0. "no-store" is the one directive
        // Cloudflare cannot override — always fetches fresh HTML from Vercel.
        // Hashed /_next/static/* assets remain immutably cached as normal.
        source: "/(.*)",
        headers: [
          {
            key: "Cache-Control",
            value: "no-store",
          },
        ],
      },
    ];
  },
};

export default nextConfig;
