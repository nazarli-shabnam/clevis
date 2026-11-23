"use client"

import { LineChart, Line, ResponsiveContainer } from "recharts"
import { CHART_COLORS } from "@/lib/charts/theme"
import { ChartA11y } from "./chart-a11y"

interface MiniSparklineProps {
  data: number[]
  color?: string
  height?: number
}

/** Inline sparkline — no axes, grid, or tooltip. For stat cards and table rows. */
export function MiniSparkline({ data, color = CHART_COLORS.primary, height = 32 }: MiniSparklineProps) {
  const chartData = data.map((value, i) => ({ i, value }))
  const summary =
    data.length === 0
      ? "Trend: no data."
      : `Trend over ${data.length} points: from ${data[0]} to ${data[data.length - 1]}, low ${Math.min(...data)}, high ${Math.max(...data)}.`
  return (
    <ChartA11y summary={summary}>
    <ResponsiveContainer width="100%" height={height}>
      <LineChart data={chartData} margin={{ top: 2, right: 2, bottom: 2, left: 2 }}>
        <Line
          type="monotone"
          dataKey="value"
          stroke={color}
          strokeWidth={1.5}
          dot={false}
          isAnimationActive={false}
        />
      </LineChart>
    </ResponsiveContainer>
    </ChartA11y>
  )
}
