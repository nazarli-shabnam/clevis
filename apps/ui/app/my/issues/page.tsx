import { PageHeader } from "@/components/page-header"
import { EmptyStatePage } from "@/components/empty-state"

export const metadata = { title: "My Issues · clevis" }

export default function MyIssuesPage() {
  return (
    <>
      <PageHeader title="My Issues" description="Issues assigned to you." />
      <EmptyStatePage message="— no organization connected" />
    </>
  )
}
