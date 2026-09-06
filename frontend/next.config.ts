import type { NextConfig } from "next";

// Two deployment shapes share this app. The default `next build` keeps
// `next start` working for anyone running the full stack themselves;
// `npm run build:static` emits plain files for the hosted demo, which has no
// backend behind it. The two outputs are mutually exclusive, so the static
// build opts in rather than the mode being switched globally.
const nextConfig: NextConfig = {
  reactStrictMode: true,
  ...(process.env.NEXT_OUTPUT === "export" ? { output: "export" as const } : {}),
};

export default nextConfig;
