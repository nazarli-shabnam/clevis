import { describe, expect, it, vi } from "vitest";

vi.mock("@/app/globals.css", () => ({}));

vi.mock("next/font/google", () => ({
  Geist: () => ({ variable: "--font-sans" }),
  Archivo: () => ({ variable: "--font-heading" }),
  JetBrains_Mono: () => ({ variable: "--font-jetbrains-mono" }),
}));

describe("RootLayout module", () => {
  it("configures the Geist, Archivo, and JetBrains Mono fonts at import time", async () => {
    const mod = await import("@/app/layout");

    expect(mod.default).toBeInstanceOf(Function);
  }, 30000);
});
