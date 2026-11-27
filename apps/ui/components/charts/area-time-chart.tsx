"use client"

import { useId } from "react"
import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ReferenceLine,
  ResponsiveContainer,
} from "recharts"
import { CHART_COLORS, CHART_TOOLTIP_STYLE } from "@/lib/charts/theme"
import { ChartA11y, describeSeries } from "./chart-a11y"

interface AreaTimeChartProps {
  data: { week: string; value: number }[]
  label: string
  color?: string
  height?: number
  /** Makes the points clickable; called with the index into `data` of the point under the click. */
  onPointClick?: (index: number) => void
  /** A horizontal goal line (e.g. an org's target score); the y axis is widened to include it. */
  target?: number
}

/** Time-series area chart with gradient fill (commit activity, velocity trends). */
export function AreaTimeChart({
  data,
  label,
  color = CHART_COLORS.primary,
  height = 240,
  onPointClick,
  target,
}: AreaTimeChartProps) {
  // useId keeps the gradient id unique even with several charts on one page.
  const gradientId = `area-grad-${useId().replace(/[^a-zA-Z0-9_-]/g, "")}`
  return (
    <ChartA11y summary={describeSeries(label, data.map((d) => ({ name: d.week, value: d.value }))) + (target === undefined ? "" : ` Target ${target}.`)}>
    <ResponsiveContainer width="100%" height={height}>
      <AreaChart
        data={data}
        style={onPointClick ? { cursor: "pointer" } : undefined}
        onClick={
          onPointClick
            ? (state) => {
                // activeIndex is null/"" when the click lands on no point; Number() would turn that into 0.
                if (state?.activeIndex == null || state.activeIndex === "") return
                const index = Number(state.activeIndex)
                if (Number.isInteger(index) && index >= 0 && index < data.length) onPointClick(index)
              }
            : undefined
        }
      >
        <defs>
          <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={color} stopOpacity={0.3} />
            <stop offset="100%" stopColor={color} stopOpacity={0} />
          </linearGradient>
        </defs>
        <CartesianGrid strokeDasharray="3 3" stroke={CHART_COLORS.grid} />
        <XAxis dataKey="week" stroke={CHART_COLORS.axis} fontSize={11} tickLine={false} />
        <YAxis stroke={CHART_COLORS.axis} fontSize={11} tickLine={false} width={36} domain={target === undefined ? undefined : [(min: number) => Math.min(min, target), (max: number) => Math.max(max, target)]} />
        <Tooltip
          contentStyle={CHART_TOOLTIP_STYLE}
          labelStyle={{ color: CHART_COLORS.axis }}
          cursor={{ stroke: CHART_COLORS.grid }}
        />
        {target !== undefined && (
          <ReferenceLine
            y={target}
            stroke={CHART_COLORS.axis}
            strokeDasharray="4 4"
            label={{ value: `Target ${target}`, position: "insideTopRight", fill: CHART_COLORS.axis, fontSize: 11 }}
          />
        )}
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
    </ChartA11y>
  )
}
