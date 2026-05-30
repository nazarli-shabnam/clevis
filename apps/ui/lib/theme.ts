"use client"

import { useEffect, useState } from "react"

// Theme system — neutral "shade/vibe" palettes (whites/blacks and muted tints).
// Colors are driven entirely by CSS variables in globals.css, keyed by the
// `data-theme` attribute on <html>. The dark/light class is toggled alongside so
// the few `dark:` Tailwind utilities (badge, input) keep working.

export type ThemeName = "midnight" | "carbon" | "slate" | "dim" | "paper" | "ash"

export interface ThemeMeta {
  name: ThemeName
  label: string
  isDark: boolean
  // [page background, surface, accent] — used for the picker preview swatch.
  swatch: [string, string, string]
}

export const THEMES: ThemeMeta[] = [
  { name: "midnight", label: "Midnight", isDark: true, swatch: ["#090909", "#1c1c1c", "#60a5fa"] },
  { name: "carbon", label: "Carbon", isDark: true, swatch: ["#141414", "#2a2a2a", "#60a5fa"] },
  { name: "slate", label: "Slate", isDark: true, swatch: ["#0b0f14", "#1e2733", "#5b9dff"] },
  { name: "dim", label: "Dim", isDark: true, swatch: ["#1c2128", "#373e47", "#6cb6ff"] },
  { name: "paper", label: "Paper", isDark: false, swatch: ["#fafafa", "#e4e4e7", "#2563eb"] },
  { name: "ash", label: "Ash", isDark: false, swatch: ["#eef1f4", "#d4dae1", "#2563eb"] },
]

export const DEFAULT_THEME: ThemeName = "midnight"
export const THEME_STORAGE_KEY = "clevis:theme"

function isThemeName(value: string | null): value is ThemeName {
  return !!value && THEMES.some((t) => t.name === value)
}

/** Apply a theme to <html> and persist the choice. Client-side only. */
export function applyTheme(name: ThemeName): void {
  const meta = THEMES.find((t) => t.name === name) ?? THEMES[0]
  const root = document.documentElement
  root.dataset.theme = meta.name
  root.classList.toggle("dark", meta.isDark)
  root.classList.toggle("light", !meta.isDark)
  try {
    localStorage.setItem(THEME_STORAGE_KEY, meta.name)
  } catch {
    // localStorage unavailable (private mode etc.) — theme still applies for this session.
  }
}

/** Read/set the active theme. Reads the persisted value after mount. */
export function useTheme() {
  const [theme, setThemeState] = useState<ThemeName>(DEFAULT_THEME)

  useEffect(() => {
    const stored = localStorage.getItem(THEME_STORAGE_KEY)
    setThemeState(isThemeName(stored) ? stored : DEFAULT_THEME)
  }, [])

  const setTheme = (name: ThemeName) => {
    applyTheme(name)
    setThemeState(name)
  }

  return { theme, setTheme }
}
