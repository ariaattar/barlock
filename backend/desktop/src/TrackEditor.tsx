import {
  AlertTriangle,
  ArrowLeft,
  Check,
  ChevronDown,
  Disc3,
  Gauge,
  Grid2X2,
  LoaderCircle,
  Lock,
  Minus,
  Music2,
  Pause,
  Play,
  Plus,
  Repeat2,
  RotateCcw,
  Save,
  ShieldCheck,
  SkipBack,
  SlidersHorizontal,
  Unlock,
  ZoomIn,
} from "lucide-react"
import { useCallback, useEffect, useRef, useState } from "react"
import WaveSurfer from "wavesurfer.js"
import { finalMessage, registerAudio, releaseAudio, runBridge } from "./lib/bridge"
import { drawRekordboxBeatGrid, drawRekordboxWaveform, waveformFormatLabel } from "./lib/rekordboxWaveform"
import type {
  EditorApplyPreview,
  EditorAudition,
  EditorCorrections,
  EditorCuePoint,
  EditorMarker,
  EditorMarkerRole,
  EditorState,
  RekordboxBeat,
  RekordboxWaveformLane,
} from "./types"

export interface EditorTarget {
  contentId?: string
  path?: string
  title: string
  artist?: string
}

interface TrackEditorProps {
  target: EditorTarget
  rekordboxRunning: boolean | null
  onClose: () => void
  onRekordboxStatus: (running: boolean) => void
  onToast: (message: string, tone?: "success" | "danger" | "neutral") => void
}

const CAMELOT_KEYS = Array.from({ length: 12 }, (_, index) => [`${index + 1}A`, `${index + 1}B`]).flat()
const CAMELOT_TO_KEY: Record<string, string> = {
  "1A": "Abm", "1B": "B", "2A": "Ebm", "2B": "F#", "3A": "Bbm", "3B": "Db",
  "4A": "Fm", "4B": "Ab", "5A": "Cm", "5B": "Eb", "6A": "Gm", "6B": "Bb",
  "7A": "Dm", "7B": "F", "8A": "Am", "8B": "C", "9A": "Em", "9B": "G",
  "10A": "Bm", "10B": "D", "11A": "F#m", "11B": "A", "12A": "Dbm", "12B": "E",
}
const MARKER_COLORS: Record<EditorMarkerRole, string> = {
  intro: "#f33c82",
  phrase16: "#38a8e8",
  phrase32: "#83cc45",
  intro_loop: "#f19a23",
  exit_loop: "#f5b942",
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" ? value as Record<string, unknown> : {}
}

function formatTime(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return "0:00.000"
  const minutes = Math.floor(seconds / 60)
  return `${minutes}:${(seconds - minutes * 60).toFixed(3).padStart(6, "0")}`
}

function exactLoopBuffer(
  context: AudioContext,
  source: AudioBuffer,
  startSeconds: number,
  endSeconds: number,
): AudioBuffer {
  const startFrame = Math.max(0, Math.min(source.length - 1, Math.round(startSeconds * source.sampleRate)))
  const endFrame = Math.max(startFrame + 1, Math.min(source.length, Math.round(endSeconds * source.sampleRate)))
  const loop = context.createBuffer(source.numberOfChannels, endFrame - startFrame, source.sampleRate)
  for (let channel = 0; channel < source.numberOfChannels; channel += 1) {
    loop.getChannelData(channel).set(source.getChannelData(channel).subarray(startFrame, endFrame))
  }
  return loop
}

function buildGrid(duration: number, bpm: number, firstDownbeat: number): RekordboxBeat[] {
  if (duration <= 0 || bpm <= 0) return []
  const beat = 60 / bpm
  const preceding = Math.floor(Math.max(0, firstDownbeat) / beat)
  const start = Math.max(0, firstDownbeat) - preceding * beat
  const startNumber = ((-preceding % 4) + 4) % 4 + 1
  const grid: RekordboxBeat[] = []
  for (let index = 0, time = start; time <= duration + 0.001; index += 1, time = start + index * beat) {
    grid.push({ beat_number: (startNumber - 1 + index) % 4 + 1, bpm, time_sec: Number(time.toFixed(3)) })
  }
  return grid
}

function snapTime(value: number, grid: RekordboxBeat[], anyBeat: boolean): number {
  const candidates = anyBeat ? grid : grid.filter((beat) => beat.beat_number === 1)
  if (!candidates.length) return Math.max(0, value)
  return candidates.reduce((best, beat) => Math.abs(beat.time_sec - value) < Math.abs(best.time_sec - value) ? beat : best).time_sec
}

function normalizeCorrections(current: EditorCorrections, duration: number): EditorCorrections {
  const grid = current.write_grid ? buildGrid(duration, current.bpm, current.first_downbeat_sec) : current.grid
  const markers = current.markers.map((marker) => {
    const seconds = snapTime(marker.seconds, grid, marker.snap_mode === "beat")
    if (marker.kind !== "loop") return { ...marker, seconds }
    const startIndex = grid.reduce((best, beat, index) => Math.abs(beat.time_sec - seconds) < Math.abs(grid[best].time_sec - seconds) ? index : best, 0)
    const loopBeats = marker.loop_beats === 4 ? 4 : 8
    const safeStart = Math.min(startIndex, Math.max(0, grid.length - loopBeats - 1))
    return { ...marker, seconds: grid[safeStart]?.time_sec ?? seconds, end_seconds: grid[safeStart + loopBeats]?.time_sec ?? marker.end_seconds, loop_beats: loopBeats as 4 | 8 }
  })
  return { ...current, grid, markers }
}

