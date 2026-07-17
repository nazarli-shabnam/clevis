"use client"

import { usePathname, useRouter } from "next/navigation"
import { useEffect, useRef, useState } from "react"
import Link from "next/link"
import { useQuery } from "@tanstack/react-query"
import { GearSix, Check, SignOut, UserPlus } from "@phosphor-icons/react"
import {
  Sidebar,
  SidebarContent,
  SidebarGroup,
  SidebarGroupContent,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarSeparator,
} from "@/components/ui/sidebar"
import { useAuth } from "@/lib/auth-context"
import { api } from "@/lib/api/client"
import type { MyOrgMembership } from "@/lib/api/types"

// Settings is no longer in the sidebar nav — it lives inside the profile dropdown.
const groups = [
  [
    { title: "Overview",         href: "/" },
    { title: "Activity",         href: "/activity" },
    { title: "Pull Requests",    href: "/pulls" },
    { title: "Releases",         href: "/releases" },
  ],
  [
    { title: "Repositories",     href: "/repos" },
    { title: "Health & Security",href: "/security" },
  ],
  [
    { title: "Collaborators",    href: "/collaborators" },
    { title: "Automation",       href: "/automation" },
    { title: "Audit Log",        href: "/audit" },
    { title: "Job Queue",        href: "/jobs" },
  ],
  [
    { title: "My PRs",     href: "/my/prs" },
    { title: "My Reviews", href: "/my/reviews" },
    { title: "My Issues",  href: "/my/issues" },
  ],
]

interface Profile {
  name: string
  org: string
  email: string
}

function ProfileDropdown({
  profile,
  inviteHref,
  onClose,
  onSignOut,
}: {
  profile: Profile
  inviteHref: string
  onClose: () => void
  onSignOut: () => void
}) {
  const initials = profile.name.charAt(0).toUpperCase()

  return (
    <div
      className="absolute top-full left-0 right-0 z-50 border-b border-sidebar-border bg-sidebar shadow-2xl"
      // prevent clicks inside from bubbling to the click-away handler
      onClick={(e) => e.stopPropagation()}
    >
      {/* Email */}
      <div className="px-3.5 py-2.5 border-b border-sidebar-border/60">
        <p className="text-[0.75rem] text-sidebar-foreground/50 truncate">
          {profile.email || "no email set"}
        </p>
      </div>

      {/* Workspace row */}
      <div className="p-1.5">
        <div className="flex items-center gap-2.5 px-2 py-2">
          <div className="size-7 rounded-none bg-primary/15 border border-primary/20 flex items-center justify-center shrink-0">
            <span className="text-[0.6875rem] font-semibold text-primary leading-none">
              {initials}
            </span>
          </div>
          <div className="min-w-0 flex-1">
            <p className="text-[0.8125rem] font-medium text-sidebar-foreground leading-none truncate">
              {profile.name}
            </p>
            <p className="text-[0.6875rem] text-sidebar-foreground/40 mt-0.5 leading-none">
              {profile.org || "Members"}
            </p>
          </div>
          <Check className="size-3.5 text-primary shrink-0" />
        </div>
      </div>

      {/* Settings + Invite members buttons */}
      <div className="px-1.5 pb-1.5 flex gap-1.5">
        <Link
          href="/settings"
          onClick={onClose}
          className="flex items-center gap-1.5 px-2.5 py-1.5 text-[0.75rem] font-medium text-sidebar-foreground/70 hover:text-sidebar-foreground bg-sidebar-accent/60 hover:bg-sidebar-accent border border-sidebar-border/60 transition-colors flex-1 justify-center"
        >
          <GearSix className="size-3" />
          Settings
        </Link>
        <Link
          href={inviteHref}
          onClick={onClose}
          title={inviteHref === "/settings" ? "Set a default organization in Settings first" : undefined}
          className="flex items-center gap-1.5 px-2.5 py-1.5 text-[0.75rem] font-medium text-sidebar-foreground/70 hover:text-sidebar-foreground bg-sidebar-accent/60 hover:bg-sidebar-accent border border-sidebar-border/60 transition-colors flex-1 justify-center"
        >
          <UserPlus className="size-3" />
          Invite members
        </Link>
      </div>

      {/* Sign out */}
      <div className="border-t border-sidebar-border/60 p-1.5">
        <button
          onClick={onSignOut}
          className="flex w-full items-center gap-2 px-2.5 py-1.5 text-[0.8125rem] text-destructive/70 hover:text-destructive hover:bg-sidebar-accent/60 transition-colors"
        >
          <SignOut className="size-3.5" />
          Sign out
        </button>
      </div>
    </div>
  )
}

