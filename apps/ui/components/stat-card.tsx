interface StatCardProps {
  label: string
  value: string | number
  delta?: number   // optional % change vs last period; positive = up, negative = down
}

/**
 * Instrument-panel stat card. No icon container — just the number.
 * Value in IBM Plex Mono for that data-dense engineering feel.
 * Optional delta shows ↑/↓ trend vs last period.
 */
export function StatCard({ label, value, delta }: StatCardProps) {
  return (
    <div className="bg-card border border-border px-4 py-4">
      <p className="text-[0.6875rem] font-medium font-mono text-muted-foreground uppercase tracking-[0.1em] mb-3">
        {label}
      </p>
      <p className="text-3xl font-semibold font-mono tabular-nums text-foreground leading-none data-value">
        {value}
      </p>
      {delta !== undefined && (
        <p className={`text-xs mt-2 font-mono ${delta >= 0 ? "text-accent" : "text-destructive"}`}>
          {delta >= 0 ? "↑" : "↓"} {Math.abs(delta)}% vs last week
        </p>
      )}
    </div>
  )
}
