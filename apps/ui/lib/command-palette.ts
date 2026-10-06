export type CommandKind = "page" | "scope" | "repo"

export interface Command {
  id: string
  kind: CommandKind
  label: string
  /** Secondary text, shown dimmed and searched too. */
  hint: string
  /** Route to open; exactly one of href / scope is set. */
  href?: string
  scope?: { kind: "org" | "personal"; login: string }
}

/** 3 = starts with the query, 2 = a word starts with it, 1 = contains it, 0.5 = its letters appear in
 * order; null = no match. Case-insensitive. An empty query matches everything equally. */
export function matchScore(text: string, query: string): number | null {
  const q = query.trim().toLowerCase()
  if (!q) return 1
  const t = text.toLowerCase()
  if (t.startsWith(q)) return 3
  if (t.split(/[\s/~._-]+/).some((w) => w.startsWith(q))) return 2
  if (t.includes(q)) return 1
  let i = 0
  for (const ch of t) if (ch === q[i]) i++
  return i === q.length ? 0.5 : null
}

const KIND_ORDER: Record<CommandKind, number> = { page: 0, scope: 1, repo: 2 }

/** Matching commands, best first; ties keep pages before accounts before repos, then input order. */
export function rankCommands(commands: Command[], query: string, limit = 50): Command[] {
  return commands
    .map((c, index) => {
      const label = matchScore(c.label, query)
      const hint = matchScore(c.hint, query)
      const score = label === null && hint === null ? null : Math.max(label ?? 0, (hint ?? 0) * 0.5)
      return { c, index, score }
    })
    .filter((x): x is { c: Command; index: number; score: number } => x.score !== null)
    .sort((a, b) => b.score - a.score || KIND_ORDER[a.c.kind] - KIND_ORDER[b.c.kind] || a.index - b.index)
    .slice(0, limit)
    .map((x) => x.c)
}
