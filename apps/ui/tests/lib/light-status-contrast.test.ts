import { readFileSync } from "node:fs"
import { join } from "node:path"
import { describe, expect, it } from "vitest"

const css = readFileSync(join(__dirname, "../../app/globals.css"), "utf8")
const light = css.slice(css.indexOf('[data-theme="light"] {'))
const lightBlock = light.slice(0, light.indexOf("\n}"))

function token(name: string): string {
  const m = lightBlock.match(new RegExp(name + ": *(#[0-9a-fA-F]{6})"))
  if (!m) throw new Error(`${name} not found in the light theme block`)
  return m[1]
}

function luminance(hex: string): number {
  const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255).map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4))
  return 0.2126 * r + 0.7152 * g + 0.0722 * b
}

function contrast(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x)
  return (hi + 0.05) / (lo + 0.05)
}

const overridden = [...lightBlock.matchAll(/(--color-[a-z]+-[0-9]{3}):/g)].map((m) => m[1])

describe("light theme status colours", () => {
  it("remaps the raw palette shades the UI uses for status text", () => {
    expect(overridden).toEqual(
      expect.arrayContaining(["--color-yellow-400", "--color-red-400", "--color-green-400", "--color-amber-500", "--color-orange-400", "--color-blue-400"]),
    )
  })

  it.each(overridden)("%s reaches WCAG AA (4.5:1) on the light surfaces", (name) => {
    for (const surface of ["--background", "--card", "--elevated"]) {
      expect(contrast(token(name), token(surface)), `${name} on ${surface}`).toBeGreaterThanOrEqual(4.5)
    }
  })
})
