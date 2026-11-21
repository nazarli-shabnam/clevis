"use client"

import Link from "next/link"
import { buttonVariants } from "@/components/ui/button"
import { cn } from "@/lib/utils"

// Rendered inside the root layout, so an unknown route keeps the sidebar and header.
export default function NotFound() {
  return (
    <div className="min-h-[60vh] flex items-center justify-center px-4">
      <div className="card max-w-md w-full px-6 py-8 flex flex-col items-center gap-3 text-center">
        <p className="text-xs text-muted-foreground/70 font-mono">404</p>
        <p className="text-sm font-medium text-foreground">Page not found</p>
        <p className="text-sm text-muted-foreground">
          This page does not exist or has moved. Use the sidebar, or go back to the Overview.
        </p>
        <Link href="/" className={cn(buttonVariants({ size: "sm", variant: "outline" }), "mt-2")}>
          Back to Overview
        </Link>
      </div>
    </div>
  )
}
