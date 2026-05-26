import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
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
        // HTML shells for app routes must never be cached by Cloudflare.
        // CF's free plan enforces a 7200s edge_cache_ttl floor that overrides
        // Vercel's own max-age=0, causing stale pages after every deploy.
        // "no-store" is the one directive Cloudflare cannot override — it
        // will always fetch fresh HTML from Vercel on each browser request.
        // Hashed static assets (/_next/static/*) are unaffected and remain
        // immutably cached at the edge as normal.
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
