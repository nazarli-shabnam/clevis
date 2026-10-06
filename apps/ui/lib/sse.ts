export interface SseMessage {
  event: string
  data: string
}

/** Parses one SSE block (the text between blank lines). Comment lines (": heartbeat") and blocks with
 * no data produce null. Multiple `data:` lines join with a newline, as the SSE spec says. */
export function parseSseBlock(block: string): SseMessage | null {
  let event = "message"
  const data: string[] = []
  for (const raw of block.split("\n")) {
    const line = raw.endsWith("\r") ? raw.slice(0, -1) : raw
    if (line === "" || line.startsWith(":")) continue
    const colon = line.indexOf(":")
    const field = colon === -1 ? line : line.slice(0, colon)
    let value = colon === -1 ? "" : line.slice(colon + 1)
    if (value.startsWith(" ")) value = value.slice(1)
    if (field === "event") event = value
    else if (field === "data") data.push(value)
  }
  return data.length ? { event, data: data.join("\n") } : null
}

/** Reads a fetch() response body as server-sent events until it ends or `signal` aborts.
 * EventSource can't send an Authorization header, which password sessions rely on, hence this. */
export async function readSse(res: Response, onMessage: (m: SseMessage) => void): Promise<void> {
  if (!res.body) return
  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ""
  try {
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, "\n")
      let sep = buffer.indexOf("\n\n")
      while (sep !== -1) {
        const message = parseSseBlock(buffer.slice(0, sep))
        buffer = buffer.slice(sep + 2)
        if (message) onMessage(message)
        sep = buffer.indexOf("\n\n")
      }
    }
  } finally {
    reader.releaseLock()
  }
}
