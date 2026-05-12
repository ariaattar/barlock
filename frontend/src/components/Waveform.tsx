import { useEffect, useRef } from "react"
import WaveSurfer from "wavesurfer.js"

type Props = {
  audioUrl: string
  beats: number[]
  downbeats: number[]
  duration: number
  /** Current playhead in seconds (controlled by parent). */
  currentTime: number
  loop: { start: number; end: number } | null
  /** Seconds of audio visible across the viewport. */
  windowSec: number
  playing: boolean
  onSeek: (t: number) => void
  onZoom?: (factor: number) => void
}

export function Waveform({
  audioUrl,
  beats,
  downbeats,
  duration,
  currentTime,
  loop,
  windowSec,
  playing,
  onSeek,
  onZoom,
}: Props) {
  const scrollRef = useRef<HTMLDivElement | null>(null)
  const innerRef = useRef<HTMLDivElement | null>(null)
  const overlayRef = useRef<HTMLDivElement | null>(null)
  const cursorRef = useRef<HTMLDivElement | null>(null)
  const wsRef = useRef<WaveSurfer | null>(null)
  const playingRef = useRef(playing)

  useEffect(() => {
    playingRef.current = playing
  }, [playing])

  // Inner width such that `windowSec` of audio spans the viewport width.
  useEffect(() => {
    const inner = innerRef.current
    const scroller = scrollRef.current
    if (!inner || !scroller || !duration) return
    const viewport = scroller.clientWidth
    const totalWidth = (duration / windowSec) * viewport
    inner.style.width = `${Math.max(totalWidth, viewport)}px`
  }, [duration, windowSec])

  // Wavesurfer is display-only here. We render the waveform, but playback
  // and seeking are controlled by the parent (Deck) via the custom player.
  useEffect(() => {
    const inner = innerRef.current
    if (!inner) return
    const ws = WaveSurfer.create({
      container: inner,
      waveColor: "#3b82f6",
      progressColor: "#f59e0b",
      cursorColor: "transparent", // we draw our own cursor
      cursorWidth: 0,
      barWidth: 2,
      barGap: 1,
      barRadius: 1,
      height: 120,
      url: audioUrl,
      normalize: true,
      fillParent: true,
      interact: false,
    })
    wsRef.current = ws
    return () => {
      ws.destroy()
      wsRef.current = null
    }
  }, [audioUrl])

  // Click-to-seek bound on the scroller, capture phase so nothing eats it.
  useEffect(() => {
    const scroller = scrollRef.current
    const inner = innerRef.current
    if (!scroller || !inner || !duration) return
    const onClick = (e: MouseEvent) => {
      const rect = inner.getBoundingClientRect()
      const x = e.clientX - rect.left
      const ratio = Math.max(0, Math.min(1, x / rect.width))
      onSeek(ratio * duration)
    }
    scroller.addEventListener("click", onClick)
    return () => scroller.removeEventListener("click", onClick)
  }, [duration, onSeek])

  // Move the custom cursor + sync wavesurfer's internal progress so the
  // played portion fills with the progress color.
  useEffect(() => {
    const cursor = cursorRef.current
    const inner = innerRef.current
    if (!cursor || !inner || !duration) return
    const ratio = Math.max(0, Math.min(1, currentTime / duration))
    cursor.style.left = `${ratio * 100}%`
    // wavesurfer 7 exposes setTime() which updates display progress but
    // not actual playback (since we're not playing through it).
    const ws = wsRef.current
    if (ws) {
      try {
        ws.setTime(currentTime)
      } catch {
        // not loaded yet
      }
    }
  }, [currentTime, duration])

  // Auto-scroll the viewport so the cursor stays centered while playing.
  useEffect(() => {
    if (!playing) return
    const scroller = scrollRef.current
    const inner = innerRef.current
    if (!scroller || !inner || !duration) return
    let raf = 0
    const tick = () => {
      const ratio = Math.max(0, Math.min(1, currentTimeRef.current / duration))
      const x = ratio * inner.clientWidth
      const target = x - scroller.clientWidth / 2
      scroller.scrollLeft = Math.max(
        0,
        Math.min(inner.clientWidth - scroller.clientWidth, target),
      )
      if (playingRef.current) raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [playing, duration, windowSec])

  // Stash currentTime in a ref for the rAF scroll loop without re-arming it.
  const currentTimeRef = useRef(currentTime)
  useEffect(() => {
    currentTimeRef.current = currentTime
  }, [currentTime])

  // Bar/beat ticks + bar numbers + loop region overlay.
  useEffect(() => {
    const el = overlayRef.current
    if (!el || !duration) return
    el.innerHTML = ""
    for (const t of beats) {
      const x = (t / duration) * 100
      const tick = document.createElement("div")
      tick.className = "absolute top-6 h-3 w-px bg-white/15"
      tick.style.left = `${x}%`
      el.appendChild(tick)
    }
    downbeats.forEach((t, i) => {
      const x = (t / duration) * 100
      const line = document.createElement("div")
      line.className = "absolute top-6 bottom-0 w-px bg-red-400/70"
      line.style.left = `${x}%`
      el.appendChild(line)
      const label = document.createElement("div")
      label.className =
        "absolute top-0 -translate-x-1/2 text-[10px] font-mono text-red-300/80 px-1"
      label.style.left = `${x}%`
      label.textContent = `${i + 1}`
      el.appendChild(label)
    })
    if (loop) {
      const x = (loop.start / duration) * 100
      const w = ((loop.end - loop.start) / duration) * 100
      const region = document.createElement("div")
      region.className =
        "absolute top-6 bottom-0 bg-amber-400/25 border-x-2 border-amber-400 pointer-events-none"
      region.style.left = `${x}%`
      region.style.width = `${w}%`
      el.appendChild(region)
    }
  }, [beats, downbeats, duration, loop])

  // Pinch / Cmd+wheel to zoom.
  useEffect(() => {
    const el = scrollRef.current
    if (!el || !onZoom) return
    const onWheel = (e: WheelEvent) => {
      if (!(e.ctrlKey || e.metaKey)) return
      e.preventDefault()
      const factor = Math.exp(e.deltaY * 0.01)
      onZoom(factor)
    }
    el.addEventListener("wheel", onWheel, { passive: false })
    return () => el.removeEventListener("wheel", onWheel)
  }, [onZoom])

  return (
    <div
      ref={scrollRef}
      className="relative w-full overflow-x-auto overflow-y-hidden rounded-md bg-black/40"
      style={{ scrollbarWidth: "thin" }}
    >
      <div ref={innerRef} className="relative h-[120px]">
        <div
          ref={overlayRef}
          className="pointer-events-none absolute inset-0"
        />
        <div
          ref={cursorRef}
          className="pointer-events-none absolute top-6 bottom-0 w-0.5 bg-amber-200 shadow-[0_0_8px_rgba(254,243,199,0.8)]"
          style={{ left: "0%" }}
        />
      </div>
    </div>
  )
}
