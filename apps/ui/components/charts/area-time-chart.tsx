"use client"

import { useId } from "react"
import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
} from "recharts"
import { CHART_COLORS, CHART_TOOLTIP_STYLE } from "@/lib/charts/theme"

interface AreaTimeChartProps {
  data: { week: string; value: number }[]
  label: string
  color?: string
  height?: number
}

/** Time-series area chart with gradient fill (commit activity, velocity trends). */
export function AreaTimeChart({
  data,
  label,
  color = CHART_COLORS.primary,
  height = 240,
}: AreaTimeChartProps) {
  // useId keeps the gradient id unique even with several charts on one page.
  const gradientId = `area-grad-${useId().replace(/[^a-zA-Z0-9_-]/g, "")}`
  return (
    <ResponsiveContainer width="100%" height={height}>
      <AreaChart data={data}>
        <defs>
          <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={color} stopOpacity={0.3} />
            <stop offset="100%" stopColor={color} stopOpacity={0} />
          </linearGradient>
        </defs>
        <CartesianGrid strokeDasharray="3 3" stroke={CHART_COLORS.grid} />
        <XAxis dataKey="week" stroke={CHART_COLORS.axis} fontSize={11} tickLine={false} />
        <YAxis stroke={CHART_COLORS.axis} fontSize={11} tickLine={false} width={36} />
        <Tooltip
          contentStyle={CHART_TOOLTIP_STYLE}
          labelStyle={{ color: CHART_COLORS.axis }}
          cursor={{ stroke: CHART_COLORS.grid }}
        />
        <Area
          type="monotone"
          dataKey="value"
          name={label}
          stroke={color}
          strokeWidth={2}
          fill={`url(#${gradientId})`}
        />
      </AreaChart>
    </ResponsiveContainer>
  )
}
