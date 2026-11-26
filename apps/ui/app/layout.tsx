import "@/app/globals.css"
import { Geist, JetBrains_Mono } from "next/font/google"
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

// Code, IDs, cache keys, timestamps. Headings reuse Geist (--font-heading aliases --font-sans in globals.css).
const jetbrainsMono = JetBrains_Mono({
  subsets: ["latin"],
  variable: "--font-jetbrains-mono",
})

export const metadata = {
  title: "Overview · clevis",
  description: "GitHub analytics and cache management",
}

// Applied before paint to avoid a theme flash, and re-applied when the OS scheme changes while the
// preference is "system". Keep in sync with normalizeTheme/resolveTheme in lib/theme.ts.
const themeScript = `(function(){var m=window.matchMedia('(prefers-color-scheme: dark)'),r=document.documentElement;function pref(){try{var t=localStorage.getItem('clevis:theme');return t==='light'||t==='system'||t==='dark'?t:(t==='paper'||t==='ash'?'light':'dark')}catch(e){return'dark'}}function paint(){var p=pref(),d=p==='system'?m.matches:p==='dark';r.setAttribute('data-theme',d?'dark':'light');r.classList.toggle('dark',d);r.classList.toggle('light',!d)}paint();m.addEventListener('change',function(){if(pref()==='system')paint()})})();`

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html
      lang="en"
      className={cn(
        "dark font-sans",
        geist.variable,
        jetbrainsMono.variable
      )}
      data-theme="dark"
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
