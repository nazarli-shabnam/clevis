/** @type {import('next').NextConfig} */
// Response headers every UI page carries even with no proxy in front: no framing (clickjacking on admin
// actions), no MIME sniffing, a trimmed Referer, and HSTS (ignored by browsers over plain HTTP, so the
// local trial is unaffected; no includeSubDomains, which would reach beyond this host).
const securityHeaders = [
  { key: "X-Frame-Options", value: "DENY" },
  { key: "Content-Security-Policy", value: "frame-ancestors 'none'" },
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  { key: "Strict-Transport-Security", value: "max-age=15552000" },
];

const nextConfig = {
  output: "standalone",
  // Pin file tracing to the monorepo root; avoids workspace-root inference issues with multiple lockfiles.
  outputFileTracingRoot: new URL("..", import.meta.url).pathname,
  async headers() {
    return [{ source: "/:path*", headers: securityHeaders }];
  },
  experimental: {
    optimizePackageImports: ["@phosphor-icons/react"],
  },
};

export default nextConfig;
