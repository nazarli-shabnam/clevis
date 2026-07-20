import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import { EmptyStateInline, EmptyStatePage } from "@/components/empty-state";

describe("EmptyStateInline", () => {
  it("renders the noun with no qualifier", () => {
    render(<EmptyStateInline noun="jobs" />);

    expect(screen.getByText("No jobs")).toBeInTheDocument();
  });

  it("renders the qualifier when provided", () => {
    render(<EmptyStateInline noun="repositories" qualifier="acme" />);

    expect(screen.getByText('No repositories matching "acme"')).toBeInTheDocument();
  });
});

describe("EmptyStatePage", () => {
  it("renders the message with no action", () => {
    render(<EmptyStatePage message="No organization configured yet." />);

    expect(screen.getByText("No organization configured yet.")).toBeInTheDocument();
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
  });

  it("renders an action link when provided", () => {
    render(
      <EmptyStatePage
        message="No organization configured yet."
        action={{ href: "/security", label: "Configure" }}
      />,
    );

    const link = screen.getByRole("link", { name: "Configure" });
    expect(link).toHaveAttribute("href", "/security");
  });
});
