import type { OrgAuditFilters } from "@/lib/api/types"

export interface FilterForm {
  actionPrefix: string
  actor: string
  target: string
  from: string // yyyy-mm-dd, UTC
  to: string // yyyy-mm-dd, UTC, inclusive
}

export const EMPTY_FORM: FilterForm = { actionPrefix: "", actor: "", target: "", from: "", to: "" }

/** The form's values as API filters. Dates are whole UTC days: `to` is inclusive, so the API's
 * exclusive `until` is the start of the following day. */
export function toAuditFilters(form: FilterForm): OrgAuditFilters {
  const filters: OrgAuditFilters = {}
  if (form.actionPrefix.trim()) filters.action_prefix = form.actionPrefix.trim()
  if (form.actor.trim()) filters.actor = form.actor.trim()
  if (form.target.trim()) filters.target = form.target.trim()
  if (form.from) filters.since = `${form.from}T00:00:00Z`
  if (form.to) {
    const next = new Date(`${form.to}T00:00:00Z`)
    next.setUTCDate(next.getUTCDate() + 1)
    filters.until = next.toISOString()
  }
  return filters
}
