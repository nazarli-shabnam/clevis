"use client"

import { useState } from "react"
import { useMutation } from "@tanstack/react-query"
import { api } from "@/lib/api/client"
import { downloadBlob } from "@/lib/download"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"

const dayInput = (daysAgo: number) => new Date(Date.now() - daysAgo * 86_400_000).toISOString().slice(0, 10)

/** Org admins download one zip of audit evidence (scan history, failed checks, roster + 2FA, audit log). */
export function EvidenceExportCard({ orgLogin }: { orgLogin: string }) {
  const [since, setSince] = useState(dayInput(90))
  const [until, setUntil] = useState(dayInput(0))

  const exportEvidence = useMutation({
    mutationFn: () => api.evidence.download(orgLogin, { since: since || undefined, until: until || undefined }),
    onSuccess: ({ blob, filename }) => downloadBlob(filename, blob),
  })

  return (
    <div className="card mb-4">
      <div className="px-4 py-3 border-b border-border">
        <span className="section-title">Compliance evidence</span>
      </div>
      <div className="p-4 flex flex-col gap-3">
        <p className="text-xs text-muted-foreground">
          One dated zip for an audit: score history with each scan&apos;s failed checks, the member roster with 2FA
          status and outside collaborators, this organization&apos;s audit log for the range, and a manifest. Each
          export is recorded in the audit log.
        </p>
        <div className="flex flex-wrap items-end gap-3">
          <div>
            <label htmlFor="evidence-since" className="text-xs font-medium block mb-1.5">From</label>
            <Input id="evidence-since" type="date" value={since} max={until || undefined} onChange={(e) => setSince(e.target.value)} />
          </div>
          <div>
            <label htmlFor="evidence-until" className="text-xs font-medium block mb-1.5">To</label>
            <Input id="evidence-until" type="date" value={until} min={since || undefined} onChange={(e) => setUntil(e.target.value)} />
          </div>
          <Button onClick={() => exportEvidence.mutate()} disabled={exportEvidence.isPending}>
            {exportEvidence.isPending ? "Preparing…" : "Export evidence"}
          </Button>
        </div>
        {exportEvidence.isError && (
          <p role="alert" className="text-xs text-destructive">
            Couldn&apos;t export: {exportEvidence.error.message}
          </p>
        )}
        {exportEvidence.isSuccess && exportEvidence.data.truncated && (
          <p role="status" className="text-xs text-amber-500">
            Some lists hit their size cap and are incomplete. The manifest in the zip says which; narrow the date
            range and export again.
          </p>
        )}
        {exportEvidence.isSuccess && !exportEvidence.data.truncated && (
          <p role="status" className="text-xs text-muted-foreground">Downloaded {exportEvidence.data.filename}.</p>
        )}
      </div>
    </div>
  )
}
