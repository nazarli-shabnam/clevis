import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CopyButton } from "@/components/copy-button";

describe("CopyButton", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => {
    cleanup();
    vi.useRealTimers();
  });

  it("copies the value, confirms, then goes back to 'Copy'", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });
    render(<CopyButton value="https://x/invite/abc" ariaLabel="Copy link" />);

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Copy link" }));
    });

    expect(writeText).toHaveBeenCalledWith("https://x/invite/abc");
    expect(screen.getByText("Copied")).toBeInTheDocument();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2100);
    });
    expect(screen.getByText("Copy")).toBeInTheDocument();
  });

  it("says so when the browser refuses", async () => {
    Object.assign(navigator, { clipboard: { writeText: vi.fn().mockRejectedValue(new Error("denied")) } });
    render(<CopyButton value="v" ariaLabel="Copy link" />);

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Copy link" }));
    });

    expect(screen.getByText("Copy failed")).toBeInTheDocument();
  });
});
