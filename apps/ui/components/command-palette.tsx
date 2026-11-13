"use client"

import { useEffect, useMemo, useRef, useState } from "react"
import { useRouter } from "next/navigation"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Dialog } from "@base-ui/react/dialog"
import { MagnifyingGlass } from "@phosphor-icons/react"
import { api } from "@/lib/api/client"
import { toast } from "@/components/ui/toast"
import { notificationsKey } from "@/lib/use-notifications"
import { useActiveScope } from "@/lib/active-scope"
import { useAuth } from "@/lib/auth-context"
import { membersHref } from "@/lib/members-href"
import { NAV_GROUPS } from "@/lib/nav-items"
import { rankCommands, type Command } from "@/lib/command-palette"
import type { InstallationMeta, MyOrgMembership } from "@/lib/api/types"

const MAX_RESULTS = 50
const KIND_LABEL = { page: "Page", action: "Action", scope: "Account", repo: "Repository" } as const

/** Cmd/Ctrl+K palette: jump to a page, run a quick action, switch account, or open a repository of the active org. */
export function CommandPalette() {
  const router = useRouter()
  const queryClient = useQueryClient()
  const { user } = useAuth()
  const { scope, setScope } = useActiveScope()
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState("")
  const [active, setActive] = useState(0)
  const listRef = useRef<HTMLUListElement>(null)

  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      // Plain Cmd/Ctrl+K only: Shift/Alt variants belong to the browser (e.g. Firefox's console), and a
      // held key must not flicker the dialog open and shut.
      if ((e.metaKey || e.ctrlKey) && !e.shiftKey && !e.altKey && !e.repeat && e.key.toLowerCase() === "k") {
        e.preventDefault()
        setOpen((v) => !v)
      }
    }
    window.addEventListener("keydown", onKeyDown)
    return () => window.removeEventListener("keydown", onKeyDown)
  }, [])

  function changeOpen(next: boolean) {
    setOpen(next)
    if (!next) {
      setQuery("")
      setActive(0)
    }
  }

  // Same keys as the sidebar, so these are already cached; nothing extra is fetched for them.
  const { data: memberships = [] } = useQuery<MyOrgMembership[]>({ queryKey: ["my-orgs"], queryFn: () => api.orgs.mine() })
  const { data: installs = [] } = useQuery<InstallationMeta[]>({ queryKey: ["installations"], queryFn: () => api.installations.list() })

  // Repositories are only fetched while the palette is open, and only for an org scope (the list call is
  // org-scoped). No token is sent: the API uses the installed App, or a workspace admin's saved PAT.
  const org = scope?.kind === "org" ? scope.login : ""
  const reposQuery = useQuery({
    queryKey: ["repos.palette", org],
    queryFn: () => api.repos.list(org, ""),
    enabled: open && org !== "",
    staleTime: 60_000,
    retry: false,
  })

  // Quick actions run in the background and report through a toast, so the palette can close at once.
  const scanMutation = useMutation({
    mutationFn: (target: string) => api.analytics.overview(target, ""),
    onSuccess: (data, target) => {
      for (const key of ["analytics.latest-scan", "analytics.history", "analytics.changes", "analytics.scans"]) {
        queryClient.invalidateQueries({ queryKey: [key, target] })
      }
      toast.success(`Scan of ${target} finished${data.score != null ? `: score ${data.score}` : ""}.`)
    },
    onError: (e, target) => toast.error(e instanceof Error ? e.message : `Scan of ${target} failed.`),
  })
  const markReadMutation = useMutation({
    mutationFn: (target: string) => api.notifications.markRead(target),
    onSuccess: (_data, target) => {
      queryClient.invalidateQueries({ queryKey: notificationsKey(target) })
      toast.success(`Marked ${target}'s notifications as read.`)
    },
    onError: (e) => toast.error(e instanceof Error ? e.message : "Couldn't mark notifications as read."),
  })

  const commands = useMemo<Command[]>(() => {
    const pages: Command[] = NAV_GROUPS.flat()
      .filter((item) => item.href !== "/audit" || user?.is_workspace_admin)
      .map((item) => ({
        id: `page:${item.href}`,
        kind: "page" as const,
        label: item.title,
        hint: "",
        href: item.href === "/collaborators" ? membersHref(memberships, scope) : item.href,
      }))
    pages.push({ id: "page:/settings", kind: "page", label: "Settings", hint: "", href: "/settings" })

    // Offered only where the caller's role allows them: org actions to members of the active org (the API
    // still enforces it), the audit log to workspace admins (it 403s for everyone else).
    const actions: Command[] = []
    if (org !== "" && memberships.some((m) => m.org_login === org)) {
      actions.push(
        { id: "action:scan", kind: "action", label: "Run security scan", hint: `Scan ${org}`, action: "scan" },
        { id: "action:mark-read", kind: "action", label: "Mark all notifications read", hint: org, action: "mark-notifications-read" },
        { id: "action:failing-ci", kind: "action", label: "Open failing CI", hint: `CI failures in ${org}`, href: "/activity#ci-failures" },
      )
    }
    if (user?.is_workspace_admin && user.email) {
      actions.push({
        id: "action:audit-me",
        kind: "action",
        label: "Open the audit log filtered to me",
        hint: "Audit log · your actions",
        href: `/audit?actor=${encodeURIComponent(user.email)}`,
      })
    }

    const personal = installs.find((i) => i.account_type === "User")
    const scopes: Command[] = [
      ...(personal ? [{ kind: "personal" as const, login: personal.account_login, hint: "Personal account" }] : []),
      ...memberships.map((m) => ({ kind: "org" as const, login: m.org_login, hint: `Organization · ${m.role}` })),
    ]
      .filter((s) => !(scope?.kind === s.kind && scope.login === s.login))
      .map((s) => ({
        id: `scope:${s.kind}:${s.login}`,
        kind: "scope" as const,
        label: `Switch to ${s.login}`,
        hint: s.hint,
        scope: { kind: s.kind, login: s.login },
      }))

    const repos: Command[] = (reposQuery.data?.repos ?? []).map((r) => {
      const [owner, name] = r.full_name.split("/")
      return {
        id: `repo:${r.full_name}`,
        kind: "repo" as const,
        label: r.full_name,
        hint: r.description ?? "",
        href: `/repos/${encodeURIComponent(owner)}~${encodeURIComponent(name)}`,
      }
    })
    return [...pages, ...actions, ...scopes, ...repos]
  }, [user?.is_workspace_admin, user?.email, memberships, installs, scope, org, reposQuery.data])

  const ranked = useMemo(() => rankCommands(commands, query), [commands, query])
  const results = useMemo(() => ranked.slice(0, MAX_RESULTS), [ranked])
  const current = Math.min(active, Math.max(results.length - 1, 0))

  useEffect(() => {
    listRef.current?.querySelector<HTMLElement>('[aria-selected="true"]')?.scrollIntoView?.({ block: "nearest" })
  }, [current, results])

  function run(cmd: Command) {
    changeOpen(false)
    if (cmd.scope) setScope(cmd.scope)
    else if (cmd.action === "scan") {
      toast.info(`Scanning ${org}…`)
      scanMutation.mutate(org)
    } else if (cmd.action === "mark-notifications-read") markReadMutation.mutate(org)
    else if (cmd.href) router.push(cmd.href)
  }

  function onInputKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === "ArrowDown") {
      e.preventDefault()
      setActive(Math.min(current + 1, results.length - 1))
    } else if (e.key === "ArrowUp") {
      e.preventDefault()
      setActive(Math.max(current - 1, 0))
    } else if (e.key === "Enter" && results[current] && !e.nativeEvent.isComposing) {
      e.preventDefault()
      run(results[current])
    }
  }

  return (
    <Dialog.Root open={open} onOpenChange={changeOpen}>
      <Dialog.Trigger
        className="flex items-center gap-1.5 rounded-md border border-border/60 px-2 py-1 text-xs text-muted-foreground hover:text-foreground hover:bg-elevated transition-colors"
        aria-label="Open command palette"
      >
        <MagnifyingGlass className="size-3.5" />
        <span className="hidden sm:inline">Search</span>
        <kbd className="hidden sm:inline rounded border border-border px-1 text-[0.625rem]">Ctrl K</kbd>
      </Dialog.Trigger>
      <Dialog.Portal>
        <Dialog.Backdrop className="fixed inset-0 z-50 bg-black/30 transition-opacity duration-150 data-ending-style:opacity-0 data-starting-style:opacity-0" />
        <Dialog.Popup className="fixed left-1/2 top-[15vh] z-50 w-[min(36rem,calc(100vw-2rem))] -translate-x-1/2 rounded-lg border border-border bg-popover text-popover-foreground shadow-xl outline-none transition-opacity duration-150 data-ending-style:opacity-0 data-starting-style:opacity-0">
          <Dialog.Title className="sr-only">Command palette</Dialog.Title>
          <Dialog.Description className="sr-only">Search pages, actions, accounts and repositories, then press Enter.</Dialog.Description>
          <div className="flex items-center gap-2 border-b border-border px-3">
            <MagnifyingGlass className="size-4 text-muted-foreground" aria-hidden />
            <input
              autoFocus
              role="combobox"
              aria-expanded={results.length > 0}
              aria-controls="command-palette-list"
              aria-activedescendant={results[current] ? `cmd-${results[current].id}` : undefined}
              aria-label="Search pages, accounts and repositories"
              placeholder="Search pages, accounts and repositories…"
              value={query}
              onChange={(e) => {
                setQuery(e.target.value)
                setActive(0)
              }}
              onKeyDown={onInputKeyDown}
              className="h-11 flex-1 bg-transparent text-sm outline-none placeholder:text-muted-foreground"
            />
          </div>
          <ul id="command-palette-list" role="listbox" ref={listRef} className="max-h-80 overflow-y-auto p-1.5">
            {results.map((c, i) => (
              <li
                key={c.id}
                id={`cmd-${c.id}`}
                role="option"
                aria-selected={i === current}
                onMouseMove={() => setActive(i)}
                onClick={() => run(c)}
                className={`flex cursor-pointer items-center justify-between gap-3 rounded-md px-2.5 py-1.5 text-sm ${
                  i === current ? "bg-elevated text-foreground" : "text-foreground/80"
                }`}
              >
                <span className="flex min-w-0 flex-col">
                  <span className="truncate">{c.label}</span>
                  {c.hint && <span className="truncate text-[0.6875rem] text-muted-foreground">{c.hint}</span>}
                </span>
                <span className="shrink-0 text-[0.6875rem] text-muted-foreground">{KIND_LABEL[c.kind]}</span>
              </li>
            ))}
          </ul>
          {results.length === 0 && (
            <p role="status" className="px-3.5 py-3 text-sm text-muted-foreground">
              No matches
            </p>
          )}
          {ranked.length > results.length && (
            <p className="px-3.5 pb-2 text-[0.6875rem] text-muted-foreground">
              Showing {results.length} of {ranked.length}. Keep typing to narrow the list.
            </p>
          )}
          {org !== "" && (
            <p className="border-t border-border px-3 py-1.5 text-[0.6875rem] text-muted-foreground" aria-live="polite">
              {reposQuery.isError
                ? `Couldn't load ${org}'s repositories.`
                : reposQuery.isFetching
                  ? `Loading ${org}'s repositories…`
                  : `Searching pages, accounts and ${org}'s repositories.`}
            </p>
          )}
        </Dialog.Popup>
      </Dialog.Portal>
    </Dialog.Root>
  )
}
