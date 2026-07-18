// Single source of truth for chart theming. References the design tokens in
// globals.css (electric-blue accent + graphite neutrals) so every chart stays
// visually consistent and a future library swap only has to re-map these values.

export const CHART_COLORS = {
  primary: "#3b82f6", // matches --primary
  grid: "#2a2a2c", // matches --border
  axis: "#8f8f93", // matches --muted-foreground
  series: ["#3b82f6", "#22c55e", "#fbbf24", "#f87171", "#a78bfa", "#38bdf8"],
} as const

// Shared Recharts <Tooltip> contentStyle so line/bar charts render identical,
// theme-matched tooltips (soft corners, card background, graphite border).
export const CHART_TOOLTIP_STYLE = {
  background: "#161617", // matches --card
  border: `1px solid ${CHART_COLORS.grid}`,
  borderRadius: 6,
  fontSize: "12px",
} as const
