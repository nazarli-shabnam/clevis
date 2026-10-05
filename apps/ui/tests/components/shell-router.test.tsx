import { cleanup, render, screen } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

let mockPathname = "/"

vi.mock("next/navigation", () => ({
  usePathname: () => mockPathname,
}))

// The real sidebar fires authenticated requests on mount; stub it (and the breadcrumb) so the test
// only observes whether ShellRouter decided to render the shell.
vi.mock("@/components/app-sidebar", () => ({ AppSidebar: () => <nav data-testid="sidebar" /> }))
vi.mock("@/components/breadcrumb", () => ({ Breadcrumb: () => <span data-testid="breadcrumb" /> }))

import { ShellRouter } from "@/components/shell-router"
import { isPublicRoute } from "@/lib/public-routes"

function renderAt(pathname: string) {
  mockPathname = pathname
  return render(
    <ShellRouter>
      <p>page content</p>
    </ShellRouter>,
  )
}

beforeEach(() => {
  // SidebarProvider reads the viewport via matchMedia, which jsdom doesn't implement.
  vi.stubGlobal(
    "matchMedia",
    vi.fn().mockImplementation((query: string) => ({
      matches: false,
      media: query,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    })),
  )
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe("ShellRouter", () => {
  it.each(["/login", "/setup", "/register", "/verify-email", "/invite/abc123"])(
    "renders %s without the app shell, so no authenticated sidebar requests fire",
    (path) => {
      expect(isPublicRoute(path)).toBe(true)
      renderAt(path)

      expect(screen.getByText("page content")).toBeInTheDocument()
      expect(screen.queryByTestId("sidebar")).not.toBeInTheDocument()
      // The shell's <main> is skipped too: public pages must bring their own landmark.
      expect(screen.queryByRole("main")).not.toBeInTheDocument()
    },
  )

  it.each(["/", "/repos", "/settings", "/invitations", "/invite"])(
    "wraps %s in the shell with a single main landmark",
    (path) => {
      expect(isPublicRoute(path)).toBe(false)
      renderAt(path)

      expect(screen.getByTestId("sidebar")).toBeInTheDocument()
      expect(screen.getAllByRole("main")).toHaveLength(1)
      expect(screen.getByRole("main")).toHaveTextContent("page content")
    },
  )
})
