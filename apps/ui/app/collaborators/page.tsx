"use client"

import { useEffect } from "react"
import { useRouter } from "next/navigation"
import { useQuery } from "@tanstack/react-query"
import { PageHeader } from "@/components/page-header"
import { EmptyState } from "@/components/empty-state"
import { api } from "@/lib/api/client"
import type { MyOrgMembership } from "@/lib/api/types"

export default function CollaboratorsPage() {
  const router = useRouter()

  const { data: memberships = [], isLoading } = useQuery<MyOrgMembership[]>({
    queryKey: ["my-orgs"],
    queryFn: () => api.orgs.mine(),
  })

  const adminOrgs = memberships.filter((m) => m.role === "admin")
  const defaultOrg = typeof window !== "undefined" ? localStorage.getItem("default_org") || "" : ""
  const target = adminOrgs.find((m) => m.org_login === defaultOrg) || adminOrgs[0]

  useEffect(() => {
    if (target) router.replace(`/settings/org/${encodeURIComponent(target.org_login)}/members`)
  }, [target, router])

  if (isLoading || target) {
    return (
      <>
        <PageHeader
          title="Collaborators"
          description="Manage your organization's team and invitations."
        />
        <div className="bg-card border border-border">
          <EmptyState title="Redirecting…" description="Taking you to member management." />
        </div>
      </>
    )
  }

  return (
    <>
      <PageHeader
        title="Collaborators"
        description="Manage your organization's team and invitations."
      />
      <div className="bg-card border border-border">
        <EmptyState
          title="No organization to manage"
          description="Member management is available for organizations where you're an admin. Visit Settings to see your organizations."
        />
      </div>
    </>
  )
}
