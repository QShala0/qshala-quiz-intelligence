import type { NextConfig } from "next";

const isVercel = Boolean(process.env.VERCEL || process.env.NEXT_PUBLIC_VERCEL_ENV);
const r2PublicBase = "https://pub-e6b1fda94a4149298de10be6794417fc.r2.dev";

const nextConfig: NextConfig = {
  async headers() {
    return [
      {
        source: "/(.*)",
        headers: [
          { key: "X-Frame-Options", value: "DENY" },
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
          { key: "Permissions-Policy", value: "camera=(), microphone=(self), geolocation=()" },
        ],
      },
      {
        source: "/voice/v1/:path*",
        headers: [
          { key: "Access-Control-Allow-Origin", value: "*" },
          { key: "Access-Control-Allow-Methods", value: "GET, HEAD, OPTIONS" },
          { key: "Access-Control-Allow-Headers", value: "Range, Content-Type, Authorization" },
          { key: "Access-Control-Expose-Headers", value: "Content-Length, Content-Range, ETag" },
          { key: "Access-Control-Max-Age", value: "86400" },
        ],
      },
    ];
  },
  async rewrites() {
    const cloudRunBackend = "https://qshala-quiz-intelligence-238470261844.asia-southeast1.run.app/api/:path*";
    const rewrites = [
      {
        source: "/api/:path*",
        destination: process.env.INTERNAL_API_URL || cloudRunBackend,
      },
      {
        source: "/voice/v1/:path*",
        destination: `${r2PublicBase}/voice/v1/:path*`,
      },
    ];

    // Only proxy /static in local dev when NOT on Vercel and an explicit internal API URL is provided.
    // In production and on Vercel, static assets are served directly from public/ via Vercel Edge CDN with 0ms cold start.
    if (!isVercel && process.env.NODE_ENV === "development" && process.env.INTERNAL_API_URL) {
      rewrites.push({
        source: "/static/:path*",
        destination: `${process.env.INTERNAL_API_URL.replace(/\/api.*$/, '')}/static/:path*`,
      });
    }

    return rewrites;
  },
};

export default nextConfig;
