"use client"

import { useMutation, useQuery } from "@tanstack/react-query"
import { CircleNotch, GithubLogo } from "@phosphor-icons/react"
import { Button } from "@/components/ui/button"
import { Field, FieldDescription, FieldLabel } from "@/components/ui/field"
import { api } from "@/lib/api/client"

/** What each `?github_link_error=` code from the API's link callback means for the person who just came back. */
export const GITHUB_LINK_ERRORS: Record<string, string> = {
  session: "Linking couldn't be matched to your sign-in (you may have signed out or switched accounts since you started). Try again.",
  taken: "That GitHub account is already linked to another Clevis account.",
  other_account: "This account is already linked to a different GitHub account.",
  oauth: "GitHub didn't complete the request. Try again.",
  not_configured: "GitHub sign-in isn't set up on this server yet.",
}

export function githubLinkErrorMessage(code: string): string {
  return GITHUB_LINK_ERRORS[code] ?? "Linking your GitHub account failed. Try again."
}

/**
 * Profile row that shows the GitHub account linked to this Clevis account, or starts linking one. A password
 * account needs a linked GitHub account to connect an organization or a personal installation. The API starts
 * the flow with the caller's own credentials and answers with GitHub's authorize URL, which this opens.
 */
export function GithubLinkField() {
  const me = useQuery({ queryKey: ["me"], queryFn: () => api.auth.me(), retry: false })
  const link = useMutation({
    mutationFn: () => api.auth.linkGithub(),
    onSuccess: ({ url }) => {
      window.location.href = url
    },
  })

  // Not known yet (or the lookup failed): show nothing rather than offering a link that may be wrong.
  if (!me.data) return null
  const login = me.data.github_login

  return (
    <Field>
      <FieldLabel>GitHub account</FieldLabel>
      {login ? (
        <p className="text-xs text-foreground/80">
          Linked as <span className="font-mono">@{login}</span>
        </p>
      ) : (
        <>
          <Button
            type="button"
            variant="outline"
            className="w-fit"
            disabled={link.isPending || link.isSuccess}
            onClick={() => link.mutate()}
          >
            {link.isPending || link.isSuccess ? (
              <><CircleNotch className="size-3.5 animate-spin" />Opening GitHub…</>
            ) : (
              <><GithubLogo className="size-3.5" />Link GitHub account</>
            )}
          </Button>
          <FieldDescription>
            Needed to connect an organization or a personal GitHub App installation. Your email and password stay
            as they are.
          </FieldDescription>
          {link.isError && (
            <p role="alert" className="text-xs text-destructive">
              {link.error.message}
            </p>
          )}
        </>
      )}
    </Field>
  )
}
