# Clevis — Design System

Design reference for all UI work in `apps/ui`. Every visual decision should trace back to a rule here. When in doubt: subtract, not add.

---

## Philosophy

Clevis is a **professional developer security tool**, not a consumer app. The visual language reflects that:

- **Data first.** Every pixel should serve information, not decoration.
- **Dark and dense.** Developers work in dark environments. Information density is a feature.
- **Confidence through restraint.** A spare UI signals control. Clutter signals chaos.
- **Doppler / Linear lineage.** The primary design references are Doppler (dark security tooling) and Linear (data-dense dark SaaS). Take cues from both; avoid the "generic shadcn dark" look.

---

## Color

All tokens are defined in `apps/ui/app/globals.css` under `:root`.

### Backgrounds

| Role | Token | Hex | Usage |
|------|-------|-----|-------|
| Page background | `--background` | `#18181b` | Body, main content area |
| Sidebar | `--sidebar` | `#0f0f11` | Left navigation panel |
| Card surface | `--card` | `#27272a` | All panels, drawers, modals |
| Input / secondary | `--input` / `--secondary` | `#27272a` / `#3f3f46` | Form fields, chips |
| Muted fills | `--muted` | `#3f3f46` | Icon backgrounds, hover fills, table row hover |

The body also carries a **subtle radial purple glow** at the top (`radial-gradient` from `rgba(168,85,247,0.07)` to transparent). Keep it barely perceptible — it should register subconsciously, not visually.

### Foreground

| Role | Token | Hex | Usage |
|------|-------|-----|-------|
| Primary text | `--foreground` | `#f4f4f5` | Headings, body copy |
| Muted text | `--muted-foreground` | `#a1a1aa` | Labels, captions, empty states |
| Sidebar text | `--sidebar-foreground` | `#f4f4f5` | Nav labels |
| Inactive nav | — | `sidebar-foreground/45` | De-emphasised nav items |

### Accent / Status

| Role | Token | Hex | Usage |
|------|-------|-----|-------|
| Primary / brand | `--primary` | `#a855f7` | Active nav accent, primary buttons, focus rings, the dot logo |
| Success / pass | `--accent` | `#22c55e` | Passing checks, success states |
| Warning | `--chart-3` | `#f59e0b` | Medium-severity issues, dry-run badge |
| Error / fail | `--destructive` | `#f87171` | Failed checks, destructive actions |

**Never use raw hex values in component code.** Always use the CSS variable via Tailwind (`text-primary`, `bg-destructive`, `border-green-500/25`, etc.).

### Borders

Use `--border` (`#3f3f46`) for all structural borders. For status-tinted card borders (check cards, result panels) use opacity modifiers: `border-green-500/25`, `border-red-500/25`. Never a solid opaque status border — they dominate.

---

## Typography

Fonts are loaded in `apps/ui/app/layout.tsx` (Geist Sans + Geist Mono).

| Role | Class | Notes |
|------|-------|-------|
| Page title | `text-base font-semibold` | In `PageHeader`, not `h1` weight — we're a tool not a blog |
| Section label | `.section-title` | 0.8125rem, weight 500 — card header labels |
| Body / paragraph | `text-sm` | Default prose size throughout |
| Caption / label | `text-xs` | Form labels, table headers, stat card labels |
| Monospace | `font-mono text-xs` | Cache keys, tokens, JSON output, all code content |
| Stat value | `text-2xl font-bold tabular-nums tracking-tight` | Numbers in stat cards |
| Score | `text-xl font-bold tabular-nums` | SVG gauge label |

Section group labels in the sidebar use `text-[0.6875rem] uppercase tracking-widest` — the widest tracking in the system. Use sparingly; only for group dividers.

---

## Spacing & Layout

### Page layout

The app shell is a fixed-width sidebar (`<Sidebar>`) + full-height `<SidebarInset>`. Content lives inside `<main className="flex-1 p-6">` (set in `layout.tsx`).

All pages follow the same scaffold:

```tsx
<>
  <PageHeader title="..." description="..." />   // always first
  {/* content */}
</>
```

### Grid

Use CSS grid for page-level layout. Standard column splits:

