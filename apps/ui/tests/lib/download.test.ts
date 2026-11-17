import { afterEach, describe, expect, it, vi } from "vitest";

import { downloadTextFile } from "@/lib/download";

describe("downloadTextFile", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  it("creates an anchor, clicks it, and revokes the object URL", () => {
    vi.useFakeTimers();
    const createUrl = vi.fn(() => "blob:fake");
    const revokeUrl = vi.fn();
    // jsdom implements URL but not the object-URL methods.
    vi.stubGlobal("URL", { ...URL, createObjectURL: createUrl, revokeObjectURL: revokeUrl });
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});

    downloadTextFile("report.csv", "a,b\r\n1,2", "text/csv");

    expect(createUrl).toHaveBeenCalledOnce();
    expect(click).toHaveBeenCalledOnce();
    // Anchor is cleaned up synchronously.
    expect(document.querySelector("a[download]")).toBeNull();
    vi.runAllTimers();
    expect(revokeUrl).toHaveBeenCalledWith("blob:fake");
  });

  async function savedBlobText(mimeType: string) {
    let blob: Blob | undefined;
    vi.stubGlobal("URL", { ...URL, createObjectURL: (b: Blob) => ((blob = b), "blob:fake"), revokeObjectURL: vi.fn() });
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    downloadTextFile("f", "Zoë", mimeType);
    return new Uint8Array(await blob!.arrayBuffer());
  }

  it("prepends a UTF-8 BOM to CSV so Excel reads non-ASCII names", async () => {
    const bytes = await savedBlobText("text/csv");
    expect([...bytes.slice(0, 3)]).toEqual([0xef, 0xbb, 0xbf]);
  });

  it("leaves other text types without a BOM", async () => {
    const bytes = await savedBlobText("text/plain");
    expect(bytes[0]).toBe("Z".charCodeAt(0));
  });
});
