import path from "node:path";
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

const uiRoot = path.resolve(__dirname);
const repoRoot = path.resolve(__dirname, "../..");

export default defineConfig({
  root: uiRoot,
  server: {
    fs: {
      allow: [repoRoot],
    },
  },
  plugins: [react()],
  test: {
    environment: "jsdom",
    setupFiles: ["./vitest.setup.ts"],
    include: ["./tests/**/*.{test,spec}.{ts,tsx}"],
    passWithNoTests: false,
    // 5000ms default is too tight under concurrent jsdom CPU contention (random file each run);
    // 20s gives headroom without masking a real hang.
    testTimeout: 20000,
    // Default one fork per core exhausts RAM on small dev machines (swap -> tinypool RPC timeouts
    // reported as "N errors" with zero failures). CI keeps full parallelism; override with VITEST_MAX_WORKERS.
    ...(process.env.CI ? {} : { maxWorkers: 3, minWorkers: 1 }),
    coverage: {
      provider: "v8",
      reporter: ["text", "lcov", "json-summary"],
      include: ["app/**", "components/**", "lib/**", "hooks/**"],
      // An explicit `exclude` replaces v8's default list, so co-located test files must be excluded
      // here too or diff-cover counts them as uncovered changed source.
      exclude: ["**/*.d.ts", "components/ui/**", "**/*.{test,spec}.{ts,tsx}"],
      // Regression guard set a few points below the measured baseline; most app/** pages have no
      // unit tests yet. Changed lines are held to a higher bar by CI's diff-coverage check.
      thresholds: {
        statements: 22,
        branches: 16,
        functions: 13,
        lines: 21,
      },
    },
  },
  resolve: {
    dedupe: ["react", "react-dom"],
    alias: {
      "@": uiRoot,
      react: path.join(uiRoot, "node_modules/react"),
      "react-dom": path.join(uiRoot, "node_modules/react-dom"),
      "@testing-library/react": path.join(
        uiRoot,
        "node_modules/@testing-library/react",
      ),
      "@testing-library/jest-dom": path.join(
        uiRoot,
        "node_modules/@testing-library/jest-dom",
      ),
      vitest: path.join(uiRoot, "node_modules/vitest"),
    },
  },
});
