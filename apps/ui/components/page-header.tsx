interface PageHeaderProps {
  title: string
  description?: string
  actions?: React.ReactNode
}

/**
 * Telemetry header: a directional marker + heavy macro-typography page label,
 * with a monospace supporting line beneath. Bottom border separates the header
 * zone from page content. Actions slot for per-page CTAs (e.g. "Run Scan").
 */
export function PageHeader({ title, description, actions }: PageHeaderProps) {
  return (
    <header className="flex items-end justify-between pb-4 mb-6 border-b border-border/60">
      <div className="flex flex-col gap-1.5">
        <div className="flex items-baseline gap-2.5">
          <span className="font-mono text-sm text-subtle-foreground leading-none select-none">
            {"///"}
          </span>
          <h1 className="macro-heading text-2xl md:text-[1.75rem] uppercase text-foreground">
            {title}
          </h1>
        </div>
        {description && (
          <p className="text-[0.8125rem] text-muted-foreground font-mono leading-snug">
            {description}
          </p>
        )}
      </div>
      {actions && <div className="flex items-center gap-2 shrink-0">{actions}</div>}
    </header>
  )
}
