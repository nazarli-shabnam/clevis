import { CHART_COLORS } from "@/lib/charts/theme"

interface HeatmapCalendarProps {
  data: number[] // weekly counts (e.g. 52 values)
  colorScale: string[] // low → high intensity buckets
}

/** GitHub-style intensity calendar. One cell per value, colored by bucket. */
export function HeatmapCalendar({ data, colorScale }: HeatmapCalendarProps) {
  const scale = colorScale.length > 0 ? colorScale : [CHART_COLORS.grid]
  const max = Math.max(1, ...data)

  function colorFor(value: number): string {
    if (value <= 0) return scale[0]
    const idx = Math.min(scale.length - 1, Math.ceil((value / max) * (scale.length - 1)))
    return scale[idx]
  }

  return (
    <div className="flex flex-wrap gap-1">
      {data.map((value, i) => (
        <div
          key={i}
          title={`${value}`}
          className="size-3 rounded-[2px]"
          style={{ backgroundColor: colorFor(value) }}
        />
      ))}
    </div>
  )
}
