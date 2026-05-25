interface PageHeaderProps {
  title: string
  description?: string
  actions?: React.ReactNode
}

/**
 * Inverted label pattern: small muted category label on top, large value below.
 * Reads "data dashboard" not "generic page". Actions slot for per-page CTAs.
 */
export function PageHeader({ title, description, actions }: PageHeaderProps) {
  return (
    <div className="flex items-baseline justify-between mb-6">
      <div>
        <p className="text-[0.6875rem] font-medium font-mono text-muted-foreground uppercase tracking-[0.12em] mb-0.5">
          {title}
        </p>
        {description && (
          <h1 className="text-2xl font-semibold text-foreground leading-tight">
            {description}
          </h1>
        )}
      </div>
      {actions && <div className="flex items-center gap-2">{actions}</div>}
    </div>
  )
}
