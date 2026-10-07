import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import RegisterLayout, { metadata as registerMetadata } from "@/app/register/layout";
import VerifyEmailLayout, { metadata as verifyMetadata } from "@/app/verify-email/layout";
import InviteLayout, { metadata as inviteMetadata } from "@/app/invite/[token]/layout";

// These public routes render outside the app shell, so without their own title they fell back to
// the root "Overview · clevis".
describe.each([
  ["Register", RegisterLayout, registerMetadata, "Create account · clevis"],
  ["Verify email", VerifyEmailLayout, verifyMetadata, "Verify email · clevis"],
  ["Invite", InviteLayout, inviteMetadata, "Invitation · clevis"],
])("%s layout", (_name, Layout, metadata, title) => {
  afterEach(cleanup);

  it("renders its children through unmodified", () => {
    render(
      <Layout>
        <p>child content</p>
      </Layout>,
    );
    expect(screen.getByText("child content")).toBeInTheDocument();
  });

  it("sets the page title", () => {
    expect(metadata.title).toBe(title);
  });
});
