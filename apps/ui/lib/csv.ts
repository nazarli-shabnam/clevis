// Minimal RFC-4180 CSV serialiser with spreadsheet formula-injection defense.

/**
 * Serialise one field. Formula-like values (leading =, +, -, @, tab, CR; plain numbers excepted)
 * get a leading single quote so an export can't smuggle a formula into a spreadsheet; then RFC-4180 quoting.
 */
function escapeField(value: unknown): string {
  let s = value === null || value === undefined ? "" : String(value)
  if (/^[=+\-@\t\r]/.test(s) && !/^-?\d+(\.\d+)?$/.test(s)) s = `'${s}`
  return /[",\r\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s
}

export interface CsvColumn<T> {
  header: string
  value: (row: T) => unknown
}

/** Render `rows` as a CSV string with a header line. Uses CRLF line endings per RFC 4180. */
export function toCsv<T>(rows: readonly T[], columns: readonly CsvColumn<T>[]): string {
  const lines = [columns.map((c) => escapeField(c.header)).join(",")]
  for (const row of rows) {
    lines.push(columns.map((c) => escapeField(c.value(row))).join(","))
  }
  return lines.join("\r\n")
}
