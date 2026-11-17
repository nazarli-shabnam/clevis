import { afterEach, describe, expect, it, vi } from "vitest";

import { safeStorage } from "@/lib/safe-storage";

describe("safeStorage (#1121)", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    localStorage.clear();
    safeStorage.removeItem("k");
  });

  it("reads and writes through localStorage when it works", () => {
    safeStorage.setItem("k", "v");
    expect(localStorage.getItem("k")).toBe("v");
    expect(safeStorage.getItem("k")).toBe("v");
    safeStorage.removeItem("k");
    expect(localStorage.getItem("k")).toBeNull();
  });

  it("never throws and falls back to memory when the storage accessor is blocked", () => {
    const blocked = () => {
      throw new DOMException("blocked", "SecurityError");
    };
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(blocked);
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(blocked);
    vi.spyOn(Storage.prototype, "removeItem").mockImplementation(blocked);

    expect(safeStorage.getItem("k")).toBeNull();
    safeStorage.setItem("k", "session-only");
    expect(safeStorage.getItem("k")).toBe("session-only");
    safeStorage.removeItem("k");
    expect(safeStorage.getItem("k")).toBeNull();
  });
});
