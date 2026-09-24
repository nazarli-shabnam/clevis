"use client"

import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { useState } from "react"

export function QueryProvider({ children }: { children: React.ReactNode }) {
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            // One retry, capped backoff, so failures settle into an error quickly.
            retry: 1,
            retryDelay: (attempt) => Math.min(1000 * 2 ** attempt, 5000),
            staleTime: 30_000,
            refetchOnWindowFocus: false,
          },
        },
      }),
  )
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>
}
