import { Warning, ArrowSquareOut } from "@phosphor-icons/react"
import type { InstallationMeta } from "@/lib/api/types"

// Per-installation page on GitHub where the owner can approve updated permissions.
function reviewUrl(install: InstallationMeta): string | null {
  const slug = process.env.NEXT_PUBLIC_GITHUB_APP_SLUG
  if (!slug || install.installation_id == null) return null
  return `https://github.com/apps/${slug}/installations/${install.installation_id}`
}

/**
 * Prompt to approve missing GitHub App permissions that write automations need; only an
 * org owner can approve, and it clears once `new_permissions_accepted` arrives. Shows a
 * muted "not yet checked" line when permissions were never synced.
 */
export function PermissionDriftNotice({
  install,
  className,
}: {
  install: InstallationMeta
  className?: string
}) {
  const wrap = ["text-xs", className].filter(Boolean).join(" ")

  if (install.permissions_synced_at === null) {
    return (
      <p className={`${wrap} text-muted-foreground flex items-center gap-1.5`}>
        <Warning className="size-3 shrink-0" />
        Permissions not yet checked for {install.account_login} — disconnect and reconnect to refresh.
      </p>
    )
  }

  const blocked = install.blocked_features ?? []
  if (blocked.length === 0) return null

  const url = reviewUrl(install)
  const labels = blocked.map((f) => f.label)

  return (
    <div className={`${wrap} border border-warning/30 bg-warning/5 px-3 py-2.5 flex flex-col gap-1.5`}>
      <p className="text-warning font-medium flex items-center gap-1.5">
        <Warning className="size-3.5 shrink-0" />
        {labels.length} automation{labels.length === 1 ? " needs" : "s need"} extra GitHub access
      </p>
      <ul className="text-muted-foreground list-disc pl-6">
        {labels.map((l) => (
          <li key={l}>{l}</li>
        ))}
      </ul>
      <p className="text-muted-foreground">
        Only a GitHub organization owner can approve. Clevis updates automatically once they do.
      </p>
      {url ? (
        <a
          href={url}
          target="_blank"
          rel="noreferrer"
          className="text-primary hover:underline inline-flex items-center gap-1 w-fit"
        >
          <ArrowSquareOut className="size-3.5" />
          Review on GitHub
        </a>
      ) : (
        <p className="text-muted-foreground">
          The GitHub App slug isn&rsquo;t configured on this instance — ask a workspace admin.
        </p>
      )}
    </div>
  )
}
