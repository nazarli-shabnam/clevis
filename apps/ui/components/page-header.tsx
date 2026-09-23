interface PageHeaderProps {
  title: string
  description?: string
  actions?: React.ReactNode
}

/** Page header: title, optional description, and an actions slot for per-page CTAs. */
export function PageHeader({ title, description, actions }: PageHeaderProps) {
  return (
    <header className="flex items-end justify-between pb-4 mb-6 border-b border-border/60">
      <div className="flex flex-col gap-1.5">
        <h1 className="macro-heading text-2xl md:text-[1.75rem] text-foreground">
          {title}
        </h1>
        {description && (
          <p className="text-[0.8125rem] text-muted-foreground leading-snug">
            {description}
          </p>
        )}
      </div>
      {actions && <div className="flex items-center gap-2 shrink-0">{actions}</div>}
    </header>
  )
}
