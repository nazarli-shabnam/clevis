import { PageHeader } from "@/components/page-header"
import { EmptyState } from "@/components/empty-state"
import { Radio } from "lucide-react"

export default function ActivityPage() {
  return (
    <>
      <PageHeader
        title="Activity"
        description="Recent events across your GitHub organization."
      />
      <div className="bg-card border border-border">
        <EmptyState
          icon={Radio}
          title="Activity feed coming soon"
          description="Connect a GitHub organization to stream real-time events — commits, PRs, releases, and more — into a unified feed."
        />
      </div>
    </>
  )
}
