import type { NextConfig } from "next";
import fs from "fs";
import path from "path";

// Read the system version from the monorepo root version.py.
// Injected at build time so the UI always shows the deployed version.
function getSystemVersion(): string {
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
