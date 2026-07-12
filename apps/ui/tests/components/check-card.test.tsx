import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

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
});
