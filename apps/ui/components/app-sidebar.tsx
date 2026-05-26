"use client"

import { usePathname } from "next/navigation"
import { useEffect, useRef, useState } from "react"
import Link from "next/link"
import { Settings, Check, LogOut, UserPlus } from "lucide-react"
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

// Settings is no longer in the sidebar nav — it lives inside the profile dropdown.
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
  ],
  [
    { title: "My PRs",     href: "/my/prs",     shortcut: undefined },
    { title: "My Reviews", href: "/my/reviews", shortcut: undefined },
    { title: "My Issues",  href: "/my/issues",  shortcut: undefined },
  ],
]

interface Profile {
  name: string
  org: string
  email: string
}

function ProfileDropdown({
  profile,
  onClose,
}: {
  profile: Profile
  onClose: () => void
}) {
  const initials = profile.name.charAt(0).toUpperCase()

  function signOut() {
    localStorage.removeItem("profile_name")
    localStorage.removeItem("profile_email")
    localStorage.removeItem("default_org")
    window.location.reload()
  }

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
        <div className="flex items-center gap-2.5 px-2 py-2 rounded-sm">
          <div className="size-7 rounded-sm bg-primary/15 border border-primary/20 flex items-center justify-center shrink-0">
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
          <Settings className="size-3" />
          Settings
        </Link>
        <button
          disabled
          className="flex items-center gap-1.5 px-2.5 py-1.5 text-[0.75rem] font-medium text-sidebar-foreground/70 hover:text-sidebar-foreground bg-sidebar-accent/60 hover:bg-sidebar-accent border border-sidebar-border/60 transition-colors flex-1 justify-center disabled:opacity-50 disabled:cursor-not-allowed"
          title="Coming soon"
        >
          <UserPlus className="size-3" />
          Invite members
        </button>
      </div>

      {/* Sign out */}
      <div className="border-t border-sidebar-border/60 p-1.5">
        <button
          onClick={signOut}
          className="flex w-full items-center gap-2 px-2.5 py-1.5 text-[0.8125rem] text-destructive/70 hover:text-destructive hover:bg-sidebar-accent/60 transition-colors"
        >
          <LogOut className="size-3.5" />
          Sign out
        </button>
      </div>
    </div>
  )
}

export function AppSidebar() {
  const pathname = usePathname()
  const [profile, setProfile] = useState<Profile>({ name: "Guest", org: "no org connected", email: "" })
  const [open, setOpen] = useState(false)
  const containerRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const name  = localStorage.getItem("profile_name")  || "Guest"
    const org   = localStorage.getItem("default_org")   || "no org connected"
    const email = localStorage.getItem("profile_email") || ""
    setProfile({ name, org, email })
  }, [])

  // Listen for profile updates from settings page
  useEffect(() => {
    function handleUpdate() {
      const name  = localStorage.getItem("profile_name")  || "Guest"
      const org   = localStorage.getItem("default_org")   || "no org connected"
      const email = localStorage.getItem("profile_email") || ""
      setProfile({ name, org, email })
    }
    window.addEventListener("profile-updated", handleUpdate)
    return () => window.removeEventListener("profile-updated", handleUpdate)
  }, [])

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
        </button>

        {open && (
          <ProfileDropdown profile={profile} onClose={() => setOpen(false)} />
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