export function AppSidebar() {
  const pathname = usePathname()
  const router = useRouter()
  const { user, logout } = useAuth()
  const [open, setOpen] = useState(false)
  const containerRef = useRef<HTMLDivElement>(null)

  const defaultOrg = typeof window !== "undefined" ? (localStorage.getItem("default_org") || "") : ""

  // Same query key as the /collaborators redirect page so TanStack Query dedupes
  // the request when both are mounted. Only route the "Invite members" link at an
  // org where the user is actually an admin — mirrors the /collaborators fallback
  // logic (prefer default_org, else the first admin org, else /settings).
  const { data: memberships = [], isLoading: membershipsLoading } = useQuery<MyOrgMembership[]>({
    queryKey: ["my-orgs"],
    queryFn: () => api.orgs.mine(),
  })

  // Profile org display reflects real org membership data, not just whatever the user
  // once typed into the localStorage-backed "default org" field in Settings.
  const displayOrg = memberships.find((m) => m.org_login === defaultOrg)?.org_login || memberships[0]?.org_login || ""
  const profile: Profile = {
    name: user?.name || user?.email || "Guest",
    org: displayOrg || "no organization connected",
    email: user?.email || "",
  }

  const adminOrgs = memberships.filter((m) => m.role === "admin")
  const inviteTarget = membershipsLoading
    ? undefined
    : adminOrgs.find((m) => m.org_login === defaultOrg) || adminOrgs[0]
  const inviteHref = inviteTarget
    ? `/settings/org/${encodeURIComponent(inviteTarget.org_login)}/members`
    : "/settings"

  // Close on click outside
  useEffect(() => {
    if (!open) return
    function handleClickOutside(e: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false)
      }
    }
    document.addEventListener("mousedown", handleClickOutside)
    return () => document.removeEventListener("mousedown", handleClickOutside)
  }, [open])

  const initials = profile.name.charAt(0).toUpperCase()

  function isActive(href: string) {
    if (href === "/") return pathname === "/"
    return pathname === href || pathname.startsWith(href + "/")
  }

  return (
    <Sidebar>
      {/* Profile widget — opens dropdown */}
      <SidebarHeader className="border-b border-sidebar-border p-0 relative" ref={containerRef}>
        <button
          onClick={() => setOpen((v) => !v)}
          className="flex w-full items-center gap-2.5 px-3.5 py-3 hover:bg-sidebar-accent/60 transition-colors group text-left"
        >
          <div className="size-7 rounded-none bg-primary/15 border border-primary/25 flex items-center justify-center shrink-0">
            <span className="text-[0.6875rem] font-semibold text-primary leading-none">{initials}</span>
          </div>
          <div className="min-w-0 flex-1">
            <p className="text-[0.8125rem] font-medium text-sidebar-foreground leading-none truncate">
              {profile.name}
            </p>
            <p className="text-[0.6875rem] text-sidebar-foreground/40 mt-0.5 leading-none truncate">
              {profile.org}
            </p>
          </div>
          <GearSix className="size-3 text-sidebar-foreground/20 group-hover:text-sidebar-foreground/50 transition-colors shrink-0" />
        </button>

        {open && (
          <ProfileDropdown
            profile={profile}
            inviteHref={inviteHref}
            onClose={() => setOpen(false)}
            onSignOut={() => { logout(); setOpen(false); router.replace("/login") }}
          />
        )}
      </SidebarHeader>

      <SidebarContent>
        {groups.map((items, groupIndex) => (
          <div key={groupIndex}>
            {groupIndex > 0 && <SidebarSeparator className="my-1 bg-sidebar-border/60" />}
            <SidebarGroup className="py-1">
              <SidebarGroupContent>
                <SidebarMenu>
                  {items.map((item) => {
                    const active = isActive(item.href)
                    return (
                      <SidebarMenuItem key={item.title}>
                        <SidebarMenuButton
                          isActive={active}
                          className={[
                            "flex items-center rounded-none px-3 py-1.5 text-[0.8125rem]",
                            active
                              ? "bg-sidebar-accent text-sidebar-foreground font-medium"
                              : "text-sidebar-foreground/50 hover:text-sidebar-foreground hover:bg-sidebar-accent/60",
                          ].join(" ")}
                          render={<Link href={item.href} />}
                        >
                          <span>{item.title}</span>
                        </SidebarMenuButton>
                      </SidebarMenuItem>
                    )
                  })}
                </SidebarMenu>
              </SidebarGroupContent>
            </SidebarGroup>
          </div>
        ))}
      </SidebarContent>
    </Sidebar>
  )
}
