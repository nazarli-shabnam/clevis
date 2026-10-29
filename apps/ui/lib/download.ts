// Triggers a browser "save file" for an in-memory string; isolated so tests can stub it. No-op on the server.

export function downloadTextFile(filename: string, content: string, mimeType = "text/plain"): void {
  downloadBlob(filename, new Blob([content], { type: `${mimeType};charset=utf-8` }))
}

/** Save a Blob (e.g. a zip the API generated) under `filename`. */
export function downloadBlob(filename: string, blob: Blob): void {
  if (typeof document === "undefined") return
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement("a")
  anchor.href = url
  anchor.download = filename
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  // Revoke on the next tick so the click has been dispatched first.
  setTimeout(() => URL.revokeObjectURL(url), 0)
}
