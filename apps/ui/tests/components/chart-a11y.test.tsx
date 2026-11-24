import { cleanup, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

// jsdom has no layout; stand Recharts in with empty containers so only the text alternatives are exercised.
vi.mock("recharts", () => {
  const Empty = () => null
  const Box = ({ children }: { children?: React.ReactNode }) => <div>{children}</div>
  return {
    ResponsiveContainer: Box, AreaChart: Box, BarChart: Box, LineChart: Box, PieChart: Box,
    Area: Empty, Bar: Empty, Line: Empty, Pie: Box, Cell: Empty, XAxis: Empty, YAxis: Empty,
    CartesianGrid: Empty, Tooltip: Empty, Legend: Empty,
  }
})

import { AreaTimeChart } from "@/components/charts/area-time-chart"
import { BarGroupChart } from "@/components/charts/bar-group-chart"
import { DonutChart } from "@/components/charts/donut-chart"
import { HeatmapCalendar } from "@/components/charts/heatmap-calendar"
import { MiniSparkline } from "@/components/charts/mini-sparkline"

afterEach(cleanup)

describe("chart text alternatives (#1291)", () => {
  it("describes an area chart as an image with a summary", () => {
    render(<AreaTimeChart label="Commits" data={[{ week: "Oct 5", value: 4 }, { week: "Oct 12", value: 41 }]} />)
    const img = screen.getByRole("img")
    expect(img).toHaveAccessibleName(/Commits: 2 points, from 4 \(Oct 5\) to 41 \(Oct 12\), peak 41 \(Oct 12\)/)
  })

  it("describes a bar chart, a donut and a sparkline", () => {
    render(
      <>
        <BarGroupChart data={[{ name: "w1", opened: 3, merged: 2 }]} bars={[{ key: "opened", color: "#000" }, { key: "merged", color: "#111" }]} />
        <DonutChart label="alerts" data={[{ name: "high", value: 2, color: "#f00" }, { name: "low", value: 1, color: "#0f0" }]} />
        <MiniSparkline data={[1, 5, 3]} />
      </>,
    )
    const names = screen.getAllByRole("img").map((el) => el.getAttribute("aria-label"))
    expect(names).toEqual([
      "Bar chart of opened, merged across 1 group, from w1 to w1.",
      "alerts: 3 in total (high 2, low 1).",
      "Trend over 3 points: from 1 to 3, low 1, high 5.",
    ])
  })

  it("summarises the heatmap with its peak and total instead of anonymous cells", () => {
    render(<HeatmapCalendar data={[0, 3, 41, 2]} colorScale={["#000", "#111", "#222"]} />)
    expect(screen.getByRole("img")).toHaveAccessibleName(
      "Activity heatmap: 4 weeks, 46 in total, peak 41 in week 3 (week 4 is the most recent).",
    )
    render(<HeatmapCalendar data={[0, 0]} colorScale={[]} />)
    expect(screen.getAllByRole("img")[1]).toHaveAccessibleName("Activity heatmap: no activity.")
  })
})
