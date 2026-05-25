interface PageHeaderProps {
  title: string
  description?: string
  actions?: React.ReactNode
}

/**
 * Inverted label pattern: small muted category label on top, larger value below.
 * Bottom border separates the header zone from page content.
 * Actions slot for per-page CTAs (e.g. "Run Scan" button).
 */
export function PageHeader({ title, description, actions }: PageHeaderProps) {
  return (
    <div className="flex items-end justify-between pb-4 mb-6 border-b border-border/60">
      <div className="flex flex-col gap-1">
        <p className="text-[0.6875rem] font-medium font-mono text-muted-foreground uppercase tracking-[0.12em]">
          {title}
        </p>
        {description && (
          <h1 className="text-xl font-semibold text-foreground leading-tight">
            {description}
          </h1>
        )}
      </div>
      {actions && <div className="flex items-center gap-2">{actions}</div>}
    </div>
  )
}
