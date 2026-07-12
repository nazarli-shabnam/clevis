export const metadata = { title: "Pull Requests · clevis" }

import { PageHeader } from "@/components/page-header"
import { EmptyStatePage } from "@/components/empty-state"

export default function PullRequestsPage() {
  return (
    <>
      <PageHeader title="Pull Requests" description="Open PRs across your organization." />
      <EmptyStatePage message="Pull requests — coming soon" />
    </>
  )
}
