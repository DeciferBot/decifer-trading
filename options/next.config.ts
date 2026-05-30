import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  env: {
    NEXT_PUBLIC_INTELLIGENCE_API_URL: process.env.INTELLIGENCE_API_URL ?? "",
  },
};

export default nextConfig;
