"use client"

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { SectionError } from "@/components/section-error"
import { api } from "@/lib/api/client"
import { relativeTime } from "@/lib/format"
import { describeDevice } from "@/lib/user-agent"

const SESSIONS_KEY = ["auth.sessions"] as const

/** The caller's signed-in sessions, each with its own Revoke, so a lost laptop can be signed out
 * without signing out the device in hand. Sessions started before this list existed are not shown. */
export function SessionsCard() {
  const queryClient = useQueryClient()
  const query = useQuery({ queryKey: SESSIONS_KEY, queryFn: () => api.auth.sessions(), retry: false })
  const revoke = useMutation({
    mutationFn: (id: string) => api.auth.revokeSession(id),
    onSettled: () => queryClient.invalidateQueries({ queryKey: SESSIONS_KEY }),
  })
  const sessions = query.data ?? []

  return (
    <div className="card">
      <div className="px-4 py-3 border-b border-border">
        <span className="section-label">Active sessions</span>
      </div>
      <p className="px-4 pt-3 text-xs text-muted-foreground max-w-xl">
        Where you are signed in. Revoke a session to sign that device out right away; this device stays signed in.
        Sessions started before this list existed aren&apos;t shown; &ldquo;Sign out of all devices&rdquo; above ends those too.
      </p>
      {query.isError ? (
        <SectionError message={query.error.message} onRetry={() => query.refetch()} retrying={query.isFetching} />
      ) : query.isLoading ? (
        <div className="p-4 flex flex-col gap-2">
          <Skeleton className="h-10 w-full" />
          <Skeleton className="h-10 w-full" />
        </div>
      ) : sessions.length === 0 ? (
        <p className="px-4 py-4 text-sm text-muted-foreground">No other sessions are recorded.</p>
      ) : (
        <ul className="divide-y divide-border">
          {sessions.map((s) => (
            <li key={s.id} className="flex items-center gap-3 px-4 py-3">
              <div className="min-w-0 flex-1 text-xs">
                <p className="flex items-center gap-2 font-medium text-foreground">
                  {describeDevice(s.user_agent)}
                  {s.current && (
                    <span className="rounded bg-primary/10 px-1.5 py-0.5 text-[0.6875rem] font-normal text-primary">
                      This device
                    </span>
                  )}
                </p>
                <p className="text-muted-foreground">
                  {s.ip ?? "Unknown IP"} · signed in {relativeTime(s.created_at)} · last active {relativeTime(s.last_seen_at)}
                </p>
              </div>
              {!s.current && (
                <Button
                  size="sm"
                  variant="outline"
                  aria-label={`Revoke the ${describeDevice(s.user_agent)} session from ${s.ip ?? "an unknown IP"}`}
                  disabled={revoke.isPending}
                  onClick={() => revoke.mutate(s.id)}
                >
                  Revoke
                </Button>
              )}
            </li>
          ))}
        </ul>
      )}
      {revoke.isError && (
        <p role="alert" className="px-4 pb-3 text-xs text-destructive">
          That session was not revoked. {revoke.error.message}
        </p>
      )}
    </div>
  )
}
