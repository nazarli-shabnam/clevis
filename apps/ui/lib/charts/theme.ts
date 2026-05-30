// Single source of truth for chart theming. References the Phase 2 design
// tokens (blue accent + zinc neutrals) so every chart stays visually consistent
// and a future library swap only has to re-map these values.

export const CHART_COLORS = {
  primary: "#60a5fa", // blue-400 — matches --primary
  grid: "#27272a", // zinc-800
  axis: "#71717a", // zinc-500
  series: ["#60a5fa", "#34d399", "#fbbf24", "#f87171", "#a78bfa", "#22d3ee"],
} as const

// Shared Recharts <Tooltip> contentStyle so line/bar charts render identical,
// theme-matched tooltips (sharp corners, card background, zinc border).
export const CHART_TOOLTIP_STYLE = {
  background: "#18181b", // zinc-900 — matches --card
  border: `1px solid ${CHART_COLORS.grid}`,
  borderRadius: 0,
  fontSize: "12px",
} as const
