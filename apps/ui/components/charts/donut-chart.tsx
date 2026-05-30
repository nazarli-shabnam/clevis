"use client"

import { PieChart, Pie, Cell, Tooltip, ResponsiveContainer } from "recharts"
import { CHART_COLORS, CHART_TOOLTIP_STYLE } from "@/lib/charts/theme"

interface DonutChartProps {
  data: { name: string; value: number; color: string }[]
  label: string
  height?: number
}

/** Thin donut with a centered total + label (security summary, language split). */
export function DonutChart({ data, label, height = 200 }: DonutChartProps) {
  const total = data.reduce((sum, d) => sum + d.value, 0)
  return (
    <div className="relative" style={{ height }}>
      <ResponsiveContainer width="100%" height="100%">
        <PieChart>
          <Pie
            data={data}
            dataKey="value"
            nameKey="name"
            innerRadius="65%"
            outerRadius="85%"
            paddingAngle={2}
            stroke="none"
          >
            {data.map((d) => (
              <Cell key={d.name} fill={d.color} />
            ))}
          </Pie>
          <Tooltip contentStyle={CHART_TOOLTIP_STYLE} labelStyle={{ color: CHART_COLORS.axis }} />
        </PieChart>
      </ResponsiveContainer>
      <div className="absolute inset-0 flex flex-col items-center justify-center pointer-events-none">
        <span className="text-2xl font-bold tabular-nums text-foreground">{total}</span>
        <span className="text-xs text-muted-foreground">{label}</span>
      </div>
    </div>
  )
}
