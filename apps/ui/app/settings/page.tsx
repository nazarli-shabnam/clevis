import { PageHeader } from "@/components/page-header"
import { EmptyStatePage } from "@/components/empty-state"

export const metadata = { title: "Settings · clevis" }

export default function SettingsPage() {
  return (
    <>
      <PageHeader title="Settings" description="Configure your clevis workspace." />
      <EmptyStatePage message="— settings coming in a future phase" />
    </>
  )
}
