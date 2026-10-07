"use client"

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { api } from "@/lib/api/client"
import { useActiveScope } from "@/lib/active-scope"
import type { NotificationFeed } from "@/lib/api/types"

export const notificationsKey = (org: string) => ["notifications", org] as const

const REFRESH_MS = 60_000

/** The active org's notification feed and a mark-all-read action. Org scopes only: notifications are an
 * org feature, so a personal scope gets an idle result. The feed is shared by the bell and Overview. */
export function useNotifications() {
  const { scope } = useActiveScope()
  const org = scope?.kind === "org" ? scope.login : ""
  const queryClient = useQueryClient()

  const query = useQuery<NotificationFeed>({
    queryKey: notificationsKey(org),
    queryFn: () => api.notifications.feed(org),
    enabled: org !== "",
    retry: false,
    refetchInterval: REFRESH_MS,
  })

  const markRead = useMutation({
    // Items are newest first; mark up to the newest one this feed actually showed.
    mutationFn: () => api.notifications.markRead(org, query.data?.items[0]?.at),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: notificationsKey(org) }),
  })

  return { org, query, markRead, items: query.data?.items ?? [], unread: query.data?.unread_count ?? 0 }
}