| Layout | Class |
|--------|-------|
| Config + content | `grid gap-4 lg:grid-cols-3` (config = 1 col, content = `lg:col-span-2`) |
| Stat cards | `grid grid-cols-2 lg:grid-cols-4 gap-3` |
| Check card grid | `grid gap-3 sm:grid-cols-2` |

### Spacing scale

Prefer `gap-3` (12px) within components and `gap-4` (16px) between top-level panels. Card internal padding: `px-4 py-4` (content) or `px-4 py-3` (headers / footers).

---

## Components

### Sidebar (`components/app-sidebar.tsx`)

- Two section groups: **Analytics** (Overview, Activity, Repositories, Health & Security) and **Management** (Collaborators, Automation).
- Group labels: `text-[0.6875rem] uppercase tracking-widest text-sidebar-foreground/30`.
- **Active item**: `bg-sidebar-accent text-sidebar-primary` + a 2px left bar via `before:` pseudo-element (`before:w-0.5 before:bg-sidebar-primary`).
- **Inactive item**: `text-sidebar-foreground/45`, hover `text-sidebar-foreground bg-sidebar-accent/50`.
- Logo mark: small rounded square with `bg-sidebar-primary/20` fill and a `size-2` purple dot inside. Not an image.
- Footer: `GitBranch` icon + org name at `text-sidebar-foreground/30`. One line, no status dot.

### Page Header (`components/page-header.tsx`)

Always rendered as the first child of every page. Props:

```tsx
<PageHeader title="…" description="…" actions={<Button>…</Button>} />
```

- Title: `text-base font-semibold`.
- Description: `text-sm text-muted-foreground mt-0.5`.
- Bottom border: `border-b border-border pb-4 mb-5` — separates the header from page content.
- `actions` slot: right-aligned; use for page-level CTAs (e.g. "Run scan", "Refresh"). Optional.

### Stat Card (`components/stat-card.tsx`)

Used on the overview page. Four cards in a responsive grid.

```tsx
<StatCard label="Repositories" value="—" icon={FolderGit2} />
```

- Card: `bg-card border border-border rounded-lg px-4 py-4`.
- Label: `text-xs text-muted-foreground mb-1.5`.
- Value: `text-2xl font-bold tabular-nums tracking-tight`.
- Icon: `size-4 text-muted-foreground` in a `p-2 bg-muted/60 rounded-md` pill, top-right.

### Panel / Card

The raw card pattern used throughout:

```tsx
<div className="bg-card border border-border rounded-lg">
  <div className="px-4 py-3 border-b border-border">
    <span className="section-title">Label</span>
  </div>
  {/* body */}
</div>
```

The header strip always has a `border-b`. The `.section-title` utility is the only text style for card headers.

### Empty State (`components/empty-state.tsx`)

Replaces all "Coming soon" / no-data states.

```tsx
<EmptyState
  icon={Radio}
  title="Activity feed coming soon"
  description="Connect a GitHub organization to stream real-time events…"
  cta={<Button disabled>Connect</Button>}   // optional
/>
```

- Icon: `size-6` in `p-3 bg-muted/60 rounded-lg`, centred above text.
- Title: `text-sm font-medium text-foreground`.
- Description: `text-sm text-muted-foreground max-w-xs leading-relaxed`.
- Vertical padding: `py-16`. Always centred.

### Check Card (`components/check-card.tsx`)

Used in the security results grid.

- Pass: `border-green-500/25`, hover `border-green-500/40`.
- Fail: `border-red-500/25`, hover `border-red-500/40`.
- Severity badge: pill with coloured text + low-opacity background tint (`bg-red-400/10`, etc.).
- Remediation: `text-xs text-muted-foreground leading-relaxed`.

### Tables

No third-party table library. Use a plain `<table>` with these conventions:

```tsx
<table className="w-full text-xs">
  <thead>
    <tr className="border-b border-border">
      <th className="text-left text-muted-foreground font-medium px-4 py-2">Col</th>
    </tr>
  </thead>
  <tbody className="divide-y divide-border">
    <tr className="hover:bg-muted/40 transition-colors">
      <td className="px-4 py-2.5 …">…</td>
    </tr>
  </tbody>
</table>
```

