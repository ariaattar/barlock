import { useCallback, useEffect, useRef, useState } from "react"
import {
  Play,
  Pause,
  Repeat,
  Upload,
  X,
  Plus,
  Minus,
  ZoomIn,
  ZoomOut,
  Link2,
} from "lucide-react"

import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Toggle } from "@/components/ui/toggle"
import { Waveform } from "@/components/Waveform"
import { HotCuePads } from "@/components/HotCuePads"
import {
  analyzePath,
  analyzeUpload,
  audioUrl,
  getCues,
  putCues,
  type Analysis,
  type Cue,
} from "@/lib/api"
import {
  LOOP_SIZES,
  type LoopSize,
  barIndexAtTime,
  loopBounds,
  snapToDownbeat,
} from "@/lib/bars"
import { WebAudioPlayer } from "@/lib/player"
import { deckRegistry } from "@/lib/decks"
import { cn } from "@/lib/utils"

type Props = {
  label: "A" | "B"
}

const ZOOM_LEVELS = [128, 96, 64, 48, 32, 24, 16, 12, 8, 6, 4, 3, 2] as const
const DEFAULT_ZOOM_IDX = 6 // 16s window

export function Deck({ label }: Props) {
  const [analysis, setAnalysis] = useState<Analysis | null>(null)
  const [cues, setCues] = useState<Cue[]>([])
  const [loadingMessage, setLoadingMessage] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [playing, setPlaying] = useState(false)
  const [currentTime, setCurrentTime] = useState(0)
  const [loopActive, setLoopActive] = useState(false)
  const [loopRegion, setLoopRegion] = useState<{ start: number; end: number } | null>(null)
  const [loopSizeIdx, setLoopSizeIdx] = useState(4) // 4 bars
  const [zoomIdx, setZoomIdx] = useState(DEFAULT_ZOOM_IDX)
  const playerRef = useRef<WebAudioPlayer | null>(null)

  const loopSize: LoopSize = LOOP_SIZES[loopSizeIdx]
  const windowSec = ZOOM_LEVELS[zoomIdx]

  // Create a player on first mount, destroy on unmount. Register with the
  // shared deck registry so the other deck can read our state for sync.
  useEffect(() => {
    const p = new WebAudioPlayer()
    playerRef.current = p
    deckRegistry[label].player = p
    return () => {
      p.destroy()
      playerRef.current = null
      deckRegistry[label].player = null
      deckRegistry[label].analysis = null
    }
  }, [label])

  useEffect(() => {
    deckRegistry[label].analysis = analysis
  }, [label, analysis])

  // Tight rAF loop just to surface the player's currentTime to the UI.
  // The player itself doesn't need the rAF to play correctly — this is only
  // for cursor / time display updates.
  useEffect(() => {
    let raf = 0
    const tick = () => {
      const p = playerRef.current
      if (p) {
        setCurrentTime(p.currentTime)
        if (p.isPlaying !== playing) setPlaying(p.isPlaying)
      }
      raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [playing])

  const ingestAnalysis = useCallback(async (a: Analysis) => {
    setLoadingMessage("Decoding audio for seamless playback…")
    await playerRef.current?.load(audioUrl(a.track_id))
    setAnalysis(a)
    const c = await getCues(a.track_id)
    setCues(c.cues)
  }, [])

  const handleFile = useCallback(async (file: File) => {
    setError(null)
    setLoadingMessage("Analyzing — finding bars & downbeats…")
    try {
      const a = await analyzeUpload(file)
      await ingestAnalysis(a)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoadingMessage(null)
    }
  }, [ingestAnalysis])

  const loadPath = useCallback(
    async (path: string) => {
      setError(null)
      setLoadingMessage("Analyzing — finding bars & downbeats…")
      try {
        const a = await analyzePath(path)
        await ingestAnalysis(a)
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e))
      } finally {
        setLoadingMessage(null)
      }
    },
    [ingestAnalysis],
  )

  // Expose loadPath via the shared deck registry so the sidebar can call it.
  useEffect(() => {
    deckRegistry[label].loadPath = loadPath
    return () => {
      deckRegistry[label].loadPath = undefined
    }
  }, [label, loadPath])

  const togglePlay = useCallback(() => {
    const p = playerRef.current
    if (!p) return
    if (p.isPlaying) p.pause()
    else p.play()
  }, [])

  const toggleLoop = useCallback(() => {
    const p = playerRef.current
    if (!analysis || !p) return
    if (loopActive) {
      p.setLoop(null)
      setLoopActive(false)
      setLoopRegion(null)
      return
    }
    // Loop starts at the bar CONTAINING the playhead (Rekordbox semantics).
    // The loop in/out points snap to detected downbeats, not BPM-derived
    // seconds, so they line up with the beatgrid you see on the waveform.
    const t = p.currentTime
    const startBar = barIndexAtTime(analysis.downbeats, t)
    const region = loopBounds(analysis, startBar, loopSize)
    p.setLoop(region)
    setLoopRegion(region)
    setLoopActive(true)
  }, [analysis, loopActive, loopSize])

  // When loop size changes while the loop is active, recompute around the
  // active start bar and push to the player live — the wrap point updates
  // mid-loop just like Rekordbox.
  useEffect(() => {
    if (!loopActive || !loopRegion || !analysis) return
    const startBar = barIndexAtTime(analysis.downbeats, loopRegion.start)
    const next = loopBounds(analysis, startBar, loopSize)
    setLoopRegion(next)
    playerRef.current?.setLoop(next)
  }, [loopSizeIdx]) // eslint-disable-line react-hooks/exhaustive-deps

  const handleSeek = useCallback((t: number) => {
    playerRef.current?.setTime(t)
  }, [])

  const onSetCue = useCallback(
    async (slot: number) => {
      const p = playerRef.current
      if (!analysis || !p) return
      const snapped = snapToDownbeat(analysis.downbeats, p.currentTime)
      const next: Cue = {
        slot,
        name: `Cue ${"ABCDEFGH"[slot]}`,
        position_sec: snapped,
        color: defaultColor(slot),
        type: "hot",
        end_sec: null,
      }
      const updated = [...cues.filter((c) => c.slot !== slot), next]
      setCues(updated)
      await putCues(analysis.track_id, updated)
    },
    [analysis, cues],
  )

  const onJumpCue = useCallback(
    (slot: number) => {
      const cue = cues.find((c) => c.slot === slot)
      if (cue) playerRef.current?.setTime(cue.position_sec)
    },
    [cues],
  )

  const onClearCue = useCallback(
    async (slot: number) => {
      if (!analysis) return
      const updated = cues.filter((c) => c.slot !== slot)
      setCues(updated)
      await putCues(analysis.track_id, updated)
    },
    [analysis, cues],
  )

  const sync = useCallback(() => {
    const me = playerRef.current
    if (!me || !analysis) return
    const other = label === "A" ? deckRegistry.B : deckRegistry.A
    if (!other.analysis || !other.player) return

    // 1) Match tempo: our effective BPM = analysis.bpm * rate. Set our rate
    //    so that effective BPM equals the other deck's effective BPM.
    const otherEffectiveBpm = other.analysis.bpm * other.player.playbackRate
    const targetRate = otherEffectiveBpm / analysis.bpm
    me.setPlaybackRate(targetRate)

    // 2) Match phase: line up our NEXT downbeat with the other deck's NEXT
    //    downbeat in audio-context time. We jump our playhead so that the
    //    most recent downbeat in our buffer is the same wall-clock offset
    //    from the other deck's current bar position.
    const otherT = other.player.currentTime
    const otherDb = other.analysis.downbeats
    const otherIdx = nearestIndex(otherDb, otherT)
    const otherBarStart = otherDb[otherIdx] ?? 0
    const otherBarEnd = otherDb[otherIdx + 1] ?? otherBarStart + barLength(other.analysis)
    const otherPhase = (otherT - otherBarStart) / (otherBarEnd - otherBarStart)

    // Find the bar in OUR track closest to our current playhead, then jump
    // to the same phase within that bar.
    const myT = me.currentTime
    const myDb = analysis.downbeats
    const myIdx = nearestIndex(myDb, myT)
    const myBarStart = myDb[myIdx] ?? 0
    const myBarEnd = myDb[myIdx + 1] ?? myBarStart + barLength(analysis)
    const myTarget = myBarStart + otherPhase * (myBarEnd - myBarStart)
    me.setTime(myTarget)
  }, [analysis, label])

  const eject = () => {
    const p = playerRef.current
    if (p) {
      p.pause()
      p.setLoop(null)
    }
    setAnalysis(null)
    setCues([])
    setPlaying(false)
    setLoopActive(false)
    setLoopRegion(null)
    setCurrentTime(0)
  }

  return (
    <Card className="flex flex-col">
      <CardHeader className="flex flex-row items-center justify-between gap-2 pb-2">
        <CardTitle className="flex items-center gap-2 text-base">
          <Badge variant="outline" className="text-base">
            Deck {label}
          </Badge>
          {analysis && (
            <span className="truncate text-sm font-normal text-muted-foreground">
              {analysis.filename}
            </span>
          )}
        </CardTitle>
        {analysis && (
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            <Badge variant="secondary">{analysis.bpm.toFixed(1)} BPM</Badge>
            <Badge variant="secondary">{analysis.time_signature}</Badge>
            <Badge variant="secondary">{analysis.downbeats.length} bars</Badge>
          </div>
        )}
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        {!analysis && (
          <FileDrop
            onFile={handleFile}
            loading={loadingMessage}
            error={error}
          />
        )}
        {analysis && (
          <>
            <Waveform
              audioUrl={audioUrl(analysis.track_id)}
              beats={analysis.beats}
              downbeats={analysis.downbeats}
              duration={analysis.duration_sec}
              currentTime={currentTime}
              loop={loopActive && loopRegion ? loopRegion : null}
              windowSec={windowSec}
              playing={playing}
              onSeek={handleSeek}
              onZoom={(factor) => {
                setZoomIdx((i) => {
                  const delta = factor < 1 ? 1 : -1
                  return Math.max(
                    0,
                    Math.min(ZOOM_LEVELS.length - 1, i + delta),
                  )
                })
              }}
            />
            <div className="flex flex-wrap items-center gap-2">
              <Button size="sm" onClick={togglePlay}>
                {playing ? (
                  <Pause className="size-4" />
                ) : (
                  <Play className="size-4" />
                )}
                {playing ? "Pause" : "Play"}
              </Button>
              <Toggle
                size="sm"
                pressed={loopActive}
                onClick={toggleLoop}
                variant="outline"
              >
                <Repeat className="size-4" />
                Loop {formatSize(loopSize)}
              </Toggle>
              <Button
                size="sm"
                variant="outline"
                onClick={sync}
                title={`Sync tempo & phase to Deck ${label === "A" ? "B" : "A"}`}
              >
                <Link2 className="size-4" />
                Sync
              </Button>
              <div className="flex items-center gap-1">
                <Button
                  size="icon"
                  variant="outline"
                  className="size-8"
                  disabled={loopSizeIdx === 0}
                  onClick={() => setLoopSizeIdx((i) => Math.max(0, i - 1))}
                  title="Halve loop length"
                >
                  <Minus className="size-3" />
                </Button>
                <Button
                  size="icon"
                  variant="outline"
                  className="size-8"
                  disabled={loopSizeIdx === LOOP_SIZES.length - 1}
                  onClick={() =>
                    setLoopSizeIdx((i) =>
                      Math.min(LOOP_SIZES.length - 1, i + 1),
                    )
                  }
                  title="Double loop length"
                >
                  <Plus className="size-3" />
                </Button>
              </div>
              <div className="ml-3 flex items-center gap-1 border-l border-border/60 pl-3">
                <Button
                  size="icon"
                  variant="outline"
                  className="size-8"
                  disabled={zoomIdx === 0}
                  onClick={() => setZoomIdx((i) => Math.max(0, i - 1))}
                  title="Zoom out"
                >
                  <ZoomOut className="size-3" />
                </Button>
                <span className="font-mono text-[10px] tabular-nums text-muted-foreground">
                  {windowSec}s
                </span>
                <Button
                  size="icon"
                  variant="outline"
                  className="size-8"
                  disabled={zoomIdx === ZOOM_LEVELS.length - 1}
                  onClick={() =>
                    setZoomIdx((i) =>
                      Math.min(ZOOM_LEVELS.length - 1, i + 1),
                    )
                  }
                  title="Zoom in"
                >
                  <ZoomIn className="size-3" />
                </Button>
              </div>
              <span className="ml-auto font-mono text-xs text-muted-foreground">
                {fmtTime(currentTime)} / {fmtTime(analysis.duration_sec)}
              </span>
              <Button size="icon" variant="ghost" onClick={eject} title="Eject">
                <X className="size-4" />
              </Button>
            </div>
            <HotCuePads
              cues={cues}
              onSetCue={onSetCue}
              onJumpCue={onJumpCue}
              onClearCue={onClearCue}
            />
          </>
        )}
      </CardContent>
    </Card>
  )
}

