import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it } from "vitest"

import { DataTable, type DataTableColumn } from "@/components/ui/data-table"

interface Row {
  id: number
  name: string
  note: string | null
}

const ROWS: Row[] = [
  { id: 1, name: "Alice", note: "first note" },
  { id: 2, name: "Bob", note: null },
]
const COLUMNS: DataTableColumn<Row>[] = [{ key: "name", header: "Name", render: (r) => r.name }]

afterEach(cleanup)

describe("DataTable renderExpanded", () => {
  it("adds no toggle column unless asked to", () => {
    render(<DataTable columns={COLUMNS} data={ROWS} getRowKey={(r) => r.id} />)
    expect(screen.queryByRole("button", { name: "Show details" })).not.toBeInTheDocument()
  })

  it("toggles the detail row, only for rows that have detail", () => {
    render(<DataTable columns={COLUMNS} data={ROWS} getRowKey={(r) => r.id} renderExpanded={(r) => r.note} />)

    const toggle = screen.getByRole("button", { name: "Show details" })
    expect(toggle).toHaveAttribute("aria-expanded", "false")
    expect(screen.getAllByRole("button", { name: /details/i })).toHaveLength(1)

    fireEvent.click(toggle)
    expect(screen.getByText("first note")).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Hide details" })).toHaveAttribute("aria-expanded", "true")

    fireEvent.click(screen.getByRole("button", { name: "Hide details" }))
    expect(screen.queryByText("first note")).not.toBeInTheDocument()
  })
})
