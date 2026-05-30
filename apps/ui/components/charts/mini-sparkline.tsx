"use client"

import { LineChart, Line, ResponsiveContainer } from "recharts"
import { CHART_COLORS } from "@/lib/charts/theme"

interface MiniSparklineProps {
  data: number[]
  color?: string
  height?: number
}

/** Inline sparkline — no axes, grid, or tooltip. For stat cards and table rows. */
export function MiniSparkline({ data, color = CHART_COLORS.primary, height = 32 }: MiniSparklineProps) {
  const chartData = data.map((value, i) => ({ i, value }))
  return (
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
  )
}
