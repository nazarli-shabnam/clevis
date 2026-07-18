/**
 * Two empty state variants — no icons, no centered layouts, just text.
 *
 * <EmptyStateInline>  — inside a card/table where data would be
 * <EmptyStatePage>    — when a whole feature section has no data yet
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

/**
 * Legacy compat shim — existing pages that pass icon/title/description
 * are redirected to EmptyStatePage so the old pattern doesn't crash.
 * Remove once all callers are migrated.
 */
interface LegacyEmptyStateProps {
  icon?: unknown
  title: string
  description: string
  cta?: React.ReactNode
}

export function EmptyState({ title, description }: LegacyEmptyStateProps) {
  return <EmptyStatePage message={`${title} — ${description}`} />
}
