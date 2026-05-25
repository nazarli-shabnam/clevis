import "@/app/globals.css"
import { IBM_Plex_Mono, IBM_Plex_Sans } from "next/font/google"
import { cn } from "@/lib/utils"
import { SidebarProvider, SidebarInset, SidebarTrigger } from "@/components/ui/sidebar"
import { TooltipProvider } from "@/components/ui/tooltip"
import { AppSidebar } from "@/components/app-sidebar"
import { QueryProvider } from "@/components/query-provider"
import { Breadcrumb } from "@/components/breadcrumb"

const plexSans = IBM_Plex_Sans({
  subsets: ["latin"],
  weight: ["400", "500", "600"],
  variable: "--font-sans",
})

const plexMono = IBM_Plex_Mono({
  subsets: ["latin"],
  weight: ["400", "500"],
  variable: "--font-mono",
})

export const metadata = {
  title: "clevis",
  description: "GitHub analytics and cache management",
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={cn("dark font-sans", plexSans.variable, plexMono.variable)}>
      <body className="min-h-screen bg-background">
        <QueryProvider>
          <TooltipProvider>
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
          </TooltipProvider>
        </QueryProvider>
      </body>
    </html>
  )
}
