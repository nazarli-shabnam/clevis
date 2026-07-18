interface StatCardProps {
  label: string
  value: string | number
  delta?: number   // optional % change vs last period; positive = up, negative = down
}

/**
 * Compact stat card — label, a large tabular-nums value, and an optional
 * delta showing ↑/↓ trend vs last period.
 */
export function StatCard({ label, value, delta }: StatCardProps) {
  return (
    <div className="bg-card border border-border rounded-md px-4 py-4">
      <p className="telemetry-label mb-2 block">
        {label}
      </p>
      <p className="text-3xl font-semibold tabular-nums text-foreground leading-none data-value">
        {value}
      </p>
      {delta !== undefined && (
        <p className={`text-xs mt-2 ${delta >= 0 ? "text-accent" : "text-destructive"}`}>
          {delta >= 0 ? "↑" : "↓"} {Math.abs(delta)}% vs last week
        </p>
      )}
    </div>
  )
}
