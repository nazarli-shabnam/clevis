"use client"

import type { Dispatch, SetStateAction } from "react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { EMPTY_FORM, type FilterForm } from "@/lib/audit-filters"

/** The audit log's actor / target / date (and optionally action-prefix) filters, shared by the org and
 * workspace audit views. Filters apply on submit, not per keystroke: each change is a server query. */
export function AuditFilterForm({
  form,
  setForm,
  filtered,
  onApply,
  onClear,
  showActionPrefix = true,
}: {
  form: FilterForm
  setForm: Dispatch<SetStateAction<FilterForm>>
  filtered: boolean
  onApply: () => void
  onClear: () => void
  /** The workspace view filters by exact action through its own dropdown instead. */
  showActionPrefix?: boolean
}) {
  const field = (id: string, label: string, key: keyof FilterForm, props: { type?: string; placeholder?: string } = {}) => (
    <div className="flex flex-col gap-1">
      <label htmlFor={id} className="text-xs font-medium text-foreground">{label}</label>
      <Input
        id={id}
        value={form[key]}
        onChange={(e) => setForm((f) => ({ ...f, [key]: e.target.value }))}
        className="h-8 text-xs w-40"
        {...props}
      />
    </div>
  )

  return (
    <form
      className="px-4 py-3 border-b border-border flex flex-wrap items-end gap-3"
      onSubmit={(e) => {
        e.preventDefault()
        onApply()
      }}
    >
      {showActionPrefix && field("act-prefix", "Action starts with", "actionPrefix", { placeholder: "e.g. token." })}
      {field("act-actor", "Actor", "actor", { placeholder: "email" })}
      {field("act-target", "Target contains", "target", { placeholder: "e.g. acme/api" })}
      {field("act-from", "From (UTC)", "from", { type: "date" })}
      {field("act-to", "To (UTC)", "to", { type: "date" })}
      <Button type="submit" size="sm">Apply filters</Button>
      {filtered && (
        <button
          type="button"
          className="text-xs text-muted-foreground underline hover:text-foreground"
          onClick={() => {
            setForm(EMPTY_FORM)
            onClear()
          }}
        >
          Clear
        </button>
      )}
    </form>
  )
}
