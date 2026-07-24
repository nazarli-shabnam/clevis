import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import PrsLayout, { metadata as prsMetadata } from "@/app/my/prs/layout";
import ReviewsLayout, { metadata as reviewsMetadata } from "@/app/my/reviews/layout";
import IssuesLayout, { metadata as issuesMetadata } from "@/app/my/issues/layout";
import ReleasesLayout, { metadata as releasesMetadata } from "@/app/releases/layout";

describe("My PRs/Reviews/Issues and Releases route layouts", () => {
  afterEach(() => {
    cleanup();
  });

  it.each([
    ["My PRs", PrsLayout, prsMetadata, "My PRs · clevis"],
    ["My Reviews", ReviewsLayout, reviewsMetadata, "My Reviews · clevis"],
    ["My Issues", IssuesLayout, issuesMetadata, "My Issues · clevis"],
    ["Releases", ReleasesLayout, releasesMetadata, "Releases · clevis"],
  ])("%s layout sets its page title and renders children", (_label, Layout, metadata, expectedTitle) => {
    expect(metadata.title).toBe(expectedTitle);

    render(
      <Layout>
        <p>child content</p>
      </Layout>,
    );

    expect(screen.getByText("child content")).toBeInTheDocument();
  });
});
