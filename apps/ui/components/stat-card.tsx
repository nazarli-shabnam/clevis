import { MiniSparkline } from "@/components/charts/mini-sparkline"
import { scoreChangeText, type ScoreChange } from "@/lib/score-change"

interface StatCardProps {
  label: string
  value: string | number
  delta?: number   // optional % change vs last period; positive = up, negative = down
  trend?: number[] // optional sparkline data, oldest first
  /** How the value moved since the previous scan (points), or why that cannot be said. */
  change?: ScoreChange | null
}

/** Compact stat card: label, large value, and an optional delta or sparkline. */
export function StatCard({ label, value, delta, trend, change }: StatCardProps) {
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
      {change && "points" in change && (
        <p className={`text-xs mt-2 ${change.points > 0 ? "text-accent" : change.points < 0 ? "text-destructive" : "text-muted-foreground"}`}>
          {scoreChangeText(change)}
        </p>
      )}
      {change && "note" in change && <p className="text-xs mt-2 text-muted-foreground">{change.note}</p>}
      {trend && trend.length > 1 && (
        <div className="mt-2 -mx-1">
          <MiniSparkline data={trend} height={28} />
        </div>
      )}
    </div>
  )
}
