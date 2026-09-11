import type { RekordboxBeat, RekordboxWaveformLane } from "../types"

const THREE_BAND = {
  low: "rgb(32, 83, 217)",
  overlap: "rgb(169, 107, 39)",
  mid: "rgb(242, 170, 60)",
  high: "rgb(255, 255, 255)",
}

export interface WaveformRenderOptions {
  width: number
  height: number
  durationSec: number
  startSec?: number
  endSec?: number
  layout: "mirrored" | "overview"
  topPadding?: number
  bottomPadding?: number
  opacity?: number
}

export interface BeatGridRenderOptions {
  width: number
  height: number
  durationSec: number
  startSec?: number
  endSec?: number
  top?: number
  bottom?: number
  showAllBeats?: boolean
  showBarNumbers?: boolean
}

function pointCount(lane: RekordboxWaveformLane): number {
  return lane.bands?.low?.length || lane.heights.length
}

function dataIndexAtTime(lane: RekordboxWaveformLane, seconds: number, durationSec: number): number {
  const count = pointCount(lane)
  if (!count) return 0
  const sourcePoints = lane.source_points || count
  if (lane.sample_rate_hz) {
    return Math.max(0, Math.min(count, seconds * lane.sample_rate_hz * count / sourcePoints))
  }
  return Math.max(0, Math.min(count, seconds / Math.max(durationSec, .001) * count))
}

function average(values: number[] | undefined, start: number, end: number): number {
  if (!values?.length) return 0
  const first = Math.max(0, Math.min(values.length - 1, Math.floor(start)))
  const last = Math.max(first + 1, Math.min(values.length, Math.ceil(end)))
  let sum = 0
  for (let index = first; index < last; index += 1) sum += values[index] || 0
  return sum / (last - first)
}

function averageScaled(values: number[], start: number, end: number, scale: number): number {
  if (!values.length) return 0
  const first = Math.max(0, Math.min(values.length - 1, Math.floor(start)))
  const last = Math.max(first + 1, Math.min(values.length, Math.ceil(end)))
  let sum = 0
  for (let index = first; index < last; index += 1) sum += Math.round((values[index] || 0) * scale)
  return Math.floor(sum / (last - first))
}

function averagePreviewHeights(
  low: number[],
  mid: number[],
  high: number[],
  start: number,
  end: number,
): [number, number, number] {
  const first = Math.max(0, Math.min(low.length - 1, Math.floor(start)))
  const last = Math.max(first + 1, Math.min(low.length, Math.ceil(end)))
  let lowSum = 0
  let midSum = 0
  let highSum = 0
  for (let index = first; index < last; index += 1) {
    const lowHeight = (low[index] || 0) * .49
    const midHeight = lowHeight + (mid[index] || 0) * .32
    const highHeight = midHeight + (high[index] || 0) * .25
    lowSum += Math.round(lowHeight)
    midSum += Math.round(midHeight)
    highSum += Math.round(highHeight)
  }
  const count = last - first
  return [Math.floor(lowSum / count), Math.floor(midSum / count), Math.floor(highSum / count)]
}

function averageColor(colors: number[][], start: number, end: number, fallback: number[]): number[] {
  if (!colors.length) return fallback
  const first = Math.max(0, Math.min(colors.length - 1, Math.floor(start)))
  const last = Math.max(first + 1, Math.min(colors.length, Math.ceil(end)))
  const result = [0, 0, 0]
  for (let index = first; index < last; index += 1) {
    const color = colors[index] || fallback
    result[0] += color[0] ?? fallback[0]
    result[1] += color[1] ?? fallback[1]
    result[2] += color[2] ?? fallback[2]
  }
  const count = last - first
  return result.map((value) => Math.round(value / count))
}

function drawMirroredLine(context: CanvasRenderingContext2D, x: number, center: number, height: number, color: string) {
  if (height <= 0) return
  context.fillStyle = color
  context.fillRect(x, Math.round(center - height), 1, Math.max(1, Math.round(height * 2) + 1))
}

