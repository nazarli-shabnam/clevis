import { EXPIRY_FILTER_LABELS, type ExpiryFilter } from "@/lib/expiry-filter"

export function ExpiryFilterSelect({
  value,
  onChange,
  label,
}: {
  value: ExpiryFilter
  onChange: (value: ExpiryFilter) => void
  label: string
}) {
  return (
    <select
      aria-label={label}
      value={value}
      onChange={(e) => onChange(e.target.value as ExpiryFilter)}
      className="card text-xs text-muted-foreground px-2 py-1 focus:outline-none focus:ring-1 focus:ring-ring"
    >
      {(Object.keys(EXPIRY_FILTER_LABELS) as ExpiryFilter[]).map((f) => (
        <option key={f} value={f}>{EXPIRY_FILTER_LABELS[f]}</option>
      ))}
    </select>
  )
}
