import { MiniSparkline } from "@/components/charts/mini-sparkline"

interface StatCardProps {
  label: string
  value: string | number
  delta?: number   // optional % change vs last period; positive = up, negative = down
  trend?: number[] // optional sparkline data, oldest first
}

/**
 * Compact stat card — label, a large tabular-nums value, and an optional
 * delta showing ↑/↓ trend vs last period, or a sparkline of recent history.
 */
export function StatCard({ label, value, delta, trend }: StatCardProps) {
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
      {trend && trend.length > 1 && (
        <div className="mt-2 -mx-1">
          <MiniSparkline data={trend} height={28} />
        </div>
      )}
    </div>
  )
}
