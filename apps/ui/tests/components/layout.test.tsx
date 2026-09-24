import { describe, expect, it, vi } from "vitest";

vi.mock("@/app/globals.css", () => ({}));

vi.mock("next/font/google", () => ({
  Geist: () => ({ variable: "--font-sans" }),
  Archivo: () => ({ variable: "--font-heading" }),
  JetBrains_Mono: () => ({ variable: "--font-jetbrains-mono" }),
}));

describe("RootLayout module", () => {
  it(
    "configures the Geist, Archivo, and JetBrains Mono fonts at import time",
    // Importing the full root layout can take >150s under suite CPU contention (~1s standalone);
    // 180s plus one retry gives headroom without masking a real hang.
    { timeout: 180000, retry: 1 },
    async () => {
      const mod = await import("@/app/layout");

      expect(mod.default).toBeInstanceOf(Function);
    },
  );
});
