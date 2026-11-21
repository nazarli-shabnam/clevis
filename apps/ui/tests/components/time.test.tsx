import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { Time } from "@/components/time";
import { exactTime } from "@/lib/format";

describe("Time", () => {
  afterEach(cleanup);

  it("shows the relative time with the exact timestamp on hover", () => {
    const iso = new Date(Date.now() - 3 * 86_400_000).toISOString();
    render(<Time iso={iso} />);

    const el = screen.getByText("3 days ago");
    expect(el.tagName).toBe("TIME");
    expect(el).toHaveAttribute("title", exactTime(iso));
    expect(el).toHaveAttribute("datetime", iso);
  });

  it("reads forward in time with `due`", () => {
    render(<Time iso={new Date(Date.now() + 3 * 86_400_000 + 60_000).toISOString()} due />);
    expect(screen.getByText("in 3 days")).toBeInTheDocument();
  });
});
