export const metadata = { title: "Automation · clevis" }

import { PageHeader } from "@/components/page-header"
import { EmptyState } from "@/components/empty-state"

export default function AutomationPage() {
  return (
    <>
      <PageHeader
        title="Automation"
        description="Manage workflows and automated actions."
      />
      <div className="card">
        <EmptyState
          title="Automation coming soon"
          description="Trigger GitHub Actions workflows, dispatch events, and automate repo hygiene tasks — all from one place."
        />
      </div>
    </>
  )
}
