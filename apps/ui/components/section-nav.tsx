"use client"

import { useEffect } from "react"

export interface NavSection {
  id: string
  label: string
}

/** The section id named by the address bar's #fragment, or null (none, malformed, or not one of these). */
function hashTarget(ids: readonly string[]): string | null {
  let id: string
  try {
    id = decodeURIComponent(window.location.hash.slice(1))
  } catch {
    return null // a hand-edited or truncated escape such as #%E0%A4
  }
  return ids.includes(id) ? id : null
}

// How long a deep link keeps following the section while the cards above it load and grow.
const SETTLE_MS = 4000

/** A sticky row of in-page links, one per section; the ids are the anchors (`/settings#sessions`). */
export function SectionNav({ sections, label = "Settings sections" }: { sections: readonly NavSection[]; label?: string }) {
  const idsKey = sections.map((s) => s.id).join(",")

  // The anchors are in the first render, but the cards above them fill in later, and the browser's own jump on load
  // ran before there was anything to land on. So scroll to the section named in the URL, then keep it in view
  // while the page's height changes, until it has settled or the person scrolls themselves.
  useEffect(() => {
    const id = hashTarget(idsKey.split(","))
    const el = id ? document.getElementById(id) : null
    if (!el) return
    let following = true
    const scroll = () => {
      if (following) el.scrollIntoView?.()
    }
    scroll()
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(scroll)
    observer?.observe(document.body)
    const stop = () => {
      following = false
      observer?.disconnect()
    }
    const timer = window.setTimeout(stop, SETTLE_MS)
    const takeOver = ["wheel", "touchstart", "keydown", "pointerdown"] as const
    for (const type of takeOver) window.addEventListener(type, stop, { passive: true })
    return () => {
      window.clearTimeout(timer)
      for (const type of takeOver) window.removeEventListener(type, stop)
      stop()
    }
  }, [idsKey])

  return (
    <nav aria-label={label} className="sticky top-0 z-10 -mx-1 mb-4 flex flex-wrap gap-1.5 bg-background/90 px-1 py-2 backdrop-blur">
      {sections.map((s) => (
        <a
          key={s.id}
          href={`#${s.id}`}
          className="text-xs font-medium px-2.5 py-1 rounded-md border border-transparent text-muted-foreground hover:bg-elevated hover:text-foreground transition-colors"
        >
          {s.label}
        </a>
      ))}
    </nav>
  )
}

/** The target of one nav link: the section's cards (which carry their own headings), with room above them for the
 * sticky nav, which wraps to several rows on a narrow screen. */
export function SectionAnchor({ id, children }: { id: string; children: React.ReactNode }) {
  return (
    <div id={id} className="scroll-mt-32 sm:scroll-mt-20 flex flex-col gap-4">
      {children}
    </div>
  )
}
