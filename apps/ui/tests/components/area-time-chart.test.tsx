import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

// jsdom has no layout, so stand the chart in with a button that fires the same onClick state Recharts passes.
vi.mock("recharts", () => ({
  ResponsiveContainer: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  AreaChart: ({ onClick, children }: { onClick?: (s: { activeIndex?: unknown }) => void; children?: React.ReactNode }) => (
    <>
      {children}
      <button onClick={() => onClick?.({ activeIndex: "1" })}>point</button>
      <button onClick={() => onClick?.({ activeIndex: "9" })}>outside</button>
      <button onClick={() => onClick?.({})}>none</button>
    </>
  ),
  Area: () => null,
  ReferenceLine: ({ y }: { y: number }) => <span data-testid="target-line">{y}</span>,
  XAxis: () => null,
  YAxis: () => null,
  CartesianGrid: () => null,
  Tooltip: () => null,
}))

import { AreaTimeChart } from "@/components/charts/area-time-chart"

afterEach(cleanup)

const data = [
  { week: "a", value: 1 },
  { week: "b", value: 2 },
]

describe("AreaTimeChart onPointClick", () => {
  it("reports the clicked index and ignores clicks off the data", () => {
    const onPointClick = vi.fn()
    render(<AreaTimeChart data={data} label="score" onPointClick={onPointClick} />)

    fireEvent.click(screen.getByText("point"))
    fireEvent.click(screen.getByText("outside"))
    fireEvent.click(screen.getByText("none"))

    expect(onPointClick).toHaveBeenCalledTimes(1)
    expect(onPointClick).toHaveBeenCalledWith(1)
  })

  it("is not clickable without a handler", () => {
    render(<AreaTimeChart data={data} label="score" />)
    fireEvent.click(screen.getByText("point"))
  })

  it("draws a target line and names it in the text alternative", () => {
    render(<AreaTimeChart data={data} label="score" target={85} />)

    expect(screen.getByTestId("target-line")).toHaveTextContent("85")
    expect(screen.getByRole("img", { name: /Target 85\.$/ })).toBeInTheDocument()
  })

  it("draws no line without a target", () => {
    render(<AreaTimeChart data={data} label="score" />)

    expect(screen.queryByTestId("target-line")).not.toBeInTheDocument()
  })
})
