import type { ReactNode } from "react"

/** Gives a chart a text alternative: the graphic is exposed as one image named by `summary`. */
export function ChartA11y({ summary, children }: { summary: string; children: ReactNode }) {
  return (
    <div role="img" aria-label={summary}>
      {children}
    </div>
  )
}

/** "label: N points, from A to B, peak P at X." for a single series; the numbers a sighted reader takes from the shape. */
export function describeSeries(label: string, points: { name: string; value: number }[]): string {
  if (points.length === 0) return `${label}: no data`
  const peak = points.reduce((best, p) => (p.value > best.value ? p : best), points[0])
  const first = points[0]
  const last = points[points.length - 1]
  return `${label}: ${points.length} points, from ${first.value} (${first.name}) to ${last.value} (${last.name}), peak ${peak.value} (${peak.name}).`
}
