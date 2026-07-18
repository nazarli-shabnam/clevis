import { CheckCircle, MinusCircle, XCircle } from "@phosphor-icons/react"
import type { CheckResult, CheckValue } from "@/lib/api/types"

interface CheckCardProps {
  check: CheckResult
}

const severityLabel: Record<string, string> = {
  high:   "text-red-400",
  medium: "text-yellow-400",
  low:    "text-blue-400",
}

function CheckValueDisplay({ value }: { value: CheckValue }) {
  if (!value) return null

  if (value.type === "boolean") {
    return (
      <div className="border-t border-border/40 mt-2 pt-2">
        {value.enabled ? (
          <span className="text-xs text-green-400 font-mono">✓ Enabled</span>
        ) : (
          <span className="text-xs text-red-400 font-mono">✗ Disabled</span>
        )}
      </div>
    )
  }

  if (value.type === "severity_counts") {
    const buckets: { key: keyof typeof value; label: string; className: string }[] = [
      { key: "critical", label: "critical", className: "text-red-400 border-red-500/30" },
      { key: "high", label: "high", className: "text-orange-400 border-orange-500/30" },
      { key: "medium", label: "medium", className: "text-yellow-400 border-yellow-500/30" },
      { key: "low", label: "low", className: "text-blue-400 border-blue-500/30" },
    ]
    const nonZero = buckets.filter((b) => (value[b.key] as number) > 0)
    if (nonZero.length === 0) {
      return (
        <div className="border-t border-border/40 mt-2 pt-2">
          <span className="text-xs text-green-400 font-mono">✓ No open alerts</span>
        </div>
      )
    }
    return (
      <div className="border-t border-border/40 mt-2 pt-2 flex flex-wrap gap-1.5">
        {nonZero.map((b) => (
          <span key={b.key} className={`stat-chip ${b.className}`}>
            {value[b.key] as number} {b.label}
          </span>
        ))}
      </div>
    )
  }

  if (value.type === "ratio") {
    const { numerator, denominator } = value
    const pct = denominator === 0 ? 0 : Math.round((numerator / denominator) * 100)
    const barColor =
      pct >= 80 ? "bg-green-400" : pct >= 50 ? "bg-yellow-400" : "bg-red-400"
    const textColor =
      pct >= 80 ? "text-green-400" : pct >= 50 ? "text-yellow-400" : "text-red-400"

    return (
      <div className="border-t border-border/40 mt-2 pt-2">
        <div className="flex items-center gap-2">
          <div className="flex-1 h-1.5 bg-muted rounded-full overflow-hidden">
            <div
              className={`h-full rounded-full ${barColor}`}
              style={{ width: `${pct}%` }}
            />
          </div>
          <span className={`text-[0.6875rem] font-mono tabular-nums shrink-0 ${textColor}`}>
            {numerator} / {denominator} · {pct}%
          </span>
        </div>
      </div>
    )
  }

  return null
}

export function CheckCard({ check }: CheckCardProps) {
  const pass = check.status === "pass"
  const notApplicable = check.status === "not_applicable"
  return (
    <div
      className={`bg-card border rounded-md p-3.5 flex items-start gap-3 transition-colors duration-200 ease-(--ease-out) ${
        notApplicable
          ? "border-border/40 hover:border-border/60"
          : pass
            ? "border-accent/20 hover:border-accent/35"
            : "border-destructive/25 hover:border-destructive/45"
      }`}
    >
      {notApplicable ? (
        <MinusCircle className="size-4 shrink-0 mt-0.5 text-muted-foreground" />
      ) : pass ? (
        <CheckCircle className="size-4 shrink-0 mt-0.5 text-accent" />
      ) : (
        <XCircle className="size-4 shrink-0 mt-0.5 text-destructive" />
      )}
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2 mb-0.5 flex-wrap">
          <span className="text-sm font-medium leading-snug">{check.title}</span>
          {notApplicable ? (
            <span className="stat-chip">Not applicable</span>
          ) : (
            <span className={`text-[0.6875rem] font-mono font-medium ${severityLabel[check.severity] ?? "text-muted-foreground"}`}>
              {check.severity}
            </span>
          )}
        </div>
        <p className="text-xs text-muted-foreground leading-relaxed">{check.remediation}</p>
        <CheckValueDisplay value={check.value} />
      </div>
    </div>
  )
}
