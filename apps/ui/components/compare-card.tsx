"use client"

import { useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { api } from "@/lib/api/client"
import { changeBetween, orgMetrics, periodMetrics, type CompareMetric } from "@/lib/compare"
import type { CockpitResponse, MyOrgMembership } from "@/lib/api/types"

const TONE_CLASS = { better: "text-accent", worse: "text-destructive", same: "text-muted-foreground" } as const

function MetricTable({ headers, metrics }: { headers: [string, string]; metrics: CompareMetric[] }) {
  return (
    <table className="w-full text-sm">
      <thead>
        <tr className="text-left text-xs text-muted-foreground">
          <th scope="col" className="py-1.5 pr-3 font-medium">Metric</th>
          <th scope="col" className="py-1.5 pr-3 font-medium text-right">{headers[0]}</th>
          <th scope="col" className="py-1.5 pr-3 font-medium text-right">{headers[1]}</th>
          <th scope="col" className="py-1.5 font-medium text-right">Change</th>
        </tr>
      </thead>
      <tbody>
        {metrics.map((m) => {
          const change = changeBetween(m)
          return (
            <tr key={m.label} className="border-t border-border/40">
              <th scope="row" className="py-1.5 pr-3 font-normal text-foreground">{m.label}</th>
              <td className="py-1.5 pr-3 text-right tabular-nums">{m.a === null ? "—" : m.format(m.a)}</td>
              <td className="py-1.5 pr-3 text-right tabular-nums">{m.b === null ? "—" : m.format(m.b)}</td>
              <td className={`py-1.5 text-right tabular-nums ${change?.tone ? TONE_CLASS[change.tone] : "text-muted-foreground"}`}>
                {change?.text ?? "—"}
              </td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}

/** Compare the active account with itself over time (the last two weeks against the two before) or with another
 * organization the user belongs to. Uses only data the cockpit already returns. */
export function CompareCard({
  login,
  cockpit,
  memberships,
}: {
  login: string
  cockpit: CockpitResponse
  memberships: MyOrgMembership[]
}) {
  // Collapsed until asked for: it repeats numbers the cards above already show.
  const [open, setOpen] = useState(false)
  const [mode, setMode] = useState<"periods" | "orgs">("periods")
  const [other, setOther] = useState("")
  const candidates = memberships.map((m) => m.org_login).filter((l) => l.toLowerCase() !== login.toLowerCase())

  // No token is sent, like the Overview's own cockpit query: the API resolves the installation server-side.
  const otherQuery = useQuery({
    queryKey: ["analytics.cockpit", other],
    queryFn: () => api.analytics.cockpit(other),
    enabled: open && mode === "orgs" && other.length > 0,
    retry: false,
  })

  return (
    <div className="card mb-6">
      <div className="px-4 py-3 border-b border-border flex items-center gap-3">
        <button
          type="button"
          aria-expanded={open}
          onClick={() => setOpen((v) => !v)}
          className="section-label hover:text-foreground"
        >
          Compare {open ? "▾" : "▸"}
        </button>
        {open && <div role="group" aria-label="Comparison type" className="flex gap-1">
          {(
            [
              ["periods", "Periods"],
              ["orgs", "Organizations"],
            ] as const
          ).map(([id, label]) => (
            <button
              key={id}
              type="button"
              aria-pressed={mode === id}
              onClick={() => setMode(id)}
              className={`text-xs px-2 py-1 border border-border ${mode === id ? "bg-elevated text-foreground" : "text-muted-foreground hover:text-foreground"}`}
            >
              {label}
            </button>
          ))}
        </div>}
      </div>
      {open && <div className="p-4">
        {mode === "periods" ? (
          <MetricTable headers={["Previous 2 weeks", "Last 2 weeks"]} metrics={periodMetrics(cockpit)} />
        ) : candidates.length === 0 ? (
          <p className="text-sm text-muted-foreground">Join or connect another organization to compare it with {login}.</p>
        ) : (
          <div className="flex flex-col gap-3">
            <label className="flex items-center gap-2 text-xs text-foreground">
              Compare {login} with
              <select
                value={other}
                onChange={(e) => setOther(e.target.value)}
                className="text-xs card text-foreground px-2 py-1.5 focus:outline-none focus:ring-1 focus:ring-ring"
              >
                <option value="">Choose an organization…</option>
                {candidates.map((l) => (
                  <option key={l} value={l}>
                    {l}
                  </option>
                ))}
              </select>
            </label>
            {other && otherQuery.isLoading && <p className="text-sm text-muted-foreground">Loading {other}…</p>}
            {other && otherQuery.isError && (
              <p role="alert" className="text-sm text-destructive">
                Couldn&apos;t load {other}: {otherQuery.error instanceof Error ? otherQuery.error.message : "unknown error"}
              </p>
            )}
            {other && otherQuery.data && (
              <MetricTable headers={[login, other]} metrics={orgMetrics(cockpit, otherQuery.data)} />
            )}
          </div>
        )}
      </div>}
    </div>
  )
}
