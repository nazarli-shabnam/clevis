import { afterEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

import { EventFeed } from "@/components/event-feed";
import type { OrgEvent } from "@/lib/api/types";

const events: OrgEvent[] = [
  {
    id: "1",
    type: "PushEvent",
    actor: "alice",
    actor_avatar: "https://avatars/alice.png",
    repo: "acme/api",
    summary: "pushed 3 commits to main",
    created_at: new Date().toISOString(),
  },
  {
    id: "2",
    type: "PullRequestEvent",
    actor: "bob",
    actor_avatar: "https://avatars/bob.png",
    repo: "acme/worker",
    summary: "opened PR #42: Fix cache timeout",
    created_at: new Date().toISOString(),
  },
];

describe("EventFeed", () => {
  afterEach(() => {
    cleanup();
  });

  it("renders each event's actor and summary", () => {
    render(<EventFeed events={events} isLoading={false} />);

    expect(screen.getByText(/pushed 3 commits to main/)).toBeInTheDocument();
    expect(screen.getByText(/opened PR #42: Fix cache timeout/)).toBeInTheDocument();
  });

  it("shows an empty state when there are no events", () => {
    render(<EventFeed events={[]} isLoading={false} />);

    expect(screen.getByText(/no events yet/)).toBeInTheDocument();
  });

  it("filters to only pushes when the Pushes chip is clicked", () => {
    render(<EventFeed events={events} isLoading={false} />);

    fireEvent.click(screen.getByRole("button", { name: "Pushes" }));

    expect(screen.getByText(/pushed 3 commits to main/)).toBeInTheDocument();
    expect(screen.queryByText(/opened PR #42/)).not.toBeInTheDocument();
  });
});
