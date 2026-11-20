import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { SectionError } from "@/components/section-error";

afterEach(cleanup);

describe("SectionError (#1122)", () => {
  it("announces the failure to assistive tech and keeps Retry a plain focusable button", () => {
    render(<SectionError message="Failed to load events." onRetry={vi.fn()} />);

    expect(screen.getByRole("alert")).toHaveTextContent("Failed to load events.");
    expect(screen.getByRole("button", { name: "Retry" })).toBeEnabled();
  });
});