function threeBandPreviewMaximum(lane: RekordboxWaveformLane): number {
  const low = lane.bands?.low || []
  const mid = lane.bands?.mid || []
  const high = lane.bands?.high || []
  let maximum = 1
  for (let index = 0; index < low.length; index += 1) {
    maximum = Math.max(maximum, Math.round((low[index] || 0) * .49 + (mid[index] || 0) * .32 + (high[index] || 0) * .25))
  }
  return maximum
}

function drawThreeBand(
  context: CanvasRenderingContext2D,
  lane: RekordboxWaveformLane,
  options: Required<WaveformRenderOptions>,
) {
  const low = lane.bands?.low || []
  const mid = lane.bands?.mid || []
  const high = lane.bands?.high || []
  const drawableHeight = Math.max(1, options.height - options.topPadding - options.bottomPadding)
  const center = options.topPadding + drawableHeight / 2
  const previewMaximum = threeBandPreviewMaximum(lane)

  for (let x = 0; x < Math.ceil(options.width); x += 1) {
    const startTime = options.startSec + x / options.width * (options.endSec - options.startSec)
    const endTime = options.startSec + (x + 1) / options.width * (options.endSec - options.startSec)
    const start = dataIndexAtTime(lane, startTime, options.durationSec)
    const end = dataIndexAtTime(lane, endTime, options.durationSec)
    if (options.layout === "overview" || lane.tag === "PWV6") {
      const [lowHeight, midHeight, highHeight] = averagePreviewHeights(low, mid, high, start, end)
      const scale = drawableHeight / previewMaximum
      const baseline = options.height - options.bottomPadding
      context.fillStyle = THREE_BAND.high
      context.fillRect(x, baseline - highHeight * scale, 1, Math.max(1, highHeight * scale))
      context.fillStyle = THREE_BAND.mid
      context.fillRect(x, baseline - midHeight * scale, 1, Math.max(1, midHeight * scale))
      context.fillStyle = THREE_BAND.low
      context.fillRect(x, baseline - lowHeight * scale, 1, Math.max(1, lowHeight * scale))
      continue
    }

    const scale = drawableHeight / 2 / 51
    const lowHeight = averageScaled(low, start, end, .4) * scale
    const midHeight = averageScaled(mid, start, end, .3) * scale
    const highHeight = averageScaled(high, start, end, .06) * scale
    if (lowHeight > midHeight) {
      drawMirroredLine(context, x, center, lowHeight, THREE_BAND.low)
      drawMirroredLine(context, x, center, midHeight, THREE_BAND.overlap)
    } else {
      if (lowHeight !== midHeight) drawMirroredLine(context, x, center, midHeight, THREE_BAND.mid)
      drawMirroredLine(context, x, center, lowHeight, THREE_BAND.overlap)
    }
    drawMirroredLine(context, x, center, highHeight, THREE_BAND.high)
  }
}

function drawLegacy(
  context: CanvasRenderingContext2D,
  lane: RekordboxWaveformLane,
  options: Required<WaveformRenderOptions>,
) {
  const drawableHeight = Math.max(1, options.height - options.topPadding - options.bottomPadding)
  const center = options.topPadding + drawableHeight / 2
  const baseline = options.height - options.bottomPadding
  const fallback = [56, 168, 232]
  for (let x = 0; x < Math.ceil(options.width); x += 1) {
    const startTime = options.startSec + x / options.width * (options.endSec - options.startSec)
    const endTime = options.startSec + (x + 1) / options.width * (options.endSec - options.startSec)
    const start = dataIndexAtTime(lane, startTime, options.durationSec)
    const end = dataIndexAtTime(lane, endTime, options.durationSec)
    const height = average(lane.heights, start, end)
    const color = averageColor(lane.colors, start, end, fallback)
    const bar = Math.max(1, height * drawableHeight)

    if (options.layout === "overview") {
      if (lane.back_heights?.length) {
        const backHeight = Math.max(1, average(lane.back_heights, start, end) * drawableHeight)
        const backColor = averageColor(lane.back_colors || [], start, end, fallback)
        context.fillStyle = `rgb(${backColor.join(",")})`
        context.fillRect(x, baseline - backHeight, 1, backHeight)
      }
      context.fillStyle = `rgb(${color.join(",")})`
      context.fillRect(x, baseline - bar, 1, bar)
    } else {
      context.fillStyle = `rgb(${color.join(",")})`
      context.fillRect(x, center - bar / 2, 1, bar)
    }
  }
}

