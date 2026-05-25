import { PageHeader } from "@/components/page-header"
import { EmptyStatePage } from "@/components/empty-state"

export const metadata = { title: "Pull Requests · clevis" }

export default function PullRequestsPage() {
  return (
    <>
      <PageHeader title="Pull Requests" description="Open PRs across your organization." />
      <EmptyStatePage message="— no organization connected" />
    </>
  )
}
