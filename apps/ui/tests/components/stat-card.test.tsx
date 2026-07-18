import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import { StatCard } from "@/components/stat-card";

describe("StatCard", () => {
  it("renders the label and value with no delta", () => {
    render(<StatCard label="Total repos" value={42} />);

    expect(screen.getByText("Total repos")).toBeInTheDocument();
    expect(screen.getByText("42")).toBeInTheDocument();
    expect(screen.queryByText(/vs last week/)).not.toBeInTheDocument();
  });

  it("renders an upward delta", () => {
    render(<StatCard label="Score" value={80} delta={5} />);

    expect(screen.getByText("↑ 5% vs last week")).toBeInTheDocument();
  });

  it("renders a downward delta", () => {
    render(<StatCard label="Score" value={60} delta={-3} />);

    expect(screen.getByText("↓ 3% vs last week")).toBeInTheDocument();
  });
});
