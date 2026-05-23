"use client"

import { usePathname } from "next/navigation"
import {
  LayoutDashboard,
  Activity,
  FolderGit2,
  ShieldCheck,
  Users,
  Zap,
  GitBranch,
} from "lucide-react"
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
} from "@/components/ui/sidebar"

const analyticsItems = [
  { title: "Overview", href: "/", icon: LayoutDashboard },
  { title: "Activity", href: "/activity", icon: Activity },
  { title: "Repositories", href: "/repos", icon: FolderGit2 },
  { title: "Health & Security", href: "/security", icon: ShieldCheck },
]

const managementItems = [
  { title: "Collaborators", href: "/collaborators", icon: Users },
  { title: "Automation", href: "/automation", icon: Zap },
]

export function AppSidebar() {
  const pathname = usePathname()

  function isActive(href: string) {
    if (href === "/") return pathname === "/"
    return pathname === href || pathname.startsWith(href + "/")
  }

  function NavGroup({ label, items }: { label: string; items: typeof analyticsItems }) {
    return (
      <SidebarGroup>
        <SidebarGroupLabel className="text-[0.6875rem] font-medium text-sidebar-foreground/30 uppercase tracking-widest px-3 pt-4 pb-1">
          {label}
        </SidebarGroupLabel>
        <SidebarGroupContent>
          <SidebarMenu>
            {items.map((item) => {
              const active = isActive(item.href)
              return (
                <SidebarMenuItem key={item.title}>
                  <SidebarMenuButton
                    isActive={active}
                    className={
                      active
                        ? "relative bg-sidebar-accent text-sidebar-primary before:absolute before:left-0 before:top-1/2 before:-translate-y-1/2 before:h-4 before:w-0.5 before:rounded-full before:bg-sidebar-primary"
                        : "text-sidebar-foreground/45 hover:text-sidebar-foreground hover:bg-sidebar-accent/50"
                    }
                    render={<a href={item.href} />}
                  >
                    <item.icon className="size-4 shrink-0" />
                    <span className="text-sm">{item.title}</span>
                  </SidebarMenuButton>
                </SidebarMenuItem>
              )
            })}
          </SidebarMenu>
        </SidebarGroupContent>
      </SidebarGroup>
    )
  }

  return (
    <Sidebar>
      <SidebarHeader className="border-b border-sidebar-border px-4 py-3.5">
        <div className="flex items-center gap-2">
          <div className="size-5 rounded bg-sidebar-primary/20 flex items-center justify-center">
            <div className="size-2 rounded-full bg-sidebar-primary" />
          </div>
          <span className="text-sm font-semibold text-sidebar-foreground tracking-tight">
            clevis
          </span>
        </div>
      </SidebarHeader>

      <SidebarContent>
        <NavGroup label="Analytics" items={analyticsItems} />
        <NavGroup label="Management" items={managementItems} />
      </SidebarContent>

      <SidebarFooter className="border-t border-sidebar-border px-4 py-3">
        <div className="flex items-center gap-2">
          <GitBranch className="size-3.5 text-sidebar-foreground/30 shrink-0" />
          <p className="text-xs text-sidebar-foreground/30 truncate">No organization connected</p>
        </div>
      </SidebarFooter>
    </Sidebar>
  )
}
