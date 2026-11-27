"use client"

import Link from "next/link"
import { useParams } from "next/navigation"
import { useQuery } from "@tanstack/react-query"
import { CircleNotch } from "@phosphor-icons/react"
import { PageHeader } from "@/components/page-header"
import { api } from "@/lib/api/client"
import { orgRoleFor } from "@/lib/members-href"
import { HygieneScoringCard } from "@/components/hygiene-scoring-card"
import { ScheduledScansCard } from "@/components/scheduled-scans-card"
import { DigestCard } from "@/components/digest-card"
import { AutomationSchedulesCard } from "@/components/automation-schedules-card"
import { WaiverRequestsCard } from "@/components/waiver-requests-card"
import { WaiversCard } from "@/components/waivers-card"
import { ClevisMembersCard } from "@/components/clevis-members-card"
import { IngestionHealthCard } from "@/components/ingestion-health-card"
import { EvidenceExportCard } from "@/components/evidence-export-card"
import { NotificationDestinationsCard } from "@/components/notification-destinations-card"
import { BadgeSettingsCard } from "@/components/badge-settings-card"
import { ApiTokensCard } from "@/components/api-tokens-card"
import type { MyOrgMembership } from "@/lib/api/types"

// The org's configuration, grouped so a setting can be found without scrolling past unrelated ones.
// The ids are the in-page anchors (/settings/org/<login>/settings#scanning).
const SECTIONS = [
  { id: "access", label: "Members and access" },
  { id: "scanning", label: "Scanning" },
  { id: "notifications", label: "Notifications and digest" },
  { id: "automation", label: "Automation" },
  { id: "waivers", label: "Waivers" },
  { id: "health", label: "Health and evidence" },
] as const

function Section({ id, label, children }: { id: string; label: string; children: React.ReactNode }) {
  return (
    <section id={id} aria-labelledby={`${id}-heading`} className="scroll-mt-20 mb-6">
      <h2 id={`${id}-heading`} className="text-sm font-semibold text-foreground mb-2">
        {label}
      </h2>
      {children}
    </section>
  )
}

export default function OrgSettingsPage() {
  const params = useParams<{ login: string }>()
  const orgLogin = params.login

  // Every card here is org-admin only on the API. Look up the role so a plain member gets an explanation instead of
  // cards that can only 403; if the lookup itself fails, show the cards and let the API's verdict surface.
  const membershipsQuery = useQuery<MyOrgMembership[]>({ queryKey: ["my-orgs"], queryFn: () => api.orgs.mine() })
  const role = membershipsQuery.data ? orgRoleFor(membershipsQuery.data, orgLogin) : undefined
  const notAdmin = role !== undefined && role !== "admin"

  return (
    <>
      <PageHeader title="Organization settings" description={`Scanning, alerts, automation and access settings for ${orgLogin}.`} />

      {membershipsQuery.isLoading ? (
        <div className="card px-4 py-6 flex items-center gap-2 text-sm text-muted-foreground">
          <CircleNotch className="size-3.5 animate-spin" /> Checking your access…
        </div>
      ) : notAdmin ? (
        <div className="card px-4 py-4">
          <span className="section-title">Organization settings</span>
          <p className="text-xs text-muted-foreground mt-1">
            {role === "member"
              ? `Changing ${orgLogin}'s settings is limited to organization admins. You're a member, not an admin.`
              : `Changing ${orgLogin}'s settings needs an admin membership, and you don't have one for this organization.`}
          </p>
        </div>
      ) : (
        <>
          <nav aria-label="Settings sections" className="sticky top-0 z-10 -mx-1 mb-4 flex flex-wrap gap-1.5 bg-background/90 px-1 py-2 backdrop-blur">
            {SECTIONS.map((s) => (
              <a
                key={s.id}
                href={`#${s.id}`}
                className="text-xs font-medium px-2.5 py-1 rounded-md border border-transparent text-muted-foreground hover:bg-elevated hover:text-foreground transition-colors"
              >
                {s.label}
              </a>
            ))}
          </nav>
          <Section id="access" label="Members and access">
            <ClevisMembersCard orgLogin={orgLogin} />
            <ApiTokensCard orgLogin={orgLogin} />
            <p className="text-xs text-muted-foreground">
              Inviting people and reviewing the GitHub roster is on the{" "}
              <Link href={`/settings/org/${encodeURIComponent(orgLogin)}/members`} className="text-primary hover:underline">
                Members page
              </Link>
              .
            </p>
          </Section>
          <Section id="scanning" label="Scanning">
            <HygieneScoringCard orgLogin={orgLogin} />
            <ScheduledScansCard orgLogin={orgLogin} />
            <BadgeSettingsCard orgLogin={orgLogin} />
          </Section>
          <Section id="notifications" label="Notifications and digest">
            <DigestCard orgLogin={orgLogin} />
            <NotificationDestinationsCard orgLogin={orgLogin} />
          </Section>
          <Section id="automation" label="Automation">
            <AutomationSchedulesCard orgLogin={orgLogin} />
          </Section>
          <Section id="waivers" label="Waivers">
            <WaiverRequestsCard orgLogin={orgLogin} />
            <WaiversCard orgLogin={orgLogin} />
          </Section>
          <Section id="health" label="Health and evidence">
            <IngestionHealthCard orgLogin={orgLogin} />
            <EvidenceExportCard orgLogin={orgLogin} />
          </Section>
        </>
      )}
    </>
  )
}
