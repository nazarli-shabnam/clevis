/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "standalone",
  // Pin file tracing to the monorepo root; avoids workspace-root inference issues with multiple lockfiles.
  outputFileTracingRoot: new URL("..", import.meta.url).pathname,
  experimental: {
    optimizePackageImports: ["@phosphor-icons/react"],
  },
};

export default nextConfig;
