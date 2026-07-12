"use client"

import { useEffect } from "react"
import { useRouter } from "next/navigation"
import { useQuery } from "@tanstack/react-query"
import { Loader2 } from "lucide-react"
import { PageHeader } from "@/components/page-header"
import { EmptyStatePage } from "@/components/empty-state"
import { api } from "@/lib/api/client"

export default function CollaboratorsPage() {
  const router = useRouter()
  const { data: orgs, isLoading } = useQuery({
    queryKey: ["orgs", "mine"],
    queryFn: () => api.orgs.mine(),
  })

  const adminOrg = orgs?.find((o) => o.role === "admin")

  useEffect(() => {
    if (adminOrg) {
      router.replace(`/settings/org/${adminOrg.github_login}/members`)
    }
  }, [adminOrg, router])

  if (isLoading || adminOrg) {
    return (
      <>
        <PageHeader title="Collaborators" description="Manage your organization's team and invitations." />
        <div className="px-4 py-8 flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="size-3.5 animate-spin" /> Loading…
        </div>
      </>
    )
  }

  return (
    <>
      <PageHeader title="Collaborators" description="Manage your organization's team and invitations." />
      <EmptyStatePage
        message="Member management lives under Settings for each organization you admin"
        action={{ href: "/settings", label: "open Settings" }}
      />
    </>
  )
}
