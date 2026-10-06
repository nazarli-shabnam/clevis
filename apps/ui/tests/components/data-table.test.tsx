import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const downloadTextFileMock = vi.fn();
vi.mock("@/lib/download", () => ({
  downloadTextFile: (...args: unknown[]) => downloadTextFileMock(...args),
}));

import { DataTable, type DataTableColumn } from "@/components/ui/data-table";

interface Row {
  id: number;
  name: string;
  score: number;
}

const ROWS: Row[] = [
  { id: 1, name: "Charlie", score: 30 },
  { id: 2, name: "Alice", score: 10 },
  { id: 3, name: "Bob", score: 20 },
];

const COLUMNS: DataTableColumn<Row>[] = [
  { key: "name", header: "Name", sortValue: (r) => r.name, render: (r) => r.name },
  { key: "score", header: "Score", align: "right", sortValue: (r) => r.score, render: (r) => String(r.score) },
];

afterEach(() => {
  cleanup();
});

describe("DataTable", () => {
  it("renders rows in the given order when no sort is applied", () => {
    render(<DataTable columns={COLUMNS} data={ROWS} getRowKey={(r) => r.id} />);
    const rows = screen.getAllByRole("row").slice(1);
    expect(rows[0]).toHaveTextContent("Charlie");
    expect(rows[1]).toHaveTextContent("Alice");
    expect(rows[2]).toHaveTextContent("Bob");
  });

  it("sorts ascending on first header click and descending on the second", () => {
    render(<DataTable columns={COLUMNS} data={ROWS} getRowKey={(r) => r.id} />);

    fireEvent.click(screen.getByText("Name"));
    let rows = screen.getAllByRole("row").slice(1);
    expect(rows[0]).toHaveTextContent("Alice");
    expect(rows[1]).toHaveTextContent("Bob");
    expect(rows[2]).toHaveTextContent("Charlie");

    fireEvent.click(screen.getByText("Name"));
    rows = screen.getAllByRole("row").slice(1);
    expect(rows[0]).toHaveTextContent("Charlie");
    expect(rows[1]).toHaveTextContent("Bob");
    expect(rows[2]).toHaveTextContent("Alice");
  });

  it("switches the active sort column when a different header is clicked", () => {
    render(<DataTable columns={COLUMNS} data={ROWS} getRowKey={(r) => r.id} />);

    fireEvent.click(screen.getByText("Score"));
    const rows = screen.getAllByRole("row").slice(1);
    expect(rows[0]).toHaveTextContent("Alice"); // score 10
    expect(rows[1]).toHaveTextContent("Bob"); // score 20
    expect(rows[2]).toHaveTextContent("Charlie"); // score 30
  });

  it("does not attach a sort handler to columns without sortValue", () => {
    const columns: DataTableColumn<Row>[] = [{ key: "name", header: "Name", render: (r) => r.name }];
    render(<DataTable columns={columns} data={ROWS} getRowKey={(r) => r.id} />);
    const header = screen.getByText("Name").closest("th")!;
    expect(header.className).not.toContain("cursor-pointer");
  });

  it("paginates once data exceeds pageSize, and Prev/Next move between pages", () => {
    render(<DataTable columns={COLUMNS} data={ROWS} getRowKey={(r) => r.id} pageSize={2} />);

    expect(screen.getByText("Page 1 of 2 · 3 total")).toBeInTheDocument();
    let rows = screen.getAllByRole("row").slice(1);
    expect(rows).toHaveLength(2);
    expect(screen.getByRole("button", { name: "Prev" })).toBeDisabled();

    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    expect(screen.getByText("Page 2 of 2 · 3 total")).toBeInTheDocument();
    rows = screen.getAllByRole("row").slice(1);
    expect(rows).toHaveLength(1);
    expect(screen.getByRole("button", { name: "Next" })).toBeDisabled();
  });

  it("does not show pagination controls when data fits within one page", () => {
    render(<DataTable columns={COLUMNS} data={ROWS} getRowKey={(r) => r.id} pageSize={10} />);
    expect(screen.queryByRole("button", { name: "Prev" })).not.toBeInTheDocument();
  });

  it("resets to page 1 when the data changes", () => {
    const { rerender } = render(
      <DataTable columns={COLUMNS} data={ROWS} getRowKey={(r) => r.id} pageSize={2} />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    expect(screen.getByText("Page 2 of 2 · 3 total")).toBeInTheDocument();

    rerender(
      <DataTable
        columns={COLUMNS}
        data={[...ROWS, { id: 4, name: "Dana", score: 40 }]}
        getRowKey={(r) => r.id}
        pageSize={2}
      />,
    );
    expect(screen.getByText("Page 1 of 2 · 4 total")).toBeInTheDocument();
  });

  it("attaches a ref to the row selected by getRowRef", () => {
    let refEl: HTMLTableRowElement | null = null;
    render(
      <DataTable
        columns={COLUMNS}
        data={ROWS}
        getRowKey={(r) => r.id}
        getRowRef={(r) => (r.id === 2 ? (el: HTMLTableRowElement | null) => { refEl = el; } : undefined)}
      />,
    );
    expect(refEl).not.toBeNull();
    expect(refEl!).toHaveTextContent("Alice");
  });

  it("renders sortable headers as real buttons so sorting works from the keyboard", () => {
    render(<DataTable columns={COLUMNS} data={ROWS} getRowKey={(r) => r.id} />);
    const button = screen.getByRole("button", { name: "Name" });
    expect(button.tagName).toBe("BUTTON");
    fireEvent.click(button);
    expect(button.closest("th")).toHaveAttribute("aria-sort", "ascending");
  });

  it("does not render a button for columns without sortValue", () => {
    const columns: DataTableColumn<Row>[] = [{ key: "name", header: "Name", render: (r) => r.name }];
    render(<DataTable columns={columns} data={ROWS} getRowKey={(r) => r.id} />);
    expect(screen.queryByRole("button", { name: "Name" })).toBeNull();
  });
});

describe("DataTable CSV export", () => {
  const EXPORT_COLUMNS: DataTableColumn<Row>[] = [
    { key: "name", header: "Name", sortValue: (r) => r.name, csvValue: (r) => r.name, render: (r) => <b>{r.name}</b> },
    { key: "score", header: "Score", sortValue: (r) => r.score, csvValue: (r) => r.score, render: (r) => String(r.score) },
    // no csvValue: presentational only, must not appear in the export
    { key: "actions", header: "Actions", render: () => <button type="button">Open</button> },
  ];

  beforeEach(() => downloadTextFileMock.mockReset());

  it("has no export button unless exportCsv is set", () => {
    render(<DataTable columns={EXPORT_COLUMNS} data={ROWS} getRowKey={(r) => r.id} />);
    expect(screen.queryByRole("button", { name: /export csv/i })).toBeNull();
  });

  it("downloads the rows in the current sort order, using only columns with a csvValue", () => {
    render(<DataTable columns={EXPORT_COLUMNS} data={ROWS} getRowKey={(r) => r.id} exportCsv={{ name: "people" }} />);

    fireEvent.click(screen.getByRole("button", { name: "Name" })); // sort ascending by name
    fireEvent.click(screen.getByRole("button", { name: /export csv/i }));

    expect(downloadTextFileMock).toHaveBeenCalledTimes(1);
    const [filename, csv, mime] = downloadTextFileMock.mock.calls[0];
    expect(filename).toMatch(/^clevis-people-\d{4}-\d{2}-\d{2}\.csv$/);
    expect(mime).toBe("text/csv");
    expect(csv).toBe(["Name,Score", "Alice,10", "Bob,20", "Charlie,30"].join("\r\n"));
  });

  it("exports every row, not just the current page", () => {
    const many = Array.from({ length: 5 }, (_, i) => ({ id: i, name: `n${i}`, score: i }));
    render(<DataTable columns={EXPORT_COLUMNS} data={many} getRowKey={(r) => r.id} pageSize={2} exportCsv={{ name: "all" }} />);
    expect(screen.getAllByRole("row")).toHaveLength(3); // header + 2 rows on page 1

    fireEvent.click(screen.getByRole("button", { name: /export csv/i }));

    const csv = downloadTextFileMock.mock.calls[0][1] as string;
    expect(csv.split("\r\n")).toHaveLength(6); // header + all 5
    expect(csv).toContain("n4,4");
  });

  it("keeps the spreadsheet formula-injection protection of toCsv", () => {
    const rows = [{ id: 1, name: "=HYPERLINK(\"http://evil\")", score: 1 }];
    render(<DataTable columns={EXPORT_COLUMNS} data={rows} getRowKey={(r) => r.id} exportCsv={{ name: "x" }} />);

    fireEvent.click(screen.getByRole("button", { name: /export csv/i }));

    // leading quote defuses the formula; the embedded quotes are then doubled per RFC 4180
    expect(downloadTextFileMock.mock.calls[0][1]).toContain(`"'=HYPERLINK(""http://evil"")"`);
  });

  it("disables the button when there is nothing to export", () => {
    render(<DataTable columns={EXPORT_COLUMNS} data={[]} getRowKey={(r) => r.id} exportCsv={{ name: "none" }} />);
    expect(screen.getByRole("button", { name: /export csv/i })).toBeDisabled();
  });
});
