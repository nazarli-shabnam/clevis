import "@/app/globals.css"
import { Inter } from "next/font/google"
import { cn } from "@/lib/utils"
import { TooltipProvider } from "@/components/ui/tooltip"
import { QueryProvider } from "@/components/query-provider"
import { AuthProvider } from "@/lib/auth-context"
import { AuthGuard } from "@/components/auth-guard"
import { ShellRouter } from "@/components/shell-router"

const inter = Inter({
  subsets: ["latin"],
  weight: ["300", "400", "500", "600", "700"],
  variable: "--font-sans",
})

export const metadata = {
  title: "Overview · clevis",
  description: "GitHub analytics and cache management",
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={cn("dark font-sans", inter.variable)}>
      <body className="min-h-screen bg-background">
        <QueryProvider>
          <TooltipProvider>
            <AuthProvider>
              <AuthGuard>
                <ShellRouter>{children}</ShellRouter>
              </AuthGuard>
            </AuthProvider>
          </TooltipProvider>
        </QueryProvider>
      </body>
    </html>
  )
}
