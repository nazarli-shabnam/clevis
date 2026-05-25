import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import { PageHeader } from "@/components/page-header";

describe("PageHeader", () => {
  it("renders title and description", () => {
    render(
      <PageHeader title="Repositories" description="Manage GitHub repos." />,
    );

    // description is now the h1; title is a small label above it
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(
      "Manage GitHub repos.",
    );
    expect(screen.getByText("Repositories")).toBeInTheDocument();
  });
});
