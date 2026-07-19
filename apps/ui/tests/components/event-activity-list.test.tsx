import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import { EventActivityList } from "@/components/event-activity-list";
import type { OrgEvent } from "@/lib/api/types";

const events: OrgEvent[] = [
  { id: "1", type: "PushEvent", actor: "alice", actor_avatar: "", repo: "acme/api", summary: "pushed 3 commits to main", created_at: new Date().toISOString() },
  { id: "2", type: "PullRequestEvent", actor: "bob", actor_avatar: "", repo: "acme/worker", summary: "opened PR #4", created_at: new Date().toISOString() },
];

describe("EventActivityList", () => {
  afterEach(() => {
    cleanup();
  });

  it("shows a loading skeleton", () => {
    const { container } = render(<EventActivityList events={[]} isLoading />);
    expect(container.querySelectorAll(".animate-pulse").length).toBeGreaterThan(0);
  });

  it("shows an empty state with no events", () => {
    render(<EventActivityList events={[]} isLoading={false} />);
    expect(screen.getByText("— no recent activity")).toBeInTheDocument();
  });

  it("renders each event's actor, summary, and repo", () => {
    render(<EventActivityList events={events} isLoading={false} />);

    expect(screen.getByText("alice")).toBeInTheDocument();
    expect(screen.getByText("pushed 3 commits to main")).toBeInTheDocument();
    expect(screen.getByText("acme/api")).toBeInTheDocument();
    expect(screen.getByText("bob")).toBeInTheDocument();
    expect(screen.getByText("opened PR #4")).toBeInTheDocument();
  });

  it("truncates to the limit", () => {
    render(<EventActivityList events={events} isLoading={false} limit={1} />);

    expect(screen.getByText("alice")).toBeInTheDocument();
    expect(screen.queryByText("bob")).not.toBeInTheDocument();
  });
});
