/** Says how far the shown score is under the org's target. Nothing when there is no target, no score, or the
 * score has reached it. */
export function TargetScoreBanner({ score, target }: { score: number | null; target: number | undefined }) {
  if (target === undefined || score === null || score >= target) return null
  const gap = target - score
  return (
    <p role="status" className="mb-3 rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs text-foreground">
      Score {score} is {gap} point{gap === 1 ? "" : "s"} below the organization&rsquo;s target of {target}.
    </p>
  )
}
