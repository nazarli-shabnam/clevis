import { CircleNotch } from "@phosphor-icons/react"
import { Button } from "@/components/ui/button"

// Inline error with manual retry; default query behavior won't retry just from switching tabs.
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
