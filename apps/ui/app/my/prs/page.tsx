export const metadata = { title: "My PRs · clevis" }

import { PageHeader } from "@/components/page-header"
import { EmptyStatePage } from "@/components/empty-state"

export default function MyPRsPage() {
  return (
    <>
      <PageHeader title="My PRs" description="Pull requests you've authored." />
      <EmptyStatePage message="— no organization connected" />
    </>
  )
}
