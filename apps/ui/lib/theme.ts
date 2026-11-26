"use client"

import { useEffect, useState } from "react"

// Colors come from CSS variables in globals.css keyed by <html data-theme="dark|light">; the dark/light
// class is toggled alongside so the few `dark:` Tailwind utilities keep working. The stored preference
// may also be "system", which is resolved against the OS setting. layout.tsx's inline themeScript
// mirrors normalizeTheme/resolveTheme before first paint, so keep the two in sync.

export type ThemeName = "dark" | "light" | "system"
export type ResolvedTheme = "dark" | "light"

export interface ThemeMeta {
  name: ThemeName
  label: string
  description: string
}

export const THEMES: ThemeMeta[] = [
  { name: "dark", label: "Dark", description: "Easy on the eyes" },
  { name: "light", label: "Light", description: "Bright and clean" },
  { name: "system", label: "System", description: "Follows your device" },
]

export const DEFAULT_THEME: ThemeName = "dark"
export const THEME_STORAGE_KEY = "clevis:theme"

const LEGACY_LIGHT = ["paper", "ash"]

/** Map a stored value (including the six pre-Dark/Light/System theme names) to a current preference. */
export function normalizeTheme(value: string | null): ThemeName {
  if (value === "dark" || value === "light" || value === "system") return value
  if (value && LEGACY_LIGHT.includes(value)) return "light"
  return DEFAULT_THEME
}

export function resolveTheme(name: ThemeName): ResolvedTheme {
  if (name !== "system") return name
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light"
}

function paint(name: ThemeName): void {
  const resolved = resolveTheme(name)
  const root = document.documentElement
  root.dataset.theme = resolved
  root.classList.toggle("dark", resolved === "dark")
  root.classList.toggle("light", resolved === "light")
}

/** Apply a theme to <html> and persist the preference. Client-side only. */
export function applyTheme(name: ThemeName): void {
  paint(name)
  try {
    localStorage.setItem(THEME_STORAGE_KEY, name)
  } catch {
    // localStorage unavailable (private mode etc.) — theme still applies for this session.
  }
}

/** Read/set the active theme preference. Reads the persisted value after mount. */
export function useTheme() {
  const [theme, setThemeState] = useState<ThemeName>(DEFAULT_THEME)

  useEffect(() => {
    let stored: string | null = null
    try {
      stored = localStorage.getItem(THEME_STORAGE_KEY)
    } catch {
      // unavailable — fall back to the default
    }
    setThemeState(normalizeTheme(stored))
  }, [])

  const setTheme = (name: ThemeName) => {
    applyTheme(name)
    setThemeState(name)
  }

  return { theme, setTheme }
}
