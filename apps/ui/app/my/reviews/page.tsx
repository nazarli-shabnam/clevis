export const metadata = { title: "My Reviews · clevis" }

import { PageHeader } from "@/components/page-header"
import { EmptyStatePage } from "@/components/empty-state"

export default function MyReviewsPage() {
  return (
    <>
      <PageHeader title="My Reviews" description="PRs awaiting your review." />
      <EmptyStatePage message="My reviews — coming soon" />
    </>
  )
}
