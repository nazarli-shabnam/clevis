"use client"

import { useEffect } from "react"

export interface NavSection {
  id: string
  label: string
}

/** A sticky row of in-page links, one per section; the ids are the anchors (`/settings#sessions`). */
export function SectionNav({ sections, label = "Settings sections" }: { sections: readonly NavSection[]; label?: string }) {
  // The anchors are in the first render, but the cards above them fill in later; scroll to the one in the URL
  // once the page is up, because the browser's own jump on load ran before there was anything to land on.
  useEffect(() => {
    const id = decodeURIComponent(window.location.hash.slice(1))
    if (id && sections.some((s) => s.id === id)) document.getElementById(id)?.scrollIntoView?.()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

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

/** The target of one nav link: the section's cards, with room above them for the sticky nav. */
export function SectionAnchor({ id, label, children }: { id: string; label: string; children: React.ReactNode }) {
  return (
    <section id={id} aria-label={label} className="scroll-mt-20 flex flex-col gap-4">
      {children}
    </section>
  )
}
