"use client"

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { api } from "@/lib/api/client"
import { useActiveScope } from "@/lib/active-scope"
import type { NotificationFeed, NotificationKind, NotificationPreferences } from "@/lib/api/types"

export const notificationsKey = (org: string) => ["notifications", org] as const
export const notificationPrefsKey = (org: string) => ["notification-preferences", org] as const

const REFRESH_MS = 60_000
// What the /notifications page asks for: the API's widest window and item cap.
const FULL_FEED = { days: 90, limit: 200 }

/** The active org's notification feed, a mark-all-read action and a mark-one-read action. Org scopes
 * only: notifications are an org feature, so a personal scope gets an idle result. The bell and Overview
 * share the default feed; `full` asks for the long window the /notifications page lists. */
export function useNotifications({ full = false }: { full?: boolean } = {}) {
  const { scope } = useActiveScope()
  const org = scope?.kind === "org" ? scope.login : ""
  const queryClient = useQueryClient()

  const query = useQuery<NotificationFeed>({
    queryKey: full ? [...notificationsKey(org), "full"] : notificationsKey(org),
    queryFn: () => api.notifications.feed(org, full ? FULL_FEED : undefined),
    enabled: org !== "",
    retry: false,
    refetchInterval: REFRESH_MS,
  })

  const markRead = useMutation({
    // Items are newest first; mark up to the newest one this feed actually showed.
    mutationFn: () => api.notifications.markRead(org, query.data?.items[0]?.at),
    // Prefix match: refreshes the bell's feed and the full page's together.
    onSuccess: () => queryClient.invalidateQueries({ queryKey: notificationsKey(org) }),
  })

  const dismiss = useMutation({
    mutationFn: (itemId: string) => api.notifications.dismiss(org, itemId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: notificationsKey(org) }),
  })

  return { org, query, markRead, dismiss, items: query.data?.items ?? [], unread: query.data?.unread_count ?? 0 }
}

/** The caller's muted notification kinds for `org`. Loaded only while `enabled` (the settings panel is
 * open), and changing them refreshes the feed so the list and the unread badge drop or regain the kind. */
export function useNotificationPreferences(org: string, enabled: boolean) {
  const queryClient = useQueryClient()
  const query = useQuery<NotificationPreferences>({
    queryKey: notificationPrefsKey(org),
    queryFn: () => api.notifications.preferences(org),
    enabled: enabled && org !== "",
    retry: false,
  })
  const save = useMutation({
    mutationFn: (muted: NotificationKind[]) => api.notifications.setPreferences(org, muted),
    onSuccess: (data) => {
      queryClient.setQueryData(notificationPrefsKey(org), data)
      return queryClient.invalidateQueries({ queryKey: notificationsKey(org) })
    },
  })
  return { query, save, muted: query.data?.muted_kinds ?? [] }
}
