"use client"

import { IconContext } from "@phosphor-icons/react"

/**
 * Standardizes Phosphor icon rendering app-wide: one weight, currentColor fill.
 * Size is left at the Phosphor default (1em) so existing `size-*` utility classes
 * continue to control dimensions per call site.
 */
export function IconProvider({ children }: { children: React.ReactNode }) {
  return (
    <IconContext.Provider value={{ weight: "regular", color: "currentColor" }}>
      {children}
    </IconContext.Provider>
  )
}
