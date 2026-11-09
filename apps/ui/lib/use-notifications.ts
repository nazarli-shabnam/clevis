"use client"

import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { api } from "@/lib/api/client"
import { useActiveScope } from "@/lib/active-scope"
import type { NotificationFeed, NotificationKind, NotificationPreferences } from "@/lib/api/types"

export const notificationsKey = (org: string) => ["notifications", org] as const
export const notificationPrefsKey = (org: string) => ["notification-preferences", org] as const

const REFRESH_MS = 60_000
// What the /notifications page asks for: the API's widest window, one page at a time. Older pages are
// fetched with the `before` cursor (the `at` of the last item already loaded).
export const FULL_FEED_PAGE = 50
const FULL_FEED = { days: 90, limit: FULL_FEED_PAGE }

/** The active org's notification feed, a mark-all-read action and a mark-one-read action. Org scopes
 * only: notifications are an org feature, so a personal scope gets an idle result. The bell and Overview
 * share the default feed; `full` asks for the long window the /notifications page lists, paged with
 * `loadMore`. */
export function useNotifications({ full = false }: { full?: boolean } = {}) {
  const { scope } = useActiveScope()
  const org = scope?.kind === "org" ? scope.login : ""
  const queryClient = useQueryClient()

  const short = useQuery<NotificationFeed>({
    queryKey: notificationsKey(org),
    queryFn: () => api.notifications.feed(org),
    enabled: org !== "" && !full,
    retry: false,
    refetchInterval: REFRESH_MS,
  })
  const long = useInfiniteQuery({
    queryKey: [...notificationsKey(org), "full"],
    queryFn: ({ pageParam }) => api.notifications.feed(org, { ...FULL_FEED, ...pageParam }),
    initialPageParam: undefined as { before: string; before_id: string } | undefined,
    // A full page means there may be older items; the next page starts before the last one shown.
    getNextPageParam: (last) => {
      const tail = last.items[last.items.length - 1]
      return last.items.length === FULL_FEED_PAGE ? { before: tail.at, before_id: tail.id } : undefined
    },
    enabled: org !== "" && full,
    retry: false,
    refetchInterval: REFRESH_MS,
  })
  const pages = long.data?.pages
  // Only the first page carries the unread count (later pages are older items).
  const items = full ? (pages?.flatMap((p) => p.items) ?? []) : (short.data?.items ?? [])
  const unread = full ? (pages?.[0]?.unread_count ?? 0) : (short.data?.unread_count ?? 0)
  const { isError, error, isLoading, isFetching, refetch } = full ? long : short
  // A failed "Load more" keeps the loaded list on screen; it is reported separately from a failed first load.
  const loadMoreFailed = full && long.isFetchNextPageError
  // A failed background refresh with a list already loaded keeps the list on screen (`refreshFailed`);
  // only a failure with nothing to show replaces it.
  const refreshFailed = isError && !loadMoreFailed && items.length > 0
  const query = { isError: isError && !loadMoreFailed && items.length === 0, error: error as Error, isLoading, isFetching, refetch }

  const markRead = useMutation({
    // Items are newest first; mark up to the newest one this feed actually showed.
    mutationFn: () => api.notifications.markRead(org, items[0]?.at),
    // Prefix match: refreshes the bell's feed and the full page's together.
    onSuccess: () => queryClient.invalidateQueries({ queryKey: notificationsKey(org) }),
  })

  const dismiss = useMutation({
    mutationFn: (itemId: string) => api.notifications.dismiss(org, itemId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: notificationsKey(org) }),
  })

  const more = { hasMore: !!long.hasNextPage, loadingMore: long.isFetchingNextPage, loadMoreFailed, refreshFailed, loadMore: () => long.fetchNextPage() }
  return { org, query, markRead, dismiss, items, unread, ...more }
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
