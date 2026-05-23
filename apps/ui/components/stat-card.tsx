import type { LucideIcon } from "lucide-react"

interface StatCardProps {
  label: string
  value: string | number
  icon: LucideIcon
}

export function StatCard({ label, value, icon: Icon }: StatCardProps) {
  return (
    <div className="bg-card border border-border rounded-lg px-4 py-4 flex items-start justify-between gap-3">
      <div>
        <p className="text-xs text-muted-foreground mb-1.5">{label}</p>
        <p className="text-2xl font-bold tabular-nums tracking-tight">{value}</p>
      </div>
      <div className="mt-0.5 p-2 bg-muted/60 rounded-md shrink-0">
        <Icon className="size-4 text-muted-foreground" />
      </div>
    </div>
  )
}
