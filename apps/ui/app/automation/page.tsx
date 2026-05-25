import { PageHeader } from "@/components/page-header"
import { EmptyState } from "@/components/empty-state"
import { Zap } from "lucide-react"

export default function AutomationPage() {
  return (
    <>
      <PageHeader
        title="Automation"
        description="Manage workflows and automated actions."
      />
      <div className="bg-card border border-border">
        <EmptyState
          icon={Zap}
          title="Automation coming soon"
          description="Trigger GitHub Actions workflows, dispatch events, and automate repo hygiene tasks — all from one place."
        />
      </div>
    </>
  )
}