- `text-xs` throughout.
- Headers: `text-muted-foreground font-medium`.
- Row hover: `bg-muted/40`.
- Monospace columns (keys, hashes): `font-mono text-foreground/80 truncate`.
- Numeric / right-aligned columns: `text-right tabular-nums`.

### Stat Chip (`.stat-chip` utility)

Inline badge for counts and status labels in card headers and result panels.

```tsx
<span className="stat-chip">14 total</span>
<span className="stat-chip text-red-400 border-red-500/30">3 failed</span>
```

Base: `inline-flex items-center gap-1 text-xs border border-border rounded px-1.5 py-0.5 text-muted-foreground`.

### Score Gauge

SVG arc gauge in `security/page.tsx`. Pattern:

```tsx
<svg width="96" height="96" className="-rotate-90">
  <circle /* track */ stroke="#3f3f46" strokeWidth="7" />
  <circle /* fill  */ stroke={color} strokeDasharray={`${progress} ${circumference}`} strokeLinecap="round" />
</svg>
<div className="absolute inset-0 flex items-center justify-center">
  <span className="text-xl font-bold tabular-nums" style={{ color }}>{score}</span>
</div>
```

The wrapper `div` is `relative`; the label `div` is `absolute inset-0`. Color thresholds: `≥80` → green, `≥50` → amber, `<50` → red.

### JSON Output Block

For API result panels:

```tsx
<pre className="font-mono text-xs text-muted-foreground leading-relaxed overflow-auto bg-muted/30 rounded-md p-3 border border-border/50">
  {JSON.stringify(data, null, 2)}
</pre>
```

### Forms

- Labels: `text-xs font-medium text-foreground block mb-1.5`.
- Inputs: use `<Input>` from `components/ui/input`. Monospace inputs (tokens, keys) get `className="font-mono"`.
- Button row: primary action at top (`<Button>`), secondary actions below in a `grid grid-cols-2 gap-2`.
- Error display: `flex items-start gap-2 text-xs text-destructive` with a `AlertTriangle size-3.5` icon.

---

## Iconography

Icons come from **Lucide React** only. No other icon sets.

Sizing conventions:

| Context | Class |
|---------|-------|
| Nav icons | `size-4` |
| Card header icons | `size-3.5` |
| Empty state icons | `size-6` (inside the icon box) |
| Inline text icons | `size-3.5` |
| Status icons (check/x) | `size-4` |

Always pass `shrink-0` on icons next to truncatable text.

---

## Motion

Keep it minimal and functional:

- `transition-colors` on interactive elements (nav items, table rows, quick-action links).
- `transition-opacity` for elements that reveal on hover (e.g. the arrow in quick actions).
- SVG arc: `transition: stroke-dasharray 0.6s ease` — the only animated layout element.
- No entrance animations, no skeletons, no loading spinners beyond `<Loader2 animate-spin>` inside buttons.

---

## Borders & Radius

`--radius` is `0.25rem` — very tight. The system is intentionally angular:

| Scale | Value |
|-------|-------|
| `rounded` | `0.25rem` — default for all components |
| `rounded-md` | `0.375rem` — icon boxes, muted fill pills |
| `rounded-lg` | `0.5rem` — cards, panels, inputs |
| `rounded-full` | Reserved for the score gauge track only |

Avoid `rounded-xl` and above.

---

## Do / Don't

**Do:**
- Build data density. Err on the side of more information per screen.
- Use `text-muted-foreground` for anything secondary. Foreground is for primary content only.
- Reach for `.stat-chip` and `EmptyState` before inventing new patterns.
- Keep borders at `border-border` (`#3f3f46`). Status tints use opacity (`/25`, `/30`), never solid.

**Don't:**
- Add gradients to components (the body glow is the only gradient).
- Use colour for decoration — only for status (pass/fail/warn/error).
- Add shadow utilities (`shadow-*`). The dark-on-dark palette doesn't need depth shadows.
- Stack multiple font weights on the same line. Pick one.
- Use `rounded-xl` or larger radius — it undermines the sharp/professional feel.
- Introduce a new icon library or icon set.
