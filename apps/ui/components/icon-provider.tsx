"use client"

import { IconContext } from "@phosphor-icons/react"

/**
 * Standardizes Phosphor icons app-wide: one weight, currentColor. Size stays at the 1em
 * default so `size-*` classes keep controlling dimensions.
 */
export function IconProvider({ children }: { children: React.ReactNode }) {
  return (
    <IconContext.Provider value={{ weight: "regular", color: "currentColor" }}>
      {children}
    </IconContext.Provider>
  )
}
