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

  it("renders a sparkline when trend data has more than one point", () => {
    const { container } = render(<StatCard label="Score" value={87} trend={[70, 80, 87]} />);

    expect(container.querySelector(".recharts-responsive-container")).toBeInTheDocument();
  });

  it("omits the sparkline when trend has 0 or 1 points", () => {
    const { container: zero } = render(<StatCard label="Score" value={87} trend={[]} />);
    expect(zero.querySelector(".recharts-responsive-container")).not.toBeInTheDocument();

    const { container: one } = render(<StatCard label="Score" value={87} trend={[87]} />);
    expect(one.querySelector(".recharts-responsive-container")).not.toBeInTheDocument();
  });

  it("shows how the score moved since the last scan, or why it cannot say", () => {
    const { rerender } = render(<StatCard label="Score" value={85} change={{ points: 5 }} />)
    expect(screen.getByText("+5 vs last scan")).toHaveClass("text-accent")

    rerender(<StatCard label="Score" value={85} change={{ points: -2 }} />)
    expect(screen.getByText("-2 vs last scan")).toHaveClass("text-destructive")

    rerender(<StatCard label="Score" value={85} change={{ points: 0 }} />)
    expect(screen.getByText("No change vs last scan")).toHaveClass("text-muted-foreground")

    rerender(<StatCard label="Score" value={85} change={{ note: "Not comparable with the previous scan" }} />)
    expect(screen.getByText("Not comparable with the previous scan")).toBeInTheDocument()

    rerender(<StatCard label="Score" value={85} change={null} />)
    expect(screen.queryByText(/vs last scan|Not comparable/)).not.toBeInTheDocument()
  })
})
