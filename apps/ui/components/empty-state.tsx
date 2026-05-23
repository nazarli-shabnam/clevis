import type { LucideIcon } from "lucide-react"
import type { ReactNode } from "react"

interface EmptyStateProps {
  icon: LucideIcon
  title: string
  description: string
  cta?: ReactNode
}

export function EmptyState({ icon: Icon, title, description, cta }: EmptyStateProps) {
  return (
    <div className="flex flex-col items-center justify-center py-16 px-4 text-center">
      <div className="p-3 bg-muted/60 rounded-lg mb-4">
        <Icon className="size-6 text-muted-foreground" />
      </div>
      <h3 className="text-sm font-medium text-foreground mb-1">{title}</h3>
      <p className="text-sm text-muted-foreground max-w-xs leading-relaxed">{description}</p>
      {cta && <div className="mt-4">{cta}</div>}
    </div>
  )
}
