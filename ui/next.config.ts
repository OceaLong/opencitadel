import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${process.env.NEXT_PUBLIC_API_PROXY_TARGET || "http://localhost:8088"}/api/:path*`,
      },
    ];
  },
};

export default nextConfig;
