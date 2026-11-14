"use client"

import { useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { CircleNotch } from "@phosphor-icons/react"
import { api } from "@/lib/api/client"
import { EmptyStateAction } from "@/components/empty-state-action"
import { SectionError } from "@/components/section-error"
import { Button } from "@/components/ui/button"
import { ConfirmDialog } from "@/components/ui/confirm-dialog"
import { Input } from "@/components/ui/input"
import type {
  AlertEvent,
  DestinationKind,
  NotificationDestination,
  NotificationDestinationPatch,
} from "@/lib/api/types"

export const EVENT_LABELS: Record<AlertEvent, string> = {
  score_drop: "Security score drops",
  new_critical_alert: "New critical/high alert",
  stale_pr_summary: "Stale PRs nudged",
  automation_failed: "Automation failed",
  waiver_expired: "Waiver expired",
  sla_breach: "Dependabot SLA breached",
}
const ALL_EVENTS = Object.keys(EVENT_LABELS) as AlertEvent[]

const KIND_LABELS: Record<DestinationKind, string> = {
  slack: "Slack",
  teams: "Microsoft Teams",
  generic: "Webhook",
  email: "Email",
}

const splitRecipients = (text: string) => text.split(/[,\s]+/).filter(Boolean)

/**
 * Edits a destination in place with the existing partial PATCH. The webhook URL and signing secret are
 * write-only (the API never returns them), so leaving them blank keeps the stored value. Only changed
 * fields are sent.
 */
function EditForm({
  dest,
  patch,
  onDone,
}: {
  dest: NotificationDestination
  patch: (body: NotificationDestinationPatch) => Promise<unknown>
  onDone: () => void
}) {
  const isEmail = dest.kind === "email"
  const [name, setName] = useState(dest.name)
  const [minDrop, setMinDrop] = useState(String(dest.min_score_drop))
  const [recipients, setRecipients] = useState((dest.recipients ?? []).join(", "))
  const [url, setUrl] = useState("")
  const [secret, setSecret] = useState("")
  const [error, setError] = useState("")
  const [saving, setSaving] = useState(false)

  const dropValid = Number.isInteger(Number(minDrop)) && Number(minDrop) >= 1 && Number(minDrop) <= 100
  const recipientList = splitRecipients(recipients)
  const body: NotificationDestinationPatch = {}
  if (name.trim() !== dest.name) body.name = name.trim()
  if (Number(minDrop) !== dest.min_score_drop) body.min_score_drop = Number(minDrop)
  if (isEmail) {
    if (recipientList.join(",") !== (dest.recipients ?? []).join(",")) body.recipients = recipientList
  } else {
    if (url.trim()) body.url = url.trim()
    if (dest.kind === "generic" && secret) body.secret = secret
  }
  const valid = name.trim() !== "" && dropValid && (!isEmail || recipientList.length > 0)
  const canSave = valid && Object.keys(body).length > 0 && !saving

  async function save(e: React.FormEvent) {
    e.preventDefault()
    if (!canSave) return
    setSaving(true)
    setError("")
    try {
      await patch(body)
      onDone()
    } catch (err) {
      setError(err instanceof Error ? err.message : "Update failed")
    } finally {
      setSaving(false)
    }
  }

  const id = (field: string) => `dest-${dest.id}-${field}`
  return (
    <form onSubmit={save} className="border border-border p-3 grid gap-3 sm:grid-cols-2" aria-label={`Edit ${dest.name}`}>
      <div>
        <label htmlFor={id("name")} className="font-medium block mb-1.5">Name</label>
        <Input id={id("name")} maxLength={100} value={name} onChange={(e) => setName(e.target.value)} />
      </div>
      <div>
        <label htmlFor={id("drop")} className="font-medium block mb-1.5">Score-drop threshold (points)</label>
        <Input id={id("drop")} type="number" min={1} max={100} value={minDrop} onChange={(e) => setMinDrop(e.target.value)} />
      </div>
      {isEmail ? (
        <div className="sm:col-span-2">
          <label htmlFor={id("recipients")} className="font-medium block mb-1.5">Recipients</label>
          <Input
            id={id("recipients")}
            placeholder="comma-separated; must be verified members of this organization"
            value={recipients}
            onChange={(e) => setRecipients(e.target.value)}
          />
        </div>
      ) : (
        <>
          <div className={dest.kind === "generic" ? "" : "sm:col-span-2"}>
            <label htmlFor={id("url")} className="font-medium block mb-1.5">New webhook URL</label>
            <Input
              id={id("url")}
              type="password"
              autoComplete="off"
              placeholder="Leave blank to keep the current URL"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
            />
          </div>
          {dest.kind === "generic" && (
            <div>
              <label htmlFor={id("secret")} className="font-medium block mb-1.5">New signing secret</label>
              <Input
                id={id("secret")}
                type="password"
                autoComplete="off"
                placeholder={dest.signed ? "Leave blank to keep the current secret" : "Leave blank to stay unsigned"}
                value={secret}
                onChange={(e) => setSecret(e.target.value)}
              />
            </div>
          )}
        </>
      )}
      {error && <p role="alert" className="sm:col-span-2 text-destructive">Couldn&apos;t save: {error}</p>}
      <div className="sm:col-span-2 flex gap-2">
        <Button type="submit" size="sm" disabled={!canSave}>{saving ? "Saving…" : "Save changes"}</Button>
        <Button type="button" variant="outline" size="sm" onClick={onDone}>Cancel</Button>
      </div>
    </form>
  )
}

/** One row owns its mutations, so a slow or failing action on one destination never disables or flags another. */
function DestinationRow({ orgLogin, dest }: { orgLogin: string; dest: NotificationDestination }) {
  const queryClient = useQueryClient()
  const queryKey = ["notification-destinations", orgLogin]
  const [confirming, setConfirming] = useState(false)
  const [editing, setEditing] = useState(false)

  const update = useMutation({
    mutationFn: (patch: NotificationDestinationPatch) =>
      api.notificationDestinations.update(orgLogin, dest.id, patch),
    // Also on error: a PATCH can commit and lose its response, and the next toggle must not start from stale values.
    onSettled: () => queryClient.invalidateQueries({ queryKey }),
  })
  // Separate from `update` so a failed save is reported in the edit form only, not twice.
  const edit = useMutation({
    mutationFn: (patch: NotificationDestinationPatch) => api.notificationDestinations.update(orgLogin, dest.id, patch),
    onSettled: () => queryClient.invalidateQueries({ queryKey }),
  })
  const test = useMutation({ mutationFn: () => api.notificationDestinations.test(orgLogin, dest.id) })
  const remove = useMutation({
    mutationFn: () => api.notificationDestinations.remove(orgLogin, dest.id),
    onSuccess: () => {
      setConfirming(false)
      return queryClient.invalidateQueries({ queryKey })
    },
    onError: () => {
      setConfirming(false)
      return queryClient.invalidateQueries({ queryKey })
    },
  })
  const busy = update.isPending || edit.isPending || remove.isPending

  function toggleEvent(event: AlertEvent, on: boolean) {
    update.mutate(on ? { add_events: [event] } : { remove_events: [event] })
  }

  return (
    <li className="px-4 py-3 flex flex-col gap-2 text-xs">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="font-medium text-foreground">
            {dest.name}
            <span className="ml-2 text-muted-foreground font-normal">
              {KIND_LABELS[dest.kind]}
              {dest.signed ? " · signed" : ""}
              {!dest.enabled ? " · paused" : ""}
            </span>
          </p>
          {dest.recipients && dest.recipients.length > 0 && (
            <p className="text-muted-foreground break-all">{dest.recipients.join(", ")}</p>
          )}
          {dest.events.includes("score_drop") && (
            <p className="text-muted-foreground">Score-drop alerts fire at {dest.min_score_drop}+ points.</p>
          )}
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <label className="flex items-center gap-1.5">
            <input
              type="checkbox"
              checked={dest.enabled}
              disabled={busy}
              onChange={(e) => update.mutate({ enabled: e.target.checked })}
              aria-label={`Enabled: ${dest.name}`}
            />
            Enabled
          </label>
          <Button variant="outline" size="sm" disabled={test.isPending} onClick={() => test.mutate()} aria-label={`Send a test to ${dest.name}`}>
            {test.isPending ? "Sending…" : "Test"}
          </Button>
          <Button variant="outline" size="sm" disabled={busy || editing} onClick={() => setEditing(true)} aria-label={`Edit ${dest.name}`}>
            Edit
          </Button>
          <Button variant="outline" size="sm" disabled={busy} onClick={() => setConfirming(true)} aria-label={`Delete ${dest.name}`}>
            Delete
          </Button>
        </div>
      </div>

      {editing && <EditForm dest={dest} patch={edit.mutateAsync} onDone={() => setEditing(false)} />}

      <fieldset className="flex flex-wrap gap-x-4 gap-y-1" disabled={busy}>
        <legend className="sr-only">Events for {dest.name}</legend>
        {ALL_EVENTS.map((event) => (
          <label key={event} className="flex items-center gap-1.5">
            <input
              type="checkbox"
              checked={dest.events.includes(event)}
              onChange={(e) => toggleEvent(event, e.target.checked)}
            />
            {EVENT_LABELS[event]}
          </label>
        ))}
      </fieldset>

      {test.isSuccess && (
        <p role="status" className={test.data.ok ? "text-muted-foreground" : "text-destructive"}>
          Test: {test.data.detail}
        </p>
      )}
      {test.isError && <p role="alert" className="text-destructive">Test failed: {test.error.message}</p>}
      {update.isError && <p role="alert" className="text-destructive">Couldn&apos;t update: {update.error.message}</p>}
      {remove.isError && <p role="alert" className="text-destructive">Couldn&apos;t delete: {remove.error.message}</p>}

      <ConfirmDialog
        open={confirming}
        onOpenChange={setConfirming}
        title={`Delete "${dest.name}"?`}
        description="Alerts stop going to this destination. Its webhook URL and signing secret are discarded and can't be recovered."
        confirmLabel="Delete"
        pending={remove.isPending}
        onConfirm={() => remove.mutate()}
      />
    </li>
  )
}

/** Org admins route alerts (score drops, new critical alerts, stale PRs, failed automation) to chat, a webhook or email. */
export function NotificationDestinationsCard({ orgLogin }: { orgLogin: string }) {
  const queryClient = useQueryClient()
  const queryKey = ["notification-destinations", orgLogin]
  const query = useQuery<NotificationDestination[]>({
    queryKey,
    queryFn: () => api.notificationDestinations.list(orgLogin),
    retry: false,
  })

  const [kind, setKind] = useState<DestinationKind>("slack")
  const [name, setName] = useState("")
  const [url, setUrl] = useState("")
  const [recipients, setRecipients] = useState("")
  const [secret, setSecret] = useState("")
  const [minDrop, setMinDrop] = useState("10")
  const [events, setEvents] = useState<AlertEvent[]>(["score_drop"])

  const create = useMutation({
    mutationFn: () =>
      api.notificationDestinations.create(orgLogin, {
        kind,
        name: name.trim(),
        events,
        min_score_drop: Number(minDrop),
        ...(kind === "email"
          ? { recipients: recipients.split(/[,\s]+/).filter(Boolean) }
          : { url: url.trim(), ...(kind === "generic" && secret ? { secret } : {}) }),
      }),
    onSuccess: () => {
      // The URL and secret are credentials: drop them from the form as soon as they are stored.
      setName("")
      setUrl("")
      setSecret("")
      setRecipients("")
      return queryClient.invalidateQueries({ queryKey })
    },
    // A create can commit and lose its response. The form keeps its values for a retry, so reload the list:
    // if the destination is already there, the admin sees it instead of adding a duplicate.
    onError: () => queryClient.invalidateQueries({ queryKey }),
  })

  const hasTarget = kind === "email" ? recipients.split(/[,\s]+/).filter(Boolean).length > 0 : url.trim() !== ""
  const dropValid = Number.isInteger(Number(minDrop)) && Number(minDrop) >= 1 && Number(minDrop) <= 100
  const canSubmit = name.trim() !== "" && hasTarget && dropValid && !create.isPending

  return (
    <div className="card mt-4">
      <div className="px-4 py-3 border-b border-border">
        <span className="section-title">Alert destinations</span>
      </div>
      <p className="px-4 pt-3 text-xs text-muted-foreground">
        Send alerts to Slack, Microsoft Teams, a signed webhook or email. A webhook URL is a credential: it is stored
        encrypted and never shown again. Every change and send is written to the audit log.
      </p>

      {query.isLoading ? (
        <div className="px-4 py-4 flex items-center gap-2 text-sm text-muted-foreground">
          <CircleNotch className="size-3.5 animate-spin" /> Loading…
        </div>
      ) : !query.data ? (
        <SectionError
          message={`Couldn't load destinations: ${query.error instanceof Error ? query.error.message : "unknown error"}`}
          onRetry={() => query.refetch()}
          retrying={query.isFetching}
        />
      ) : query.data.length === 0 ? (
        <EmptyStateAction
          message="No destinations yet. Get a Slack, Teams, email or webhook message when the score drops, a critical alert lands or a waiver expires."
          actionLabel="Add a destination"
          focusId="dest-name"
        />
      ) : (
        <ul className="divide-y divide-border">
          {query.data.map((d) => (
            <DestinationRow key={d.id} orgLogin={orgLogin} dest={d} />
          ))}
        </ul>
      )}

      <form
        className="border-t border-border p-4 grid gap-3 sm:grid-cols-2"
        onSubmit={(e) => {
          e.preventDefault()
          if (canSubmit) create.mutate()
        }}
      >
        <div>
          <label htmlFor="dest-kind" className="text-xs font-medium block mb-1.5">Type</label>
          <select
            id="dest-kind"
            value={kind}
            onChange={(e) => setKind(e.target.value as DestinationKind)}
            className="text-xs card text-foreground px-2 py-1.5 w-full focus:outline-none focus:ring-1 focus:ring-ring"
          >
            {(Object.keys(KIND_LABELS) as DestinationKind[]).map((k) => (
              <option key={k} value={k}>{KIND_LABELS[k]}</option>
            ))}
          </select>
        </div>
        <div>
          <label htmlFor="dest-name" className="text-xs font-medium block mb-1.5">Name</label>
          <Input id="dest-name" maxLength={100} placeholder="e.g. #security-alerts" value={name} onChange={(e) => setName(e.target.value)} />
        </div>
        {kind === "email" ? (
          <div className="sm:col-span-2">
            <label htmlFor="dest-recipients" className="text-xs font-medium block mb-1.5">Recipients</label>
            <Input
              id="dest-recipients"
              placeholder="comma-separated; must be verified members of this organization"
              value={recipients}
              onChange={(e) => setRecipients(e.target.value)}
            />
          </div>
        ) : (
          <>
            <div className={kind === "generic" ? "" : "sm:col-span-2"}>
              <label htmlFor="dest-url" className="text-xs font-medium block mb-1.5">Webhook URL</label>
              <Input
                id="dest-url"
                type="password"
                autoComplete="off"
                placeholder="https://…"
                value={url}
                onChange={(e) => setUrl(e.target.value)}
              />
            </div>
            {kind === "generic" && (
              <div>
                <label htmlFor="dest-secret" className="text-xs font-medium block mb-1.5">Signing secret (optional)</label>
                <Input
                  id="dest-secret"
                  type="password"
                  autoComplete="off"
                  value={secret}
                  onChange={(e) => setSecret(e.target.value)}
                />
              </div>
            )}
          </>
        )}
        <fieldset className="sm:col-span-2 flex flex-wrap gap-x-4 gap-y-1 text-xs">
          <legend className="text-xs font-medium mb-1.5">Events</legend>
          {ALL_EVENTS.map((event) => (
            <label key={event} className="flex items-center gap-1.5">
              <input
                type="checkbox"
                checked={events.includes(event)}
                onChange={(e) => setEvents((cur) => (e.target.checked ? [...cur, event] : cur.filter((x) => x !== event)))}
              />
              {EVENT_LABELS[event]}
            </label>
          ))}
        </fieldset>
        <div>
          <label htmlFor="dest-drop" className="text-xs font-medium block mb-1.5">Score-drop threshold (points)</label>
          <Input id="dest-drop" type="number" min={1} max={100} value={minDrop} onChange={(e) => setMinDrop(e.target.value)} />
        </div>
        <div className="flex items-end">
          <Button type="submit" disabled={!canSubmit}>
            {create.isPending ? "Adding…" : "Add destination"}
          </Button>
        </div>
        {create.isError && (
          <p role="alert" className="sm:col-span-2 text-xs text-destructive">
            Couldn&apos;t add the destination: {create.error.message}
          </p>
        )}
      </form>
    </div>
  )
}
