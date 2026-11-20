import { CircleNotch } from "@phosphor-icons/react"
import { Button } from "@/components/ui/button"

// Inline error with manual retry; default query behavior won't retry just from switching tabs.
// A 4xx validation error (a rejected query parameter, not a failed request) has no point of
// retrying: the same filters will be rejected again, so the Retry button is hidden for those.
export function SectionError({
  message,
  onRetry,
  retrying,
  error,
}: {
  message: string
  onRetry: () => void
  retrying?: boolean
  error?: unknown
}) {
  const retryable = (error as { status?: number } | null)?.status !== 422
  const retryContent: React.ReactNode = retrying ? <CircleNotch className="size-3 animate-spin" /> : "Retry"

  return (
    <div className="px-4 py-6 flex items-center justify-between gap-3">
      <p role="alert" className="text-sm text-destructive">{message}</p>
      {retryable && (
        <Button size="sm" variant="outline" onClick={onRetry} disabled={retrying}>
          {retryContent}
        </Button>
      )}
    </div>
  )
}
