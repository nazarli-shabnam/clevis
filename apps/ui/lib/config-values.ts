/** Helpers for instance-config form state vs server refetches. */

export function initialConfigValues(
  current: Record<string, string>,
  server: Record<string, string>,
): Record<string, string> {
  return Object.keys(current).length === 0 ? server : current
}

export function mergeSavedConfigValue(
  current: Record<string, string>,
  server: Record<string, string>,
  savedKey: string,
): Record<string, string> {
  return { ...current, [savedKey]: server[savedKey] ?? current[savedKey] ?? "" }
}
