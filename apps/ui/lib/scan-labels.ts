const day = (d: Date) => d.toLocaleDateString(undefined, { month: "short", day: "numeric" })
const time = (d: Date) => d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" })

/**
 * Axis/tooltip labels for a series of scans, oldest first: the day ("Nov 27"), plus the time when another scan
 * shares that day (so no two points read the same). Falls back to "Scan N" when the dates are not there or do not
 * line up with the points (an older API, or a row without a timestamp).
 */
export function scanLabels(count: number, dates: readonly string[] | undefined): string[] {
  const parsed = dates?.length === count ? dates.map((d) => new Date(d)) : null
  if (!parsed || parsed.some((d) => Number.isNaN(d.getTime()))) return Array.from({ length: count }, (_, i) => `Scan ${i + 1}`)
  const perDay = new Map<string, number>()
  for (const d of parsed) perDay.set(day(d), (perDay.get(day(d)) ?? 0) + 1)
  return parsed.map((d) => ((perDay.get(day(d)) ?? 0) > 1 ? `${day(d)} ${time(d)}` : day(d)))
}
