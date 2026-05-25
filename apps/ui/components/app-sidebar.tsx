"use client"

import { usePathname } from "next/navigation"
import { useEffect, useState } from "react"
import Link from "next/link"
import { Settings } from "lucide-react"
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

// Each group is an array of nav items. Groups are separated by a thin line — no labels.
const groups = [
  [
    { title: "Overview",         href: "/",             shortcut: "g o" },
    { title: "Activity",         href: "/activity",     shortcut: "g a" },
    { title: "Pull Requests",    href: "/pulls",        shortcut: "g p" },
    { title: "Releases",         href: "/releases",     shortcut: "g r" },
  ],
  [
    { title: "Repositories",     href: "/repos",        shortcut: "g R" },
    { title: "Health & Security",href: "/security",     shortcut: "g s" },
  ],
  [
    { title: "Collaborators",    href: "/collaborators", shortcut: undefined },
    { title: "Automation",       href: "/automation",    shortcut: undefined },
    { title: "Audit Log",        href: "/audit",         shortcut: undefined },
    { title: "Job Queue",        href: "/jobs",          shortcut: undefined },
    { title: "Settings",         href: "/settings",      shortcut: undefined },
  ],
  [
    { title: "My PRs",     href: "/my/prs",     shortcut: undefined },
    { title: "My Reviews", href: "/my/reviews", shortcut: undefined },
    { title: "My Issues",  href: "/my/issues",  shortcut: undefined },
  ],
]

export function AppSidebar() {
  const pathname = usePathname()
  const [profile, setProfile] = useState({ name: "Guest", org: "no org connected" })

  useEffect(() => {
    const name = localStorage.getItem("profile_name") || "Guest"
    const org  = localStorage.getItem("default_org")  || "no org connected"
    setProfile({ name, org })
  }, [])

  const initials = profile.name.charAt(0).toUpperCase()

  function isActive(href: string) {
    if (href === "/") return pathname === "/"
    return pathname === href || pathname.startsWith(href + "/")
  }

  return (
    <Sidebar>
      {/* Profile widget — links to /settings */}
      <SidebarHeader className="border-b border-sidebar-border p-0">
        <Link
          href="/settings"
          className="flex items-center gap-2.5 px-3.5 py-3 hover:bg-sidebar-accent/60 transition-colors group"
        >
          <div className="size-7 rounded-full bg-primary/15 border border-primary/25 flex items-center justify-center shrink-0">
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
          <Settings className="size-3 text-sidebar-foreground/20 group-hover:text-sidebar-foreground/50 transition-colors shrink-0" />
        </Link>
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
                            "group/item relative flex items-center justify-between rounded-none px-3 py-1.5 text-[0.8125rem]",
                            active
                              ? "bg-sidebar-accent text-sidebar-foreground font-medium"
                              : "text-sidebar-foreground/50 hover:text-sidebar-foreground hover:bg-sidebar-accent/60",
                          ].join(" ")}
                          render={<Link href={item.href} />}
                        >
                          <span>{item.title}</span>
                          {item.shortcut && (
                            <span className="font-mono text-[0.625rem] text-sidebar-foreground/20 opacity-0 group-hover/item:opacity-100 transition-opacity">
                              {item.shortcut}
                            </span>
                          )}
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
