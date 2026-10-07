"use client"

import { usePathname } from "next/navigation"
import { SidebarProvider, SidebarInset, SidebarTrigger } from "@/components/ui/sidebar"
import { AppSidebar } from "@/components/app-sidebar"
import { Breadcrumb } from "@/components/breadcrumb"
import { NotificationBell } from "@/components/notification-bell"
import { CommandPalette } from "@/components/command-palette"
import { isPublicRoute } from "@/lib/public-routes"

export function ShellRouter({ children }: { children: React.ReactNode }) {
  const pathname = usePathname()

  if (isPublicRoute(pathname)) {
    return <>{children}</>
  }

  return (
    <SidebarProvider>
      <AppSidebar />
      <SidebarInset>
        <header className="flex h-10 shrink-0 items-center justify-between border-b border-border/60 px-4">
          <div className="flex items-center gap-3">
            <SidebarTrigger className="size-6 text-muted-foreground hover:text-foreground" />
            <Breadcrumb />
          </div>
          <div className="flex items-center gap-1">
            <NotificationBell />
            <CommandPalette />
          </div>
        </header>
        {/* SidebarInset already renders the page's <main>; a second one would nest landmarks. */}
        <div className="flex-1 p-5">
          {children}
        </div>
      </SidebarInset>
    </SidebarProvider>
  )
}