function FileDrop({
  onFile,
  loading,
  error,
}: {
  onFile: (f: File) => void
  loading: string | null
  error: string | null
}) {
  const inputRef = useRef<HTMLInputElement | null>(null)
  const [drag, setDrag] = useState(false)
  return (
    <div
      onDragOver={(e) => {
        e.preventDefault()
        setDrag(true)
      }}
      onDragLeave={() => setDrag(false)}
      onDrop={(e) => {
        e.preventDefault()
        setDrag(false)
        const f = e.dataTransfer.files[0]
        if (f) onFile(f)
      }}
      className={cn(
        "flex h-44 flex-col items-center justify-center gap-3 rounded-md border-2 border-dashed transition-colors",
        drag ? "border-primary bg-primary/5" : "border-border",
      )}
    >
      <Upload className="size-6 text-muted-foreground" />
      <div className="text-sm text-muted-foreground">
        {loading ? loading : "Drop an audio file, or"}
      </div>
      <Button
        size="sm"
        variant="outline"
        disabled={!!loading}
        onClick={() => inputRef.current?.click()}
      >
        Choose file
      </Button>
      <input
        type="file"
        ref={inputRef}
        accept="audio/*"
        className="hidden"
        onChange={(e) => {
          const f = e.target.files?.[0]
          if (f) onFile(f)
        }}
      />
      {error && <div className="text-xs text-destructive">{error}</div>}
    </div>
  )
}

function nearestIndex(arr: number[], t: number): number {
  if (!arr.length) return 0
  let lo = 0
  let hi = arr.length - 1
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1
    if (arr[mid] <= t) lo = mid
    else hi = mid - 1
  }
  return lo
}

function barLength(a: Analysis): number {
  return a.bpm > 0 ? (60 / a.bpm) * 4 : 2
}

function defaultColor(slot: number): [number, number, number] {
  const colors: Array<[number, number, number]> = [
    [40, 226, 20], [80, 180, 255], [255, 100, 0], [255, 64, 64],
    [180, 100, 255], [255, 230, 0], [255, 0, 200], [0, 230, 200],
  ]
  return colors[slot % colors.length]
}

function formatSize(s: LoopSize): string {
  if (s === 0.25) return "¼ bar"
  if (s === 0.5) return "½ bar"
  return `${s} bar${s === 1 ? "" : "s"}`
}

function fmtTime(s: number) {
  const m = Math.floor(s / 60)
  const sec = Math.floor(s % 60)
  return `${m}:${sec.toString().padStart(2, "0")}`
}
