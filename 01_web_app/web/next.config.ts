import type { NextConfig } from "next";

// The browser calls /api/* on this app; Next proxies to the API backend (module 02).
// This keeps the backend URL out of the browser bundle and avoids CORS.
const API_URL = process.env.API_URL ?? "http://localhost:8002";

const nextConfig: NextConfig = {
  cacheComponents: true,
  partialPrefetching: true,
  output: "standalone",
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${API_URL}/:path*` }];
  },
};

export default nextConfig;
