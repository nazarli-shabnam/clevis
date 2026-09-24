"use client"

import { Button } from "@/components/ui/button"

// Catches render-time errors below the root layout so a crash shows a recoverable fallback.
// error.message is deliberately not shown (generic in prod, raw internals on client); error.digest
// is the reference for server logs.
export default function Error({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  return (
    <div className="min-h-[60vh] flex items-center justify-center px-4">
      <div className="card max-w-md w-full px-6 py-8 flex flex-col items-center gap-3 text-center">
        <p className="text-sm font-medium text-foreground">Something went wrong</p>
        <p className="text-sm text-muted-foreground">
          An unexpected error occurred while rendering this page. Try again, or reload if it persists.
        </p>
        {error.digest && (
          <p className="text-xs text-muted-foreground/70 font-mono">Reference: {error.digest}</p>
        )}
        <Button size="sm" variant="outline" onClick={reset} className="mt-2">
          Try again
        </Button>
      </div>
    </div>
  )
}
