import "@/app/globals.css"
import { Geist, Archivo, JetBrains_Mono } from "next/font/google"
import { cn } from "@/lib/utils"
import { TooltipProvider } from "@/components/ui/tooltip"
import { Toaster } from "@/components/ui/toast"
import { QueryProvider } from "@/components/query-provider"
import { QueryAuthSync } from "@/components/query-auth-sync"
import { AuthProvider } from "@/lib/auth-context"
import { AuthGuard } from "@/components/auth-guard"
import { ShellRouter } from "@/components/shell-router"
import { IconProvider } from "@/components/icon-provider"

// Body / UI
const geist = Geist({
  subsets: ["latin"],
  variable: "--font-sans",
})

// Display headers
const archivo = Archivo({
  subsets: ["latin"],
  weight: ["500", "600", "700", "800", "900"],
  variable: "--font-heading",
})

// Data / telemetry: stat values, IDs, timestamps, code.
const jetbrainsMono = JetBrains_Mono({
  subsets: ["latin"],
  variable: "--font-jetbrains-mono",
})

export const metadata = {
  title: "Overview · clevis",
  description: "GitHub analytics and cache management",
}

// Applied before paint to avoid a theme flash. Keep in sync with lib/theme.ts (default + dark theme list).
const themeScript = `(function(){try{var t=localStorage.getItem('clevis:theme')||'midnight';var dark=['midnight','carbon','slate','dim'].indexOf(t)!==-1;var r=document.documentElement;r.setAttribute('data-theme',t);r.classList.toggle('dark',dark);r.classList.toggle('light',!dark);}catch(e){}})();`

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html
      lang="en"
      className={cn(
        "dark font-sans",
        geist.variable,
        archivo.variable,
        jetbrainsMono.variable
      )}
      data-theme="midnight"
      suppressHydrationWarning
    >
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeScript }} />
      </head>
      <body className="min-h-screen bg-background">
        <QueryProvider>
          <TooltipProvider>
            <IconProvider>
              <AuthProvider>
                <QueryAuthSync />
                <AuthGuard>
                  <ShellRouter>{children}</ShellRouter>
                  <Toaster />
                </AuthGuard>
              </AuthProvider>
            </IconProvider>
          </TooltipProvider>
        </QueryProvider>
      </body>
    </html>
  )
}
