import { PageHeader } from "@/components/page-header"
import { StatCard } from "@/components/stat-card"
import { FolderGit2, GitPullRequest, ShieldCheck, Users, ArrowRight, Radio } from "lucide-react"

const stats = [
  { label: "Repositories", value: "—", icon: FolderGit2 },
  { label: "Open PRs", value: "—", icon: GitPullRequest },
  { label: "Security Score", value: "—", icon: ShieldCheck },
  { label: "Team Members", value: "—", icon: Users },
]

const quickActions = [
  { label: "Run Security Scan", href: "/security" },
  { label: "Manage Caches", href: "/repos" },
  { label: "View Collaborators", href: "/collaborators" },
]

export default function OverviewPage() {
  return (
    <>
      <PageHeader title="Overview" description="Your GitHub organization at a glance." />

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 mb-5">
        {stats.map((s) => (
          <StatCard key={s.label} label={s.label} value={s.value} icon={s.icon} />
        ))}
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        <div className="lg:col-span-2 bg-card border border-border rounded-lg">
          <div className="flex items-center gap-2 px-4 py-3 border-b border-border">
            <Radio className="size-3.5 text-muted-foreground" />
            <span className="section-title">Recent Activity</span>
          </div>
          <div className="flex flex-col items-center justify-center py-16 px-4 text-center">
            <div className="p-3 bg-muted/60 rounded-lg mb-4">
              <Radio className="size-5 text-muted-foreground" />
            </div>
            <p className="text-sm font-medium text-foreground mb-1">No activity yet</p>
            <p className="text-sm text-muted-foreground max-w-xs leading-relaxed">
              Connect a GitHub account to see your organization&apos;s event feed here.
            </p>
          </div>
        </div>

        <div className="bg-card border border-border rounded-lg">
          <div className="px-4 py-3 border-b border-border">
            <span className="section-title">Quick Actions</span>
          </div>
          <div className="p-2">
            {quickActions.map((action) => (
              <a
                key={action.href}
                href={action.href}
                className="flex items-center justify-between px-3 py-2.5 rounded text-sm text-muted-foreground hover:text-foreground hover:bg-muted/60 transition-colors group"
              >
                {action.label}
                <ArrowRight className="size-3.5 shrink-0 opacity-0 group-hover:opacity-100 transition-opacity" />
              </a>
            ))}
          </div>
        </div>
      </div>
    </>
  )
}
