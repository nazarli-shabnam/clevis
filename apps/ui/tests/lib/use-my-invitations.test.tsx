import { renderHook, waitFor } from "@testing-library/react"
import { describe, expect, it, vi } from "vitest"

const mineMock = vi.fn()
vi.mock("@/lib/api/client", () => ({ api: { invitations: { mine: () => mineMock() } } }))

import { useMyInvitations } from "@/lib/use-my-invitations"

const inv = (org: string) => ({ org_login: org, token: `tok-${org}`, expires_at: "2030-01-01T00:00:00Z" })

describe("useMyInvitations", () => {
  it("never shows one account's invitations to the next account that signs in", async () => {
    mineMock.mockResolvedValueOnce([inv("acme")])
    const { result, rerender } = renderHook(({ ownerId }) => useMyInvitations(true, "/repos", ownerId), {
      initialProps: { ownerId: 1 },
    })
    await waitFor(() => expect(result.current).toEqual([inv("acme")]))

    // Account 2 replaces account 1 with no signed-out render in between; its read has not landed yet.
    let resolveSecond: (v: unknown) => void = () => {}
    mineMock.mockReturnValueOnce(new Promise((resolve) => { resolveSecond = resolve }))
    rerender({ ownerId: 2 })
    expect(result.current).toEqual([])

    resolveSecond([inv("other")])
    await waitFor(() => expect(result.current).toEqual([inv("other")]))
  })
})
