import { CircleNotch } from "@phosphor-icons/react"
import { Button } from "@/components/ui/button"

// Shared inline error state with a manual retry action — used anywhere a TanStack Query
// section can fail and would otherwise show the same cached error forever (default query
// behavior doesn't retry just from switching tabs away and back).
export function SectionError({ message, onRetry, retrying }: { message: string; onRetry: () => void; retrying?: boolean }) {
  const retryContent: React.ReactNode = retrying ? <CircleNotch className="size-3 animate-spin" /> : "Retry"

  return (
    <div className="px-4 py-6 flex items-center justify-between gap-3">
      <p className="text-sm text-destructive">{message}</p>
      <Button size="sm" variant="outline" onClick={onRetry} disabled={retrying}>
        {retryContent}
      </Button>
    </div>
  )
}
