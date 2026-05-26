export const metadata = { title: "Collaborators · clevis" }

import { PageHeader } from "@/components/page-header"
import { EmptyState } from "@/components/empty-state"
import { Users } from "lucide-react"

export default function CollaboratorsPage() {
  return (
    <>
      <PageHeader
        title="Collaborators"
        description="Manage your organization's team and invitations."
      />
      <div className="bg-card border border-border">
        <EmptyState
          icon={Users}
          title="Collaborators coming soon"
          description="View your organization roster, manage pending invitations, and fix member permissions from a single dashboard."
        />
      </div>
    </>
  )
}
