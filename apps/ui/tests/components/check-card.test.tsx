import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import { CheckCard } from "@/components/check-card";
import type { CheckResult } from "@/lib/api/types";

const baseCheck: CheckResult = {
  id: "mfa",
  title: "MFA enforced",
  severity: "high",
  remediation: "Require two-factor authentication for all members.",
  status: "pass",
  value: { type: "boolean", enabled: true },
};

describe("CheckCard", () => {
  afterEach(() => {
    cleanup();
  });

  it("renders a passing check with its boolean value", () => {
    render(<CheckCard check={baseCheck} />);

    expect(screen.getByText("MFA enforced")).toBeInTheDocument();
    expect(screen.getByText("✓ Enabled")).toBeInTheDocument();
  });

  it("renders a failing check with a ratio value", () => {
    render(
      <CheckCard
        check={{
          ...baseCheck,
          status: "fail",
          value: { type: "ratio", numerator: 2, denominator: 10 },
        }}
      />,
    );

    expect(screen.getByText(/2 \/ 10/)).toBeInTheDocument();
  });

  it("renders a not_applicable check with neutral styling, not failed styling", () => {
    const { container } = render(
      <CheckCard
        check={{
          ...baseCheck,
          status: "not_applicable",
          value: null,
        }}
      />,
    );

    expect(screen.getByText("Not applicable")).toBeInTheDocument();
    expect(screen.queryByText("high")).not.toBeInTheDocument();

    const card = container.firstElementChild;
    expect(card?.className).not.toContain("border-destructive");
    expect(card?.className).not.toContain("border-accent");
  });

  it("falls back to the neutral color for a severity not present in the label map", () => {
    render(
      <CheckCard
        check={{
          ...baseCheck,
          // severity is a "high" | "medium" | "low" union at the type level,
          // but the component defends against unmapped values at runtime
          // (e.g. a future backend severity level) via `?? "text-muted-foreground"`.
          // Cast through unknown to exercise that fallback branch.
          severity: "critical" as unknown as CheckResult["severity"],
        }}
      />,
    );

    const severitySpan = screen.getByText("critical");
    expect(severitySpan.className).toContain("text-muted-foreground");
    expect(severitySpan.className).not.toContain("text-red-400");
    expect(severitySpan.className).not.toContain("text-yellow-400");
    expect(severitySpan.className).not.toContain("text-blue-400");
  });
});