function EditorButton({
  icon: Icon,
  children,
  primary = false,
  danger = false,
  loading = false,
  ...props
}: {
  icon?: typeof Play
  children?: React.ReactNode
  primary?: boolean
  danger?: boolean
  loading?: boolean
} & React.ButtonHTMLAttributes<HTMLButtonElement>) {
  const CurrentIcon = loading ? LoaderCircle : Icon
  return <button className={`editor-button ${primary ? "primary" : ""} ${danger ? "danger" : ""}`} {...props} disabled={props.disabled || loading}>
    {CurrentIcon ? <CurrentIcon size={15} className={loading ? "spin" : ""} aria-hidden="true" /> : null}
    {children ? <span>{children}</span> : null}
  </button>
}

function WaveOverview({ lane, playhead, duration, viewportStart, viewportDuration, onSeek }: {
  lane: RekordboxWaveformLane
  playhead: number
  duration: number
  viewportStart: number
  viewportDuration: number
  onSeek: (seconds: number) => void
}) {
  const ref = useRef<HTMLCanvasElement>(null)
  useEffect(() => {
    const canvas = ref.current
    if (!canvas) return
    const draw = () => {
      const rect = canvas.getBoundingClientRect()
      const ratio = window.devicePixelRatio || 1
      canvas.width = Math.max(1, Math.round(rect.width * ratio))
      canvas.height = Math.max(1, Math.round(rect.height * ratio))
      const context = canvas.getContext("2d")
      if (!context) return
      context.setTransform(ratio, 0, 0, ratio, 0, 0)
      context.clearRect(0, 0, rect.width, rect.height)
      drawRekordboxWaveform(context, lane, {
        width: rect.width,
        height: rect.height,
        durationSec: duration,
        layout: "overview",
        topPadding: 3,
        bottomPadding: 3,
      })
      context.fillStyle = "rgba(255,255,255,.08)"
      context.fillRect(0, 0, rect.width, rect.height)
      const left = duration ? viewportStart / duration * rect.width : 0
      const width = duration ? viewportDuration / duration * rect.width : rect.width
      context.fillStyle = "rgba(82,168,255,.12)"
      context.fillRect(left, 0, width, rect.height)
      context.strokeStyle = "rgba(82,168,255,.72)"
      context.strokeRect(left + .5, .5, Math.max(1, width - 1), rect.height - 1)
      context.fillStyle = "#fff"
      context.fillRect(duration ? playhead / duration * rect.width : 0, 0, 1, rect.height)
    }
    draw()
    const observer = new ResizeObserver(draw)
    observer.observe(canvas)
    return () => observer.disconnect()
  }, [duration, lane, playhead, viewportDuration, viewportStart])
  return <canvas ref={ref} className="editor-overview-canvas" aria-label="Track waveform overview" onPointerDown={(event) => {
    const rect = event.currentTarget.getBoundingClientRect()
    onSeek(Math.max(0, Math.min(duration, (event.clientX - rect.left) / rect.width * duration)))
  }} />
}

