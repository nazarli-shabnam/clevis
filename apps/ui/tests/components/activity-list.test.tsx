import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import { ActivityList } from "@/components/activity-list";
import type { JobOut } from "@/lib/api/types";

const jobs: JobOut[] = [
  { id: 1, job_type: "clear_actions_cache", status: "done", result: null, created_at: new Date().toISOString(), updated_at: new Date().toISOString() },
  { id: 2, job_type: "clear_actions_cache", status: "failed", result: "boom", created_at: new Date().toISOString(), updated_at: new Date().toISOString() },
];

describe("ActivityList", () => {
  it("renders each job's id and status", () => {
    render(<ActivityList jobs={jobs} isLoading={false} />);

    expect(screen.getByText("#1")).toBeInTheDocument();
    expect(screen.getByText("#2")).toBeInTheDocument();
    expect(screen.getByText("done")).toBeInTheDocument();
    expect(screen.getByText("failed")).toBeInTheDocument();
  });

  it("shows a 'View all' link when limited and there are more jobs than the limit", () => {
    render(<ActivityList jobs={jobs} isLoading={false} limit={1} />);

    expect(screen.getByRole("link", { name: /View all 2 jobs/ })).toBeInTheDocument();
  });
});
