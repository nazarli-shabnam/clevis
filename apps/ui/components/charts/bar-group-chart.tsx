"use client"

import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer,
} from "recharts"
import { CHART_COLORS, CHART_TOOLTIP_STYLE } from "@/lib/charts/theme"

interface BarGroupChartProps {
  data: { name: string; [key: string]: number | string }[]
  bars: { key: string; color: string }[]
  height?: number
}

/** Grouped bar chart — renders one Bar series per entry in `bars`. */
export function BarGroupChart({ data, bars, height = 240 }: BarGroupChartProps) {
  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart data={data}>
        <CartesianGrid strokeDasharray="3 3" stroke={CHART_COLORS.grid} />
        <XAxis dataKey="name" stroke={CHART_COLORS.axis} fontSize={11} tickLine={false} />
        <YAxis stroke={CHART_COLORS.axis} fontSize={11} tickLine={false} width={36} />
        <Tooltip
          contentStyle={CHART_TOOLTIP_STYLE}
          labelStyle={{ color: CHART_COLORS.axis }}
          cursor={{ fill: "rgba(255,255,255,0.04)" }}
        />
        {bars.length > 1 && <Legend wrapperStyle={{ fontSize: "11px" }} />}
        {bars.map((b) => (
          <Bar key={b.key} dataKey={b.key} fill={b.color} radius={[2, 2, 0, 0]} />
        ))}
      </BarChart>
    </ResponsiveContainer>
  )
}
