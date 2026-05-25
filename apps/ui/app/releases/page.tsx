import { PageHeader } from "@/components/page-header"
import { EmptyStatePage } from "@/components/empty-state"

export const metadata = { title: "Releases · clevis" }

export default function ReleasesPage() {
  return (
    <>
      <PageHeader title="Releases" description="Release history across your organization." />
      <EmptyStatePage message="— no organization connected" />
    </>
  )
}