export function drawRekordboxWaveform(
  context: CanvasRenderingContext2D,
  lane: RekordboxWaveformLane,
  options: WaveformRenderOptions,
) {
  const resolved: Required<WaveformRenderOptions> = {
    ...options,
    startSec: options.startSec ?? 0,
    endSec: options.endSec ?? options.durationSec,
    topPadding: options.topPadding ?? 0,
    bottomPadding: options.bottomPadding ?? 0,
    opacity: options.opacity ?? 1,
  }
  context.save()
  context.globalAlpha = resolved.opacity
  if (lane.style === "three_band" && lane.bands?.low?.length) drawThreeBand(context, lane, resolved)
  else drawLegacy(context, lane, resolved)
  context.restore()
}

function drawDownbeatTriangle(
  context: CanvasRenderingContext2D,
  x: number,
  edge: number,
  size: number,
  pointsDown: boolean,
) {
  context.beginPath()
  context.moveTo(x - size, edge)
  context.lineTo(x + size, edge)
  context.lineTo(x, edge + (pointsDown ? size : -size))
  context.closePath()
  context.fill()
}

export function drawRekordboxBeatGrid(
  context: CanvasRenderingContext2D,
  beats: RekordboxBeat[],
  options: BeatGridRenderOptions,
) {
  const startSec = options.startSec ?? 0
  const endSec = options.endSec ?? options.durationSec
  const visibleDuration = Math.max(.001, endSec - startSec)
  const top = options.top ?? 0
  const bottom = options.bottom ?? options.height
  const visible = beats.filter((beat) => beat.time_sec >= startSec && beat.time_sec <= endSec)
  const downbeats = visible.filter((beat) => beat.beat_number === 1)
  const downbeatSpacing = downbeats.length > 1
    ? (downbeats.at(-1)!.time_sec - downbeats[0].time_sec) / (downbeats.length - 1) / visibleDuration * options.width
    : options.width
  const drawTriangles = downbeatSpacing >= 7
  const triangleSize = Math.max(3, Math.min(6, downbeatSpacing * .28))

  for (const beat of visible) {
    if (!options.showAllBeats && beat.beat_number !== 1) continue
    const x = (beat.time_sec - startSec) / visibleDuration * options.width
    context.fillStyle = beat.beat_number === 1 ? "rgba(255,255,255,.34)" : "rgba(255,255,255,.11)"
    context.fillRect(Math.round(x), top, 1, Math.max(1, bottom - top))
  }

  if (!drawTriangles) return
  context.fillStyle = "rgb(255, 35, 52)"
  for (const beat of downbeats) {
    const x = (beat.time_sec - startSec) / visibleDuration * options.width
    drawDownbeatTriangle(context, x, top, triangleSize, true)
    drawDownbeatTriangle(context, x, bottom, triangleSize, false)
    if (options.showBarNumbers) {
      context.fillStyle = "rgba(255,255,255,.68)"
      context.font = "500 9px Geist Mono, monospace"
      context.textAlign = "left"
      context.fillText("1", x + triangleSize + 2, bottom - triangleSize - 2)
      context.fillStyle = "rgb(255, 35, 52)"
    }
  }
}

export function waveformFormatLabel(lane: RekordboxWaveformLane | null | undefined): string {
  if (!lane) return "Unavailable"
  if (lane.style === "three_band") return `${lane.tag} · Native 3-band${lane.sample_rate_hz ? ` · ${lane.sample_rate_hz} Hz` : ""}`
  if (lane.style === "rgb") return `${lane.tag} · Native RGB`
  return `${lane.tag} · Native blue`
}
