"use client"

import { usePathname } from "next/navigation"
import { SidebarProvider, SidebarInset, SidebarTrigger } from "@/components/ui/sidebar"
import { AppSidebar } from "@/components/app-sidebar"
import { Breadcrumb } from "@/components/breadcrumb"

// Routes rendered without the sidebar shell
const SHELL_EXCLUDED = ["/login", "/setup", "/register"]

export function ShellRouter({ children }: { children: React.ReactNode }) {
  const pathname = usePathname()

  if (SHELL_EXCLUDED.includes(pathname)) {
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
        </header>
        <main className="flex-1 p-5">
          {children}
        </main>
      </SidebarInset>
    </SidebarProvider>
  )
}
