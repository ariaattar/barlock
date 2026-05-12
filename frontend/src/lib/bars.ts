import type { Analysis } from "./api"

export const LOOP_SIZES = [0.25, 0.5, 1, 2, 4, 8, 16, 32] as const
export type LoopSize = (typeof LOOP_SIZES)[number]

/** Time per bar in seconds, derived from BPM (assuming 4/4). */
export function barSeconds(bpm: number): number {
  return bpm > 0 ? (60 / bpm) * 4 : 2
}

/** Index of the bar whose start <= t. */
export function barIndexAtTime(downbeats: number[], t: number): number {
  if (!downbeats.length) return 0
  if (t <= downbeats[0]) return 0
  let lo = 0
  let hi = downbeats.length - 1
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1
    if (downbeats[mid] <= t) lo = mid
    else hi = mid - 1
  }
  return lo
}

/** Snap time t to the nearest downbeat. */
export function snapToDownbeat(downbeats: number[], t: number): number {
  if (!downbeats.length) return t
  let best = downbeats[0]
  let bestDelta = Math.abs(t - best)
  for (const d of downbeats) {
    const delta = Math.abs(t - d)
    if (delta < bestDelta) {
      bestDelta = delta
      best = d
    }
  }
  return best
}

/** Compute loop bounds [start, end) for `size` bars starting at bar i. */
export function loopBounds(
  analysis: Analysis,
  startBar: number,
  size: LoopSize,
): { start: number; end: number } {
  const db = analysis.downbeats
  const bs = barSeconds(analysis.bpm)
  if (!db.length) return { start: 0, end: bs * size }
  const startIdx = Math.max(0, Math.min(startBar, db.length - 1))
  const start = db[startIdx]
  if (size >= 1) {
    const wholeBars = Math.floor(size)
    const endIdx = startIdx + wholeBars
    const end =
      endIdx < db.length
        ? db[endIdx]
        : start + wholeBars * bs
    const fraction = size - wholeBars
    return { start, end: end + fraction * bs }
  }
  // sub-bar: use bar fraction from BPM
  return { start, end: start + size * bs }
}
