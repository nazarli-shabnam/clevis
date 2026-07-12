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
    coverage: {
      provider: "v8",
      reporter: ["text", "lcov", "json-summary"],
      include: ["app/**", "components/**", "lib/**", "hooks/**"],
      exclude: ["**/*.d.ts", "components/ui/**"],
      // Global floor is a regression guard, not an aspirational target — most `app/**` page
      // components have no unit tests yet (large, integration-style route components; this
      // repo's convention so far is unit-testing extracted logic/hooks/components, not full
      // pages). Measured baseline: ~24.6%/18%/15.7%/23.7%. Set a few points below so normal
      // fluctuation doesn't fail CI, while still catching a real drop. New/changed lines in a
      // PR are separately held to a much higher bar by the diff-coverage check in CI.
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
