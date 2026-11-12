"use client"

import { useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { CircleNotch } from "@phosphor-icons/react"
import { api } from "@/lib/api/client"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { SectionError } from "@/components/section-error"
import type { AutomationScheduleSetting, AutomationSchedules } from "@/lib/api/types"

type Choice = "follow" | "on" | "off"

const toChoice = (enabled: boolean | null): Choice => (enabled === null ? "follow" : enabled ? "on" : "off")
const fromChoice = (choice: Choice): boolean | null => (choice === "follow" ? null : choice === "on")

function ScheduleSelect({
  id,
  label,
  setting,
  disabled,
  onChange,
}: {
  id: string
  label: string
  setting: AutomationScheduleSetting
  disabled: boolean
  onChange: (enabled: boolean | null) => void
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={id} className="text-xs font-medium text-foreground">
        {label}
      </label>
      <select
        id={id}
        value={toChoice(setting.enabled)}
        disabled={disabled}
        onChange={(e) => onChange(fromChoice(e.target.value as Choice))}
        className="text-xs card text-foreground px-2 py-1.5 max-w-72 focus:outline-none focus:ring-1 focus:ring-ring"
      >
        <option value="follow">Follow the instance setting ({setting.instance_cadence})</option>
        <option value="on">Always run on a schedule</option>
        <option value="off">Never run on a schedule</option>
      </select>
      <p className="text-xs text-muted-foreground">
        {setting.effective ? `Runs ${setting.cadence}.` : "Does not run on its own: it only runs when someone clicks Run."}
      </p>
    </div>
  )
}

/** Org admins choose whether stale-PR nudges and Dependabot auto-triage run on a schedule, overriding the
 * instance cadence, and which repositories get scheduled nudges. */
export function AutomationSchedulesCard({ orgLogin }: { orgLogin: string }) {
  const queryClient = useQueryClient()
  const queryKey = ["automation-schedules", orgLogin]
  const query = useQuery<AutomationSchedules>({
    queryKey,
    queryFn: () => api.orgs.automationSchedules(orgLogin),
    retry: false,
  })
  const update = useMutation({
    mutationFn: (body: { pr_nudges?: boolean | null; dependabot_triage?: boolean | null }) =>
      api.orgs.setAutomationSchedules(orgLogin, body),
    onSuccess: (data) => queryClient.setQueryData(queryKey, data),
  })
  const [newRepo, setNewRepo] = useState("")
  const setRepo = useMutation({
    mutationFn: ({ repo, enabled }: { repo: string; enabled: boolean }) => {
      const [owner, name] = repo.includes("/") ? repo.split("/", 2) : [orgLogin, repo]
      return api.orgs.setPrNudgeRepo(orgLogin, owner, name, enabled)
    },
    onSuccess: () => {
      setNewRepo("")
      return queryClient.invalidateQueries({ queryKey })
    },
  })

  const data = query.data
  return (
    <div className="card mb-4">
      <div className="px-4 py-3 border-b border-border">
        <span className="section-title">Scheduled automations</span>
      </div>
      {query.isLoading ? (
        <div className="px-4 py-4 flex items-center gap-2 text-sm text-muted-foreground">
          <CircleNotch className="size-3.5 animate-spin" /> Loading…
        </div>
      ) : query.isError || !data ? (
        <SectionError
          message={`Couldn't load the automation schedules: ${query.error instanceof Error ? query.error.message : "unknown error"}`}
          onRetry={() => query.refetch()}
          retrying={query.isFetching}
        />
      ) : (
        <div className="p-4 flex flex-col gap-4">
          <p className="text-xs text-muted-foreground">
            Scheduled runs use the GitHub App installation, never a pasted token, and only touch repositories you
            enabled below or in the Dependabot triage card on the Automation page. Every action is recorded in the audit log.
          </p>
          <ScheduleSelect
            id="schedule-pr-nudges"
            label="Nudge stale pull requests automatically"
            setting={data.pr_nudges}
            disabled={update.isPending}
            onChange={(enabled) => update.mutate({ pr_nudges: enabled })}
          />
          <div className="flex flex-col gap-1.5">
            <span className="text-xs font-medium text-foreground">Repositories with stale-PR nudges</span>
            {data.pr_nudge_repos.length === 0 ? (
              <p className="text-xs text-muted-foreground">None yet: a scheduled run does nothing until a repository is added.</p>
            ) : (
              <ul className="flex flex-col gap-1">
                {data.pr_nudge_repos.map((repo) => (
                  <li key={repo} className="flex items-center gap-2 text-xs">
                    <span className="font-mono">{repo}</span>
                    <button
                      type="button"
                      className="text-muted-foreground hover:text-foreground"
                      disabled={setRepo.isPending}
                      onClick={() => setRepo.mutate({ repo, enabled: false })}
                      aria-label={`Stop nudging ${repo}`}
                    >
                      Remove
                    </button>
                  </li>
                ))}
              </ul>
            )}
            <form
              className="flex items-center gap-2"
              onSubmit={(e) => {
                e.preventDefault()
                if (newRepo.trim()) setRepo.mutate({ repo: newRepo.trim(), enabled: true })
              }}
            >
              <Input
                value={newRepo}
                onChange={(e) => setNewRepo(e.target.value)}
                placeholder={`repo or ${orgLogin}/repo`}
                aria-label="Repository to nudge"
                className="h-7 text-xs max-w-60"
              />
              <Button type="submit" size="sm" variant="outline" disabled={setRepo.isPending || !newRepo.trim()}>
                Add
              </Button>
            </form>
          </div>
          <ScheduleSelect
            id="schedule-triage"
            label="Triage Dependabot pull requests automatically"
            setting={data.dependabot_triage}
            disabled={update.isPending}
            onChange={(enabled) => update.mutate({ dependabot_triage: enabled })}
          />
          <p className="text-xs text-muted-foreground">
            Triage runs on the {data.triage_repos.length} {data.triage_repos.length === 1 ? "repository" : "repositories"} enabled in the
            Dependabot triage card.
          </p>
          {(update.isError || setRepo.isError) && (
            <p role="alert" className="text-xs text-destructive">
              {(update.error ?? setRepo.error)?.message}
            </p>
          )}
        </div>
      )}
    </div>
  )
}