function EditableWaveform({
  lane,
  duration,
  grid,
  markers,
  playhead,
  zoom,
  scroll,
  activeRole,
  gridEditable,
  firstDownbeat,
  onSeek,
  onMarker,
  onFirstDownbeat,
  onActiveRole,
  onPan,
}: {
  lane: RekordboxWaveformLane
  duration: number
  grid: RekordboxBeat[]
  markers: EditorMarker[]
  playhead: number
  zoom: number
  scroll: number
  activeRole: EditorMarkerRole
  gridEditable: boolean
  firstDownbeat: number
  onSeek: (seconds: number) => void
  onMarker: (role: EditorMarkerRole, seconds: number, anyBeat: boolean) => void
  onFirstDownbeat: (seconds: number) => void
  onActiveRole: (role: EditorMarkerRole) => void
  onPan: (value: number) => void
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const stageRef = useRef<HTMLDivElement>(null)
  const visibleDuration = duration / zoom
  const visibleStart = Math.max(0, Math.min(duration - visibleDuration, scroll * Math.max(0, duration - visibleDuration)))
  const visibleEnd = visibleStart + visibleDuration

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const draw = () => {
      const rect = canvas.getBoundingClientRect()
      if (!rect.width || !rect.height) return
      const ratio = window.devicePixelRatio || 1
      canvas.width = Math.round(rect.width * ratio)
      canvas.height = Math.round(rect.height * ratio)
      const context = canvas.getContext("2d")
      if (!context) return
      context.setTransform(ratio, 0, 0, ratio, 0, 0)
      context.clearRect(0, 0, rect.width, rect.height)
      drawRekordboxWaveform(context, lane, {
        width: rect.width,
        height: rect.height,
        durationSec: duration,
        startSec: visibleStart,
        endSec: visibleEnd,
        layout: "mirrored",
        topPadding: 22,
        bottomPadding: 4,
        opacity: .94,
      })
      drawRekordboxBeatGrid(context, grid, {
        width: rect.width,
        height: rect.height,
        durationSec: duration,
        startSec: visibleStart,
        endSec: visibleEnd,
        top: 22,
        bottom: rect.height - 4,
        showAllBeats: true,
        showBarNumbers: zoom >= 3,
      })
      markers.filter((marker) => marker.kind === "loop" && marker.end_seconds !== null).forEach((marker) => {
        const start = Math.max(marker.seconds, visibleStart)
        const end = Math.min(marker.end_seconds ?? marker.seconds, visibleEnd)
        if (end <= start) return
        const x = (start - visibleStart) / visibleDuration * rect.width
        const width = (end - start) / visibleDuration * rect.width
        context.fillStyle = `${MARKER_COLORS[marker.role]}1f`
        context.fillRect(x, 22, width, rect.height - 26)
        context.strokeStyle = `${MARKER_COLORS[marker.role]}cc`
        context.strokeRect(x + .5, 22.5, Math.max(1, width - 1), rect.height - 27)
      })
      markers.forEach((marker) => {
        if (marker.seconds < visibleStart || marker.seconds > visibleEnd) return
        const x = (marker.seconds - visibleStart) / visibleDuration * rect.width
        context.fillStyle = MARKER_COLORS[marker.role]
        context.fillRect(Math.round(x), 22, 1, rect.height - 22)
      })
      if (playhead >= visibleStart && playhead <= visibleEnd) {
        const x = (playhead - visibleStart) / visibleDuration * rect.width
        context.fillStyle = "#fff"
        context.fillRect(Math.round(x), 0, 1, rect.height)
      }
    }
    draw()
    const observer = new ResizeObserver(draw)
    observer.observe(canvas)
    return () => observer.disconnect()
  }, [duration, grid, lane, markers, playhead, visibleDuration, visibleEnd, visibleStart, zoom])

  const beginMarkerDrag = (event: React.PointerEvent, marker: EditorMarker) => {
    event.preventDefault()
    event.stopPropagation()
    onActiveRole(marker.role)
    const stage = stageRef.current
    if (!stage) return
    const move = (pointer: PointerEvent) => {
      const rect = stage.getBoundingClientRect()
      const raw = visibleStart + Math.max(0, Math.min(1, (pointer.clientX - rect.left) / rect.width)) * visibleDuration
      onMarker(marker.role, raw, pointer.altKey)
    }
    const up = () => {
      window.removeEventListener("pointermove", move)
      window.removeEventListener("pointerup", up)
    }
    window.addEventListener("pointermove", move)
    window.addEventListener("pointerup", up, { once: true })
  }

  const beginGridDrag = (event: React.PointerEvent) => {
    event.preventDefault()
    event.stopPropagation()
    const stage = stageRef.current
    if (!stage) return
    const move = (pointer: PointerEvent) => {
      const rect = stage.getBoundingClientRect()
      const raw = visibleStart + Math.max(0, Math.min(1, (pointer.clientX - rect.left) / rect.width)) * visibleDuration
      onFirstDownbeat(raw)
    }
    const up = () => {
      window.removeEventListener("pointermove", move)
      window.removeEventListener("pointerup", up)
    }
    window.addEventListener("pointermove", move)
    window.addEventListener("pointerup", up, { once: true })
  }

  return <div ref={stageRef} className="editor-wave-stage" onWheel={(event) => {
    if (zoom <= 1) return
    event.preventDefault()
    onPan(Math.max(0, Math.min(1, scroll + (event.deltaX || event.deltaY) / 1400)))
  }} onPointerDown={(event) => {
    if (event.target !== event.currentTarget && event.target !== canvasRef.current) return
    const rect = event.currentTarget.getBoundingClientRect()
    onSeek(visibleStart + (event.clientX - rect.left) / rect.width * visibleDuration)
  }}>
    <canvas ref={canvasRef} aria-label="Editable Rekordbox waveform" />
    {gridEditable && firstDownbeat >= visibleStart && firstDownbeat <= visibleEnd ? <button
      className="grid-anchor"
      style={{ left: `clamp(20px, ${(firstDownbeat - visibleStart) / visibleDuration * 100}%, calc(100% - 20px))` }}
      aria-label="Move first downbeat"
      title={`First downbeat · ${formatTime(firstDownbeat)}`}
      onPointerDown={beginGridDrag}
    ><span>1</span></button> : null}
    {markers.map((marker) => {
      if (marker.seconds < visibleStart || marker.seconds > visibleEnd) return null
      const left = (marker.seconds - visibleStart) / visibleDuration * 100
      return <button
        key={marker.role}
        className={`editor-marker ${activeRole === marker.role ? "active" : ""} ${marker.conflict ? "conflict" : ""}`}
        style={{ left: `clamp(20px, ${left}%, calc(100% - 20px))`, "--marker-color": MARKER_COLORS[marker.role] } as React.CSSProperties}
        aria-label={`Move ${marker.name}`}
        title={`${marker.name} · ${formatTime(marker.seconds)}`}
        onPointerDown={(event) => beginMarkerDrag(event, marker)}
      ><span>{marker.pad}</span></button>
    })}
  </div>
}

function CueOwnershipRow({ cue, onAdopt, busy }: { cue: EditorCuePoint; onAdopt: (cue: EditorCuePoint) => void; busy: boolean }) {
  return <div className="editor-existing-cue">
    <span className="cue-pad" style={{ background: cue.role ? MARKER_COLORS[cue.role] : "var(--text-tertiary)" }}>{cue.kind === 0 ? "M" : cue.kind <= 3 ? String.fromCharCode(64 + cue.kind) : cue.kind === 5 ? "D" : cue.kind === 6 ? "E" : "?"}</span>
    <span><strong>{cue.name || "Unnamed cue"}</strong><small className="mono">{formatTime(cue.in_sec)}</small></span>
    <span className={`ownership-state ${cue.ownership}`}>
      {cue.ownership === "app" ? <Unlock size={12} /> : <Lock size={12} />}
      {cue.ownership === "app" ? "Editable" : cue.ownership === "legacy_candidate" ? "Unverified" : "Manual"}
    </span>
    {cue.ownership === "legacy_candidate" ? <button disabled={busy} onClick={() => onAdopt(cue)}>Adopt</button> : null}
  </div>
}

