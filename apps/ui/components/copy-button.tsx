"use client"

import { useEffect, useState } from "react"
import { Button } from "@/components/ui/button"

/** Copies `value` to the clipboard and says whether it worked ("Copied" / "Copy failed"), then
 * reverts to "Copy". `ariaLabel` names the button for assistive tech (the visible text alone is
 * just "Copy"). */
export function CopyButton({ value, ariaLabel }: { value: string; ariaLabel: string }) {
  const [state, setState] = useState<"idle" | "copied" | "failed">("idle")
  // Reset after a moment; keyed on state so rapid clicks restart one timer and unmount clears it.
  useEffect(() => {
    if (state === "idle") return
    const t = setTimeout(() => setState("idle"), 2000)
    return () => clearTimeout(t)
  }, [state])

  async function copy() {
    try {
      await navigator.clipboard.writeText(value)
      setState("copied")
    } catch {
      setState("failed")
    }
  }

  return (
    <>
      <Button size="sm" variant="outline" onClick={copy} aria-label={ariaLabel}>
        {state === "copied" ? "Copied" : state === "failed" ? "Copy failed" : "Copy"}
      </Button>
      {/* The button's own text changing isn't announced; this is. */}
      <span role="status" className="sr-only">
        {state === "copied" ? "Copied to clipboard" : state === "failed" ? "Could not copy to the clipboard" : ""}
      </span>
    </>
  )
}
