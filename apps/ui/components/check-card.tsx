import { CheckCircle2, XCircle } from "lucide-react"
import type { CheckResult } from "@/lib/api/types"

interface CheckCardProps {
  check: CheckResult
}

const severityLabel: Record<string, string> = {
  high:   "text-red-400",
  medium: "text-yellow-400",
  low:    "text-blue-400",
}

export function CheckCard({ check }: CheckCardProps) {
  const pass = check.status === "pass"
  return (
    <div
      className={`bg-card border p-3.5 flex items-start gap-3 transition-colors ${
        pass ? "border-green-500/20 hover:border-green-500/35" : "border-red-500/20 hover:border-red-500/35"
      }`}
    >
      {pass ? (
        <CheckCircle2 className="size-4 shrink-0 mt-0.5 text-green-400" />
      ) : (
        <XCircle className="size-4 shrink-0 mt-0.5 text-red-400" />
      )}
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2 mb-0.5 flex-wrap">
          <span className="text-sm font-medium leading-snug">{check.title}</span>
          <span className={`text-[0.6875rem] font-mono font-medium ${severityLabel[check.severity] ?? "text-muted-foreground"}`}>
            {check.severity}
          </span>
        </div>
        <p className="text-xs text-muted-foreground leading-relaxed">{check.remediation}</p>
      </div>
    </div>
  )
}
