// Single source of truth for chart theming; mirrors the design tokens in globals.css.

export const CHART_COLORS = {
  primary: "#3b82f6", // matches --primary
  grid: "#2a2a2c", // matches --border
  axis: "#8f8f93", // matches --muted-foreground
  series: ["#3b82f6", "#22c55e", "#38bdf8", "#f87171", "#a78bfa", "#fbbf24"], // chart-1..5 order, amber appended last
} as const

// Shared Recharts <Tooltip> contentStyle.
export const CHART_TOOLTIP_STYLE = {
  background: "#161617", // matches --card
  border: `1px solid ${CHART_COLORS.grid}`,
  borderRadius: 6,
  fontSize: "12px",
} as const