export function TrackEditor({ target, rekordboxRunning, onClose, onRekordboxStatus, onToast }: TrackEditorProps) {
  const [editor, setEditor] = useState<EditorState | null>(null)
  const [corrections, setCorrections] = useState<EditorCorrections | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState("")
  const [saveState, setSaveState] = useState<"saved" | "saving" | "error">("saved")
  const [playing, setPlaying] = useState(false)
  const [playhead, setPlayhead] = useState(0)
  const [zoom, setZoom] = useState(2)
  const [scroll, setScroll] = useState(0)
  const [activeRole, setActiveRole] = useState<EditorMarkerRole>("exit_loop")
  const [auditionMode, setAuditionMode] = useState<"through" | "loop" | null>(null)
  const [auditionBusy, setAuditionBusy] = useState(false)
  const [preview, setPreview] = useState<EditorApplyPreview | null>(null)
  const [applyBusy, setApplyBusy] = useState(false)
  const [adoptBusy, setAdoptBusy] = useState(false)
  const waveContainer = useRef<HTMLDivElement>(null)
  const waveSurfer = useRef<WaveSurfer | null>(null)
  const audioToken = useRef<string>("")
  const audioContext = useRef<AudioContext | null>(null)
  const auditionSource = useRef<AudioBufferSourceNode | null>(null)
  const saveTimer = useRef<number | null>(null)
  const revision = useRef(0)
  const correctionsRef = useRef<EditorCorrections | null>(null)
  const initialised = useRef(false)
  const saveQueue = useRef<Promise<number>>(Promise.resolve(0))
  const lastSaved = useRef("")

  const load = useCallback(async () => {
    setLoading(true)
    setError("")
    try {
      const message = finalMessage(await runBridge("editor-load", [], { content_id: target.contentId ?? "", path: target.path ?? "" }))
      const next = asRecord(message.editor) as unknown as EditorState
      setEditor(next)
      setCorrections(next.draft.corrections)
      correctionsRef.current = next.draft.corrections
      lastSaved.current = JSON.stringify(next.draft.corrections)
      revision.current = next.draft.revision
      initialised.current = false
      setPlayhead(next.draft.corrections.markers.find((marker) => marker.role === "intro")?.seconds ?? 0)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setLoading(false)
    }
  }, [target.contentId, target.path])

  useEffect(() => { void load() }, [load])

  useEffect(() => {
    if (!editor || !waveContainer.current) return
    let canceled = false
    const setup = async () => {
      try {
        const asset = await registerAudio(editor.track.path)
        if (canceled) {
          await releaseAudio(asset.token)
          return
        }
        audioToken.current = asset.token
        if (!asset.url) return
        const lane = editor.waveform.detail ?? editor.waveform.preview
        const instance = WaveSurfer.create({
          container: waveContainer.current!,
          url: asset.url,
          height: 1,
          waveColor: "transparent",
          progressColor: "transparent",
          cursorWidth: 0,
          interact: false,
          normalize: false,
          peaks: lane ? [lane.heights] : undefined,
          duration: editor.track.duration_sec,
        })
        instance.on("timeupdate", setPlayhead)
        instance.on("play", () => setPlaying(true))
        instance.on("pause", () => setPlaying(false))
        instance.on("finish", () => setPlaying(false))
        instance.on("error", (reason) => setError(String(reason)))
        waveSurfer.current = instance
      } catch (reason) {
        if (!canceled) setError(reason instanceof Error ? reason.message : String(reason))
      }
    }
    void setup()
    return () => {
      canceled = true
      waveSurfer.current?.destroy()
      waveSurfer.current = null
      if (audioToken.current) void releaseAudio(audioToken.current)
      audioToken.current = ""
    }
  }, [editor?.track.audio_fingerprint, editor?.track.path, editor?.track.duration_sec, editor?.waveform.detail, editor?.waveform.preview])

  const persistDraft = useCallback((value?: EditorCorrections): Promise<number> => {
    const next = value ?? correctionsRef.current
    const draftId = editor?.draft.id
    if (!draftId || !next) return Promise.resolve(revision.current)
    const serialized = JSON.stringify(next)
    const save = async () => {
      if (serialized === lastSaved.current) return revision.current
      setSaveState("saving")
      try {
        const message = finalMessage(await runBridge("editor-save-draft", [], {
          draft_id: draftId, revision: revision.current, corrections: next,
        }))
        const saved = asRecord(message.draft)
        revision.current = Number(saved.revision)
        lastSaved.current = serialized
        setEditor((current) => current ? { ...current, draft: { ...current.draft, revision: revision.current, status: "draft", updated_at: String(saved.updated_at) } } : current)
        if (JSON.stringify(correctionsRef.current) === serialized) setSaveState("saved")
        return revision.current
      } catch (reason) { setSaveState("error"); throw reason }
    }
    const pending = saveQueue.current.then(save, save)
    saveQueue.current = pending
    return pending
  }, [editor?.draft.id])

  useEffect(() => {
    correctionsRef.current = corrections
    if (!corrections || !editor) return
    if (!initialised.current) {
      initialised.current = true
      return
    }
    setSaveState("saving")
    if (saveTimer.current) window.clearTimeout(saveTimer.current)
    saveTimer.current = window.setTimeout(() => void persistDraft(corrections).catch((reason) => onToast(reason instanceof Error ? reason.message : String(reason), "danger")), 250)
    return () => { if (saveTimer.current) window.clearTimeout(saveTimer.current) }
  }, [corrections, editor?.draft.id, onToast, persistDraft])

  useEffect(() => () => {
    auditionSource.current?.stop()
    void audioContext.current?.close()
  }, [])

  const seek = (seconds: number) => {
    const value = Math.max(0, Math.min(editor?.track.duration_sec ?? 0, seconds))
    setPlayhead(value)
    waveSurfer.current?.setTime(value)
  }

  const updateCorrections = (producer: (current: EditorCorrections) => EditorCorrections) => {
    setCorrections((current) => {
      if (!current || !editor) return current
      const next = producer(current)
      if (next.bpm !== current.bpm || next.first_downbeat_sec !== current.first_downbeat_sec) next.write_grid = true
      return normalizeCorrections(next, editor.track.duration_sec)
    })
  }

  const updateMarker = (role: EditorMarkerRole, seconds: number, anyBeat: boolean) => {
    updateCorrections((current) => ({
      ...current,
      markers: current.markers.map((marker) => marker.role === role ? { ...marker, seconds, snap_mode: anyBeat ? "beat" : "downbeat" } : marker),
    }))
  }

  const selectedMarker = corrections?.markers.find((marker) => marker.role === activeRole)
  const selectedLoop = selectedMarker?.kind === "loop" ? selectedMarker : corrections?.markers.find((marker) => marker.role === "exit_loop")
  const lane = editor?.waveform.detail ?? editor?.waveform.preview ?? null
  const viewportDuration = editor ? editor.track.duration_sec / zoom : 0
  const viewportStart = editor ? scroll * Math.max(0, editor.track.duration_sec - viewportDuration) : 0

  const centerOn = (seconds: number) => {
    if (!editor || zoom <= 1) return
    setScroll(Math.max(0, Math.min(1, (seconds - viewportDuration / 2) / Math.max(0.001, editor.track.duration_sec - viewportDuration))))
  }

  const stopAudition = () => {
    auditionSource.current?.stop()
    auditionSource.current = null
    setAuditionMode(null)
  }

  const startAudition = async (mode: "through" | "loop") => {
    if (!editor || !selectedLoop) return
    stopAudition()
    waveSurfer.current?.pause()
    setAuditionBusy(true)
    try {
      // WebKit requires resume() to begin while the click still owns the user gesture.
      const context = audioContext.current ?? new AudioContext()
      audioContext.current = context
      const contextReady = context.state === "suspended" ? context.resume() : Promise.resolve()
      if (saveTimer.current) window.clearTimeout(saveTimer.current)
      await persistDraft()
      const message = finalMessage(await runBridge("editor-prepare-audition", [], { draft_id: editor.draft.id, role: selectedLoop.role }))
      const audition = asRecord(message.audition) as unknown as EditorAudition
      const asset = await registerAudio(audition.path)
      if (!asset.url) {
        setAuditionMode(mode)
        if (mode === "through") {
          window.setTimeout(() => setAuditionMode((current) => current === mode ? null : current), 900)
        }
        return
      }
      let auditionBytes: ArrayBuffer
      try {
        const response = await fetch(asset.url)
        if (!response.ok) throw new Error("Could not read the audition audio")
        auditionBytes = await response.arrayBuffer()
      } finally {
        await releaseAudio(asset.token)
      }
      await contextReady
      if (context.state === "suspended") await context.resume()
      const decoded = await context.decodeAudioData(auditionBytes)
      const source = context.createBufferSource()
      source.connect(context.destination)
      if (mode === "loop") {
        const loop = exactLoopBuffer(context, decoded, audition.loop_start_sec, audition.loop_end_sec)
        source.buffer = loop
        source.loop = true
        source.loopStart = 0
        source.loopEnd = loop.duration
        source.start(0)
      } else {
        source.buffer = decoded
        source.start(0)
      }
      source.onended = () => { if (auditionSource.current === source) setAuditionMode(null) }
      auditionSource.current = source
      setAuditionMode(mode)
    } catch (reason) {
      onToast(reason instanceof Error ? reason.message : String(reason), "danger")
    } finally {
      setAuditionBusy(false)
    }
  }

  const reviewApply = async () => {
    if (!editor) return
    setApplyBusy(true)
    try {
      if (saveTimer.current) window.clearTimeout(saveTimer.current)
      const currentRevision = await persistDraft()
      const message = finalMessage(await runBridge("editor-preview-apply", [], { draft_id: editor.draft.id, revision: currentRevision }))
      setPreview(asRecord(message.preview) as unknown as EditorApplyPreview)
    } catch (reason) {
      onToast(reason instanceof Error ? reason.message : String(reason), "danger")
    } finally {
      setApplyBusy(false)
    }
  }

  const confirmApply = async () => {
    if (!editor || !preview) return
    setApplyBusy(true)
    try {
      const result = finalMessage(await runBridge("editor-apply", [], {
        draft_id: editor.draft.id,
        revision: revision.current,
        idempotency_key: crypto.randomUUID(),
      }))
      setPreview(null)
      onToast(`Corrections applied. Backup: ${String(result.backup_dir)}`, "success")
      await load()
    } catch (reason) {
      onToast(reason instanceof Error ? reason.message : String(reason), "danger")
    } finally {
      setApplyBusy(false)
    }
  }

  const closeAndApply = async () => {
    setApplyBusy(true)
    try {
      const result = finalMessage(await runBridge("close-rekordbox"))
      if (!result.closed) throw new Error("Rekordbox did not close. Save pending work there and try again.")
      onRekordboxStatus(false)
      setPreview((current) => current ? { ...current, rekordbox_running: false } : current)
    } catch (reason) {
      onToast(reason instanceof Error ? reason.message : String(reason), "danger")
    } finally {
      setApplyBusy(false)
    }
  }

  const adopt = async (cue: EditorCuePoint) => {
    if (!editor) return
    setAdoptBusy(true)
    try {
      if (saveTimer.current) window.clearTimeout(saveTimer.current)
      await persistDraft()
      const message = finalMessage(await runBridge("editor-adopt-cues", [], { draft_id: editor.draft.id, cue_ids: [cue.id] }))
      onToast(`${String(message.adopted)} cue adopted`, "success")
      await load()
    } catch (reason) {
      onToast(reason instanceof Error ? reason.message : String(reason), "danger")
    } finally {
      setAdoptBusy(false)
    }
  }

  const resetAutomatic = async () => {
    if (!editor) return
    setApplyBusy(true)
    try {
      if (saveTimer.current) window.clearTimeout(saveTimer.current)
      const message = finalMessage(await runBridge("editor-reset-auto", [], { draft_id: editor.draft.id, revision: revision.current }))
      const draft = asRecord(message.draft)
      const next = asRecord(draft.corrections) as unknown as EditorCorrections
      revision.current = Number(draft.revision)
      initialised.current = false
      setCorrections(next)
      correctionsRef.current = next
      setSaveState("saved")
      onToast("Automatic suggestions restored", "neutral")
    } catch (reason) {
      onToast(reason instanceof Error ? reason.message : String(reason), "danger")
    } finally {
      setApplyBusy(false)
    }
  }

  const rebase = async () => {
    if (!editor) return
    setApplyBusy(true)
    try {
      if (saveTimer.current) window.clearTimeout(saveTimer.current)
      const message = finalMessage(await runBridge("editor-rebase", [], { draft_id: editor.draft.id, revision: revision.current }))
      const next = asRecord(message.editor) as unknown as EditorState
      setEditor(next)
      setCorrections(next.draft.corrections)
      correctionsRef.current = next.draft.corrections
      lastSaved.current = JSON.stringify(next.draft.corrections)
      revision.current = next.draft.revision
      initialised.current = false
      setSaveState("saved")
      onToast("Draft rebased onto the current audio and Rekordbox analysis", "success")
    } catch (reason) {
      onToast(reason instanceof Error ? reason.message : String(reason), "danger")
    } finally {
      setApplyBusy(false)
    }
  }

  if (loading) return <main className="track-editor loading"><LoaderCircle className="spin" size={20} /><span>Loading waveform and draft...</span></main>
  if (error && !editor) return <main className="track-editor loading"><AlertTriangle size={20} /><strong>Track editor unavailable</strong><span>{error}</span><EditorButton icon={ArrowLeft} onClick={onClose}>Back</EditorButton></main>
  if (!editor || !corrections || !lane) return null

  return <main className="track-editor">
    <header className="editor-header">
      <button className="editor-back" onClick={onClose} aria-label="Close track editor"><ArrowLeft size={17} /></button>
      <div className="editor-title"><span>{editor.waveform.source === "rekordbox_anlz" ? <Disc3 size={14} /> : <Music2 size={14} />}{editor.waveform.source === "rekordbox_anlz" ? "Rekordbox analysis" : "Local analysis"}</span><h1>{editor.track.title}</h1><p>{editor.track.artist || "Unknown artist"} · {formatTime(editor.track.duration_sec)}</p></div>
      <div className={`draft-state ${saveState}`}>
        {saveState === "saving" ? <LoaderCircle className="spin" size={13} /> : saveState === "error" ? <AlertTriangle size={13} /> : <Check size={13} />}
        {saveState === "saving" ? "Saving draft" : saveState === "error" ? "Draft not saved" : `Draft saved · r${revision.current}`}
      </div>
      <button className="editor-reset" aria-label="Reset to automatic analysis" title="Reset to automatic analysis" disabled={applyBusy} onClick={() => void resetAutomatic()}><RotateCcw size={15} /></button>
      <EditorButton icon={Save} primary loading={applyBusy} disabled={editor.draft.status === "stale"} onClick={() => void reviewApply()}>Review and apply</EditorButton>
    </header>

    <div className="editor-workspace">
      {editor.draft.status === "stale" ? <div className="editor-warning stale"><AlertTriangle size={14} /><span>The audio, Rekordbox analysis, or cue layout changed after this draft was created.</span><EditorButton loading={applyBusy} onClick={() => void rebase()}>Rebase draft</EditorButton></div> : null}
      {editor.warnings.map((warning) => <div className="editor-warning" key={warning}><AlertTriangle size={14} /><span>{warning}</span></div>)}
      {error ? <div className="editor-warning"><AlertTriangle size={14} /><span>{error}</span></div> : null}

      <section className="transport-band">
        <div ref={waveContainer} className="wave-audio-engine" aria-hidden="true" />
        <button className="transport-play" onClick={() => {
          stopAudition()
          void waveSurfer.current?.playPause()
        }} aria-label={playing ? "Pause" : "Play"}>{playing ? <Pause size={18} /> : <Play size={18} />}</button>
        <button className="transport-skip" onClick={() => seek(Math.max(0, playhead - 8))} aria-label="Back eight seconds"><SkipBack size={15} /></button>
        <span className="transport-time mono">{formatTime(playhead)}</span>
        <div className="transport-line"><i style={{ width: `${playhead / editor.track.duration_sec * 100}%` }} /></div>
        <span className="transport-time mono">{formatTime(editor.track.duration_sec)}</span>
        <div className="transport-zoom"><ZoomIn size={14} /><input aria-label="Waveform zoom" type="range" min="1" max="64" step="1" value={zoom} onChange={(event) => setZoom(Number(event.target.value))} /><span className="mono">{zoom}x</span></div>
      </section>

      <section className="waveform-band">
        <div className="waveform-heading"><div><span className="section-kicker">Editable waveform</span><strong>{editor.waveform.source === "rekordbox_anlz" ? `${waveformFormatLabel(editor.waveform.detail ?? editor.waveform.preview)} · Authoritative ANLZ` : "Decoded local peaks"}</strong></div><div className="waveform-legend"><span><i className="beat" />Beat</span><span><i className="downbeat" />Downbeat</span><span><i className="draft" />Draft cue</span></div></div>
        <EditableWaveform lane={lane} duration={editor.track.duration_sec} grid={corrections.grid} markers={corrections.markers} playhead={playhead} zoom={zoom} scroll={scroll} activeRole={activeRole} gridEditable={editor.grid_editable} firstDownbeat={corrections.first_downbeat_sec} onSeek={seek} onMarker={updateMarker} onFirstDownbeat={(seconds) => updateCorrections((value) => ({ ...value, first_downbeat_sec: seconds }))} onActiveRole={(role) => { setActiveRole(role); const marker = corrections.markers.find((item) => item.role === role); if (marker) centerOn(marker.seconds) }} onPan={setScroll} />
        <WaveOverview lane={editor.waveform.preview ?? lane} playhead={playhead} duration={editor.track.duration_sec} viewportStart={viewportStart} viewportDuration={viewportDuration} onSeek={(seconds) => { seek(seconds); centerOn(seconds) }} />
        <input className="wave-scroll" aria-label="Waveform position" type="range" min="0" max="1" step="0.001" value={scroll} disabled={zoom === 1} onChange={(event) => setScroll(Number(event.target.value))} />
      </section>

      <div className="editor-lower-grid">
        <section className="correction-panel">
          <div className="panel-heading"><span><Gauge size={15} />Beat grid</span>{editor.grid_editable ? <small><Unlock size={11} />Constant tempo</small> : <small><Lock size={11} />Read-only</small>}</div>
          <div className="correction-fields">
            <div className="correction-field"><label htmlFor="editor-bpm">BPM</label><div className="number-control"><button aria-label="Decrease BPM" disabled={!editor.grid_editable} onClick={() => updateCorrections((value) => ({ ...value, bpm: Math.max(60, Number((value.bpm - .01).toFixed(2))) }))}><Minus size={13} /></button><input id="editor-bpm" type="number" min="60" max="200" step="0.01" disabled={!editor.grid_editable} value={corrections.bpm} onChange={(event) => updateCorrections((value) => ({ ...value, bpm: Math.max(60, Math.min(200, Number(event.target.value))) }))} /><button aria-label="Increase BPM" disabled={!editor.grid_editable} onClick={() => updateCorrections((value) => ({ ...value, bpm: Math.min(200, Number((value.bpm + .01).toFixed(2))) }))}><Plus size={13} /></button></div><div className="field-quick"><button disabled={!editor.grid_editable || corrections.bpm / 2 < 60} onClick={() => updateCorrections((value) => ({ ...value, bpm: value.bpm / 2 }))}>Half</button><button disabled={!editor.grid_editable || corrections.bpm * 2 > 200} onClick={() => updateCorrections((value) => ({ ...value, bpm: value.bpm * 2 }))}>Double</button></div></div>
            <div className="correction-field"><label htmlFor="editor-downbeat">First downbeat</label><input id="editor-downbeat" className="mono" type="number" min="0" max={editor.track.duration_sec} step="0.001" disabled={!editor.grid_editable} value={corrections.first_downbeat_sec} onChange={(event) => updateCorrections((value) => ({ ...value, first_downbeat_sec: Number(event.target.value) }))} /><div className="field-quick"><button disabled={!editor.grid_editable} onClick={() => updateCorrections((value) => ({ ...value, first_downbeat_sec: Math.max(0, value.first_downbeat_sec - .01) }))}>-10 ms</button><button disabled={!editor.grid_editable} onClick={() => updateCorrections((value) => ({ ...value, first_downbeat_sec: Math.min(editor.track.duration_sec, value.first_downbeat_sec + .01) }))}>+10 ms</button></div></div>
            <div className="correction-field"><label htmlFor="editor-key">Key</label><div className="select-wrap"><select id="editor-key" aria-label="Camelot key" value={corrections.camelot_key} onChange={(event) => setCorrections((value) => value ? { ...value, camelot_key: event.target.value, musical_key: CAMELOT_TO_KEY[event.target.value] } : value)}>{CAMELOT_KEYS.map((key) => <option key={key}>{key}</option>)}</select><ChevronDown size={13} /></div><small>{corrections.musical_key}</small></div>
          </div>
        </section>

        <section className="audition-panel">
          <div className="panel-heading"><span><Repeat2 size={15} />Loop audition</span><small>Hard seam · no crossfade</small></div>
          <div className="loop-targets">
            {(["intro_loop", "exit_loop"] as EditorMarkerRole[]).map((role) => {
              const marker = corrections.markers.find((item) => item.role === role)!
              return <button key={role} className={selectedLoop?.role === role ? "active" : ""} onClick={() => { setActiveRole(role); centerOn(marker.seconds); seek(marker.seconds) }}><span style={{ background: MARKER_COLORS[role] }}>{marker.pad}</span><strong>{marker.name}</strong><small className="mono">{formatTime(marker.seconds)}</small></button>
            })}
          </div>
          {selectedLoop ? <>
            <div className="loop-length"><span>Length</span>{([4, 8] as const).map((beats) => <button key={beats} className={selectedLoop.loop_beats === beats ? "active" : ""} onClick={() => updateCorrections((current) => ({ ...current, markers: current.markers.map((marker) => marker.role === selectedLoop.role ? { ...marker, loop_beats: beats } : marker) }))}>{beats} beats</button>)}</div>
            <div className="loop-readout"><span><small>Start</small><strong className="mono">{formatTime(selectedLoop.seconds)}</strong></span><span><small>End</small><strong className="mono">{formatTime(selectedLoop.end_seconds ?? 0)}</strong></span><span><small>Grid</small><strong>{selectedLoop.snap_mode === "beat" ? "Any beat" : "Downbeat"}</strong></span></div>
            <div className="ab-controls"><button className={auditionMode === "through" ? "active" : ""} disabled={auditionBusy} onClick={() => auditionMode === "through" ? stopAudition() : void startAudition("through")}><span>A</span><strong>Through</strong><small>Hear across the seam</small></button><button className={auditionMode === "loop" ? "active" : ""} disabled={auditionBusy} onClick={() => auditionMode === "loop" ? stopAudition() : void startAudition("loop")}><span>B</span><strong>Loop</strong><small>Repeat exact boundary</small></button></div>
          </> : null}
        </section>
      </div>

      <section className="cue-editor-band">
        <div className="panel-heading"><span><SlidersHorizontal size={15} />Draft markers</span><small>Drag markers · hold Option for any beat</small></div>
        <div className="draft-marker-list">{corrections.markers.map((marker) => <button key={marker.role} className={`${activeRole === marker.role ? "active" : ""} ${marker.conflict ? "conflict" : ""}`} onClick={() => { setActiveRole(marker.role); centerOn(marker.seconds); seek(marker.seconds) }}><span className="cue-pad" style={{ background: MARKER_COLORS[marker.role] }}>{marker.pad}</span><span><strong>{marker.name}</strong><small>{marker.ownership === "app" ? "App-owned" : "Draft suggestion"}</small></span><span className="mono">{formatTime(marker.seconds)}</span>{marker.conflict ? <AlertTriangle size={13} /> : <Grid2X2 size={13} />}</button>)}</div>
      </section>

      {editor.existing_cues.length ? <section className="existing-cues-band"><div className="panel-heading"><span><ShieldCheck size={15} />Existing Rekordbox cues</span><small>Manual cues remain locked</small></div><div className="existing-cues-list">{editor.existing_cues.map((cue) => <CueOwnershipRow key={cue.id} cue={cue} onAdopt={adopt} busy={adoptBusy} />)}</div></section> : null}
    </div>

    {preview ? <div className="editor-sheet-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget && !applyBusy) setPreview(null) }}><section className="editor-apply-sheet" role="dialog" aria-modal="true" aria-labelledby="apply-title"><div className="apply-sheet-icon"><ShieldCheck size={20} /></div><div><span className="section-kicker">Reviewed transaction</span><h2 id="apply-title">Apply this draft?</h2><p>{preview.rekordbox ? "The database, affected ANLZ grid, and audio file are backed up before the write." : "The original audio file is backed up before tags and analysis are updated."}</p></div><div className="apply-change-list">{preview.changes.map((change) => <span key={change}><Check size={13} />{change}</span>)}</div>{preview.conflicts.length ? <div className="editor-warning"><ShieldCheck size={14} /><span>Occupied manual cues will be preserved: {preview.conflicts.join(", ")}</span></div> : null}{preview.rekordbox && (preview.rekordbox_running || rekordboxRunning) ? <div className="rekordbox-close-prompt"><Disc3 size={17} /><span><strong>Rekordbox is open</strong><small>Save work there before continuing.</small></span><EditorButton loading={applyBusy} onClick={() => void closeAndApply()}>Save and close</EditorButton></div> : null}<div className="apply-sheet-actions"><EditorButton onClick={() => setPreview(null)} disabled={applyBusy}>Cancel</EditorButton><EditorButton icon={Save} primary loading={applyBusy} disabled={Boolean(preview.rekordbox && (preview.rekordbox_running || rekordboxRunning))} onClick={() => void confirmApply()}>Apply corrections</EditorButton></div></section></div> : null}
  </main>
}
