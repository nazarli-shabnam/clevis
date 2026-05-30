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

// Applied before paint so the persisted theme is set without a flash of the
// default. Kept in sync with apps/ui/lib/theme.ts (default + dark theme list).
const themeScript = `(function(){try{var t=localStorage.getItem('clevis:theme')||'midnight';var dark=['midnight','carbon','slate','dim'].indexOf(t)!==-1;var r=document.documentElement;r.setAttribute('data-theme',t);r.classList.toggle('dark',dark);r.classList.toggle('light',!dark);}catch(e){}})();`

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={cn("dark font-sans", inter.variable)} data-theme="midnight" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeScript }} />
      </head>
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
