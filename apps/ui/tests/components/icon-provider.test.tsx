import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import { IconProvider } from "@/components/icon-provider";

describe("IconProvider", () => {
  it("renders its children", () => {
    render(
      <IconProvider>
        <span>child content</span>
      </IconProvider>,
    );

    expect(screen.getByText("child content")).toBeInTheDocument();
  });
});
