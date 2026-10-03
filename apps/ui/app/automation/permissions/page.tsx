"use client"

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { ArrowClockwise, ArrowSquareOut, CheckCircle, Warning, XCircle } from "@phosphor-icons/react"
import { PageHeader } from "@/components/page-header"
import { SectionError } from "@/components/section-error"
import { Button } from "@/components/ui/button"
import { reviewUrl } from "@/components/permission-drift-notice"
import { api } from "@/lib/api/client"
import { useActiveScope } from "@/lib/active-scope"
import { relativeTime } from "@/lib/format"
import type { InstallationMeta } from "@/lib/api/types"

const formatPermissions = (perms: Record<string, string>) =>
  Object.entries(perms).map(([name, level]) => `${name}: ${level}`).join(", ")

export default function PermissionsPage() {
  const { scope } = useActiveScope()
  const queryClient = useQueryClient()
  const queryKey = ["installations.permissions", scope?.kind, scope?.login]

  const installsQuery = useQuery<InstallationMeta[]>({
    queryKey,
    queryFn: () => (scope?.kind === "org" ? api.installations.listForOrg(scope.login) : api.installations.list()),
    enabled: !!scope,
    retry: false,
  })

  const refresh = useMutation({
    mutationFn: (installationId: number) =>
      api.installations.refreshPermissions(
        scope?.kind === "org" ? { scope: "org", orgLogin: scope.login } : { scope: "me" },
        installationId,
      ),
    onSuccess: () => queryClient.invalidateQueries({ queryKey }),
  })

  const installs = (installsQuery.data ?? []).filter((i) => i.installation_id != null)

  return (
    <>
      <PageHeader
        title="Permissions"
        description="Which automations your GitHub App installation can run, and which need extra access."
      />

      {!scope && <p className="text-sm text-muted-foreground">Select an account in the sidebar first.</p>}
      {installsQuery.isError && (
        <SectionError message={installsQuery.error.message} onRetry={() => installsQuery.refetch()} />
      )}
      {installsQuery.isSuccess && installs.length === 0 && (
        <p className="text-sm text-muted-foreground">
          No GitHub App installation is connected for {scope?.login}. Connect one from Settings first.
        </p>
      )}

      <div className="flex flex-col gap-5">
        {installs.map((install) => {
          const syncedAt = install.permissions_synced_at
          const url = reviewUrl(install)
          const refreshingThis = refresh.variables === install.installation_id
          return (
            <section key={install.id} className="card">
              <div className="px-4 py-3 border-b border-border flex items-center justify-between gap-4">
                <div className="flex flex-col gap-0.5">
                  <span className="section-label">{install.account_login}</span>
                  <span className="text-[0.6875rem] text-muted-foreground">
                    {syncedAt ? `Permissions synced ${relativeTime(syncedAt)}` : "Permissions not yet checked"}
                  </span>
                </div>
                <div className="flex items-center gap-2">
                  {url && (
                    <a
                      href={url}
                      target="_blank"
                      rel="noreferrer"
                      className="text-xs text-primary hover:underline inline-flex items-center gap-1"
                    >
                      <ArrowSquareOut className="size-3.5" />
                      Review on GitHub
                    </a>
                  )}
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={refresh.isPending}
                    onClick={() => refresh.mutate(install.installation_id!)}
                  >
                    <ArrowClockwise className="size-3.5" />
                    {refresh.isPending && refreshingThis ? "Re-syncing…" : "Re-sync permissions"}
                  </Button>
                </div>
              </div>
              {refresh.isError && refreshingThis && (
                <p className="px-4 pt-3 text-xs text-destructive flex items-center gap-1.5">
                  <Warning className="size-3 shrink-0" />
                  {refresh.error.message}
                </p>
              )}
              <table className="w-full text-xs">
                <thead>
                  <tr className="border-b border-border text-muted-foreground">
                    <th className="font-medium px-4 py-2 text-left">Automation</th>
                    <th className="font-medium px-4 py-2 text-left">Needs</th>
                    <th className="font-medium px-4 py-2 text-left">Status</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border">
                  {(install.automations ?? []).map((a) => {
                    const blocked = Object.keys(a.missing).length > 0
                    return (
                      <tr key={a.feature}>
                        <td className="px-4 py-2.5 text-foreground">{a.label}</td>
                        <td className="px-4 py-2.5 font-mono text-muted-foreground">{formatPermissions(a.required)}</td>
                        <td className="px-4 py-2.5">
                          {!syncedAt ? (
                            <span className="text-muted-foreground">Unknown</span>
                          ) : blocked ? (
                            <span className="text-warning inline-flex items-center gap-1">
                              <XCircle className="size-3.5" />
                              Missing {formatPermissions(a.missing)}
                            </span>
                          ) : (
                            <span className="text-accent inline-flex items-center gap-1">
                              <CheckCircle className="size-3.5" />
                              Ready
                            </span>
                          )}
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </section>
          )
        })}
      </div>
    </>
  )
}
