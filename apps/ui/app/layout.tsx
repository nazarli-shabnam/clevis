import "@/app/globals.css"
import { Geist, Geist_Mono } from "next/font/google"
import { cn } from "@/lib/utils"
import { SidebarProvider, SidebarInset, SidebarTrigger } from "@/components/ui/sidebar"
import { TooltipProvider } from "@/components/ui/tooltip"
import { AppSidebar } from "@/components/app-sidebar"
import { QueryProvider } from "@/components/query-provider"

const geist = Geist({ subsets: ["latin"], variable: "--font-sans" })
const geistMono = Geist_Mono({ subsets: ["latin"], variable: "--font-mono" })

export const metadata = {
  title: "clevis",
  description: "GitHub security analytics and cache management",
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={cn("dark font-sans", geist.variable, geistMono.variable)}>
      <body className="min-h-screen bg-background">
        <QueryProvider>
          <TooltipProvider>
            <SidebarProvider>
              <AppSidebar />
              <SidebarInset>
                <header className="flex h-10 shrink-0 items-center gap-2 border-b border-border px-4">
                  <SidebarTrigger className="size-7 text-muted-foreground hover:text-foreground" />
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
