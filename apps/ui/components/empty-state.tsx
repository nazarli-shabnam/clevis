import Link from "next/link"
import { useQuery } from "@tanstack/react-query"
import { buttonVariants } from "@/components/ui/button"
import { api } from "@/lib/api/client"
import { githubWebUrl } from "@/lib/github-web"
import { useGithubAppSlug } from "@/lib/use-github-app-slug"
import { cn } from "@/lib/utils"

/**
 * Empty state variants: Inline (inside a card/table), Page (whole section has no data),
 * NoAccount (no org/personal account selected).
 */

interface EmptyStateInlineProps {
  noun: string         // e.g. "jobs", "caches", "members"
  qualifier?: string   // e.g. filter value currently applied
}

export function EmptyStateInline({ noun, qualifier }: EmptyStateInlineProps) {
  return (
    <div className="px-4 py-8 border-t border-border/60">
      <p className="text-sm text-muted-foreground">
        No {noun}{qualifier ? ` matching "${qualifier}"` : ""}
      </p>
    </div>
  )
}

interface EmptyStatePageProps {
  message: string
  action?: { href: string; label: string }
}

export function EmptyStatePage({ message, action }: EmptyStatePageProps) {
  return (
    <div className="border border-dashed border-border rounded-md px-6 py-12">
      <p className="text-sm text-muted-foreground">
        {message}
        {action && (
          <>
            {" — "}
            <a
              href={action.href}
              className="text-primary underline-offset-2 hover:underline"
            >
              {action.label}
            </a>
          </>
        )}
      </p>
    </div>
  )
}

interface EmptyStateNoAccountProps {
  /** Skip the outer card wrapper -- for use inside a panel that's already a card. */
  bare?: boolean
  /** Override the default copy, e.g. for Repos where manual search works without a scope. */
  message?: string
  /** The page only works for an organization and the user is on their personal account: point to installing the App on one. */
  orgOnly?: boolean
}

function InstallOnOrgLink() {
  const { slug } = useGithubAppSlug()
  const href = slug ? githubWebUrl(`apps/${slug}/installations/new`) : "/settings"
  return (
    <a href={href} className={cn(buttonVariants({ size: "sm" }))}>
      Install the GitHub App on an organization
    </a>
  )
}

/** The connect steps for an account with no linked GitHub identity: a password account must link one before it
 * can connect an organization, which the plain "Connect a GitHub account" button does not say. */
function LinkThenInstall() {
  return (
    <ol className="flex flex-col gap-3 text-sm list-decimal pl-5">
      <li>
        <p className="font-medium text-foreground">Link your GitHub account first</p>
        <p className="text-muted-foreground mb-2">
          This account signs in with a password, so it needs a linked GitHub account before it can connect an
          organization (Settings, Profile).
        </p>
        <Link href="/settings" className={cn(buttonVariants({ size: "sm" }))}>
          Link your GitHub account
        </Link>
      </li>
      <li>
        <p className="font-medium text-foreground">Install the GitHub App</p>
        <p className="text-muted-foreground mb-2">Then install the Clevis GitHub App on the organization you want to see.</p>
        <InstallOnOrgLink />
      </li>
    </ol>
  )
}

export function EmptyStateNoAccount({ bare = false, message: messageOverride, orgOnly = false }: EmptyStateNoAccountProps) {
  // Same query as the Profile section's GitHub field. Only a loaded answer of "no linked account" changes the
  // steps: while it loads, or if it fails, the generic button is still right.
  const me = useQuery({ queryKey: ["me"], queryFn: () => api.auth.me(), retry: false, staleTime: 60_000 })
  const needsLink = !orgOnly && me.data !== undefined && !me.data.github_login
  const content = (
    <div className="px-4 py-6 flex flex-col gap-3 items-start">
      <p className="text-sm text-muted-foreground">
        {messageOverride ?? "No account selected yet — this page has nothing to query. Pick an organization or your personal account from the profile menu, or connect one below if you haven’t already."}
      </p>
      {orgOnly ? (
        <InstallOnOrgLink />
      ) : needsLink ? (
        <LinkThenInstall />
      ) : (
        <Link href="/settings" className={cn(buttonVariants({ size: "sm" }))}>
          Connect a GitHub account
        </Link>
      )}
    </div>
  )
  return bare ? content : <div className="card mb-6">{content}</div>
}
