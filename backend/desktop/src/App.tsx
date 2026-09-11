import { AppButton, StatusBadge, Segmented } from "./components/Primitives"
import { Sidebar, Topbar } from "./components/AppChrome"
import { CommandPalette } from "./components/CommandPalette"
import { useDialogFocus } from "./components/useDialogFocus"
import {
  Activity,
  AlertCircle,
  ArrowLeft,
  ArrowRight,
  AudioLines,
  Check,
  CheckCircle2,
  ChevronDown,
  CircleDot,
  Clock3,
  Disc3,
  Download,
  ExternalLink,
  FileAudio,
  FileUp,
  Folder,
  Gauge,
  HardDrive,
  Heart,
  Info,
  ListMusic,
  LockKeyhole,
  LoaderCircle,
  Music2,
  Plus,
  RefreshCw,
  RotateCcw,
  Search,
  ShieldCheck,
  SlidersHorizontal,
  Square,
  Trash2,
  TriangleAlert,
  Usb,
  WandSparkles,
  X,
  Zap,
  type LucideIcon,
} from "lucide-react"
import { FormEvent, ReactNode, useCallback, useEffect, useMemo, useRef, useState } from "react"
import { finalMessage, isDesktopRuntime, runBridge, selectReplacementAudio } from "./lib/bridge"
import { mockConfig } from "./lib/mock"
import { drawRekordboxBeatGrid, drawRekordboxWaveform, waveformFormatLabel } from "./lib/rekordboxWaveform"
import { TrackEditor, type EditorTarget } from "./TrackEditor"
import type {
  ActivityJob,
  AppConfig,
  BridgeMessage,
  CueMode,
  DoctorResult,
  JobTrack,
  Playlist,
  PlaylistTrack,
  RekordboxCuePoint,
  RekordboxWaveform,
  RekordboxWaveformLane,
  SourceEntry,
  SyncPlan,
  TrackFeature,
  UsbDevice,
  ViewId,
} from "./types"

const JOBS_KEY = "soundcloud-dl.desktop.jobs"
const THEME_KEY = "soundcloud-dl.desktop.theme"

type Theme = "dark" | "light"
type InspectorItem =
  | { kind: "track"; track: JobTrack }
  | { kind: "playlist"; playlist: Playlist }
  | { kind: "rekordbox-track"; track: PlaylistTrack; playlist: Playlist }
  | null


function asString(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback
}

function asNumber(value: unknown, fallback = 0): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" ? value as Record<string, unknown> : {}
}

function stopActiveTracks(tracks: JobTrack[], message: string): JobTrack[] {
  return tracks.map((track) => ["queued", "downloading", "fallback", "converting", "analyzing", "syncing"].includes(track.stage)
    ? { ...track, stage: "interrupted", error: message }
    : track)
}

function loadStoredJobs(): ActivityJob[] {
  try {
    const value = JSON.parse(localStorage.getItem(JOBS_KEY) ?? "[]")
    if (!Array.isArray(value)) return []
    return value.map((stored: ActivityJob) => {
      let job = stored
      if (job.state === "running") {
        job = {
          ...job,
          state: "failed",
          stage: "Interrupted",
          summary: "The app closed before this job finished. Resolve the source again to retry its remaining delta.",
        }
      }
      if (job.state === "failed") {
        const interrupted = job.tracks.some((track) => ["analyzing", "syncing"].includes(track.stage))
        const message = interrupted
          ? "The worker stopped before analysis/import finished. Retry sync reuses audio already saved on disk."
          : job.summary || "The worker stopped. Retry sync to continue."
        job = { ...job, tracks: stopActiveTracks(job.tracks, message), summary: interrupted ? message : job.summary }
      }
      const tracks = job.tracks.map((track) => track.stage === "failed" && track.error?.toLowerCase().includes("drm protected") ? {
        ...track,
        stage: "protected" as const,
        cueStatus: "skipped" as const,
        error: "SoundCloud restricts this track to encrypted playback. Attach a licensed audio file to continue.",
      } : track)
      const protectedCount = tracks.filter((track) => track.stage === "protected").length
      return protectedCount ? {
        ...job,
        state: job.state === "complete" ? "partial" : job.state,
        stage: job.state === "running" || job.state === "blocked" ? job.stage : "Needs source files",
        summary: `${protectedCount} protected track(s) need a licensed local audio file.`,
        tracks,
      } : { ...job, tracks }
    })
  } catch {
    return []
  }
}

function createId(prefix: string): string {
  return `${prefix}_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`
}

function timeAgo(iso: string): string {
  const delta = Date.now() - new Date(iso).getTime()
  if (delta < 60_000) return "just now"
  if (delta < 3_600_000) return `${Math.floor(delta / 60_000)}m ago`
  if (delta < 86_400_000) return `${Math.floor(delta / 3_600_000)}h ago`
  return `${Math.floor(delta / 86_400_000)}d ago`
}

function formatDuration(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds <= 0) return "--:--"
  const minutes = Math.floor(seconds / 60)
  const remainder = Math.floor(seconds % 60)
  return `${minutes}:${String(remainder).padStart(2, "0")}`
}

function formatTimestamp(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return "--:--.---"
  const minutes = Math.floor(seconds / 60)
  const remainder = seconds - minutes * 60
  return `${minutes}:${remainder.toFixed(3).padStart(6, "0")}`
}

function SourceIcon({ url }: { url: string }) {
  return (
    <div className="source-art" aria-hidden="true">
      <AudioLines size={22} />
      <div className="source-bars"><i /><i /><i /><i /><i /><i /><i /></div>
      {url.includes("likes") ? <Heart size={11} className="source-corner-icon" /> : null}
    </div>
  )
}

function PlanSummary({ plan }: { plan: SyncPlan }) {
  const newCount = plan.added.length
  return (
    <div className="plan-strip">
      <div><strong>{plan.total_count}</strong><span>Total tracks</span></div>
      <div className="plan-new"><strong>+{newCount}</strong><span>New downloads</span></div>
      <div><strong>{plan.unchanged_count}</strong><span>Already local</span></div>
      <div className={plan.removed_ids.length ? "plan-warning" : ""}><strong>{plan.removed_ids.length}</strong><span>Missing upstream · kept</span></div>
    </div>
  )
}

function SourceTrackList({ entries }: { entries: SourceEntry[] }) {
  const [expanded, setExpanded] = useState(false)
  const visible = expanded ? entries : entries.slice(0, 6)
  return (
    <div className="source-track-list">
      <div className="track-list-head"><span>New tracks</span><span>{entries.length} selected</span></div>
      {visible.map((entry, index) => (
        <div className="source-track-row" key={entry.id || entry.url}>
          <span className="track-index">{String(index + 1).padStart(2, "0")}</span>
          <FileAudio size={16} className="muted" aria-hidden="true" />
          <span className="source-track-title"><strong>{entry.title || entry.label}</strong>{entry.artist ? <small>{entry.artist}</small> : null}</span>
          <StatusBadge tone="blue">New</StatusBadge>
        </div>
      ))}
      {entries.length > 6 ? <button className="show-more" onClick={() => setExpanded((value) => !value)}>{expanded ? "Show fewer tracks" : `Show ${entries.length - 6} more tracks`} <ChevronDown size={13} className={expanded ? "chevron-up" : ""} /></button> : null}
    </div>
  )
}

function ImportWorkspace({
  config,
  playlists,
  jobs,
  onCreateJob,
  onUpdateJob,
  onInspect,
  initialUrl,
  resumeJob,
  onResumeJobConsumed,
  onShowActivity,
  rekordboxRunning,
  onRekordboxStatus,
}: {
  config: AppConfig | null
  playlists: Playlist[]
  jobs: ActivityJob[]
  onCreateJob: (job: ActivityJob) => void
  onUpdateJob: (id: string, update: Partial<ActivityJob> | ((job: ActivityJob) => Partial<ActivityJob>)) => void
  onInspect: (item: InspectorItem) => void
  initialUrl: string
  resumeJob: ActivityJob | null
  onResumeJobConsumed: () => void
  onShowActivity: () => void
  rekordboxRunning: boolean | null
  onRekordboxStatus: (running: boolean) => void
}) {
  const [url, setUrl] = useState(initialUrl)
  const [plan, setPlan] = useState<SyncPlan | null>(null)
  const [resolving, setResolving] = useState(false)
  const [error, setError] = useState("")
  const [destination, setDestination] = useState<"rekordbox" | "download">("rekordbox")
  const [playlistName, setPlaylistName] = useState("")
  const [outputDir, setOutputDir] = useState(config?.output_dir ?? "~/Downloads")
  const [analyze, setAnalyze] = useState(true)
  const [cueMode, setCueMode] = useState<CueMode>("off")
  const [runningJobId, setRunningJobId] = useState<string | null>(null)
  const [engineStatus, setEngineStatus] = useState("")
  const [handoffBusy, setHandoffBusy] = useState(false)
  const currentJob = runningJobId ? jobs.find((job) => job.id === runningJobId) : null

  useEffect(() => {
    if (initialUrl) {
      setUrl(initialUrl)
      setPlan(null)
      setError("")
    }
  }, [initialUrl])

  useEffect(() => {
    if (!resumeJob) return
    let canceled = false
    setUrl(resumeJob.sourceUrl)
    setDestination(resumeJob.destination ?? (resumeJob.playlistName ? "rekordbox" : "download"))
    setPlaylistName(resumeJob.playlistName ?? "")
    setOutputDir(resumeJob.outputDir)
    setAnalyze(resumeJob.analyze ?? true)
    setCueMode(resumeJob.cueMode ?? "off")
    setRunningJobId(resumeJob.id)
    setHandoffBusy(resumeJob.state !== "complete")
    setEngineStatus(resumeJob.state === "complete" ? "" : "Refreshing source delta...")
    setError("")

    const rehydrate = async () => {
      if (resumeJob.state === "complete") { onResumeJobConsumed(); return }
      try {
        const result = await runBridge("sync-plan", [resumeJob.sourceUrl], undefined, (message) => {
          if (!canceled && message.message) setEngineStatus(message.message)
        })
        const nextPlan = finalMessage(result) as unknown as SyncPlan
        if (!canceled) {
          setPlan(nextPlan)
          setEngineStatus("")
        }
      } catch (reason) {
        if (!canceled) setError(reason instanceof Error ? reason.message : String(reason))
      } finally {
        if (!canceled) {
          setHandoffBusy(false)
          onResumeJobConsumed()
        }
      }
    }
    void rehydrate()
    return () => { canceled = true }
  }, [resumeJob, onResumeJobConsumed])

  useEffect(() => {
    if (config && !plan) setOutputDir(config.output_dir)
  }, [config, plan])

  const resolveSource = async (event?: FormEvent) => {
    event?.preventDefault()
    const cleanUrl = url.trim()
    if (!cleanUrl) return
    setResolving(true)
    setError("")
    setPlan(null)
    setEngineStatus("Connecting to SoundCloud...")
    try {
      const result = await runBridge("sync-plan", [cleanUrl], undefined, (message) => {
        if (message.message) setEngineStatus(message.message)
      })
      const message = finalMessage(result) as unknown as SyncPlan
      setPlan(message)
      setPlaylistName(message.suggested_playlist)
      setOutputDir(message.target_dir)
      setEngineStatus("")
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
      setEngineStatus("")
    } finally {
      setResolving(false)
    }
  }

  const applyProgressMessage = (jobId: string, message: BridgeMessage, entries: SourceEntry[]) => {
    if (message.event === "download-results" && Array.isArray(message.download_outcomes)) {
      const outcomes = message.download_outcomes.map(asRecord)
      onUpdateJob(jobId, (job) => ({
        tracks: job.tracks.map((track) => {
          const outcome = outcomes.find((item) => asString(item.id) === track.id)
          if (!outcome) return track
          const failed = outcome.outcome === "failed"
          return {
            ...track,
            stage: failed ? "failed" : analyze ? "analyzing" : "ready",
            progress: failed || !analyze ? 100 : 58,
            path: Array.isArray(outcome.paths) ? asString(outcome.paths[0]) : track.path,
            downloadMethod: outcome.download_method === "klickaud" ? "klickaud" : outcome.download_method === "yt-dlp" ? "yt-dlp" : undefined,
            fallbackAttempted: outcome.fallback_attempted === true,
            primaryDownloadError: asString(outcome.primary_error),
            error: failed ? asString(outcome.error) : undefined,
          }
        }),
      }))
    }
    if (message.event === "status" && message.message) {
      setEngineStatus(message.message)
      const value = message.message
      onUpdateJob(jobId, (job) => {
        const tracks = job.tracks.map((track) => ({ ...track }))
        let stage = job.stage
        let progress = job.progress
        const urlMatch = value.match(/(?:downloading|finished) (https?:\/\/\S+)$/)
        const statusUrlMatch = value.match(/(https?:\/\/\S+)$/)
        const analysisProgressMatch = value.match(/\[(\d+)\/(\d+) done\]/i)
        const analyzedFileMatch = value.match(/analyzing \((.+)\)$/i)
        const lowerValue = value.toLowerCase()
        const statusEntryIndex = statusUrlMatch
          ? entries.findIndex((entry) => entry.url === statusUrlMatch[1])
          : -1
        if (value.includes("downloading")) stage = "Downloading"
        if (value.includes("finished") && urlMatch) {
          const index = entries.findIndex((entry) => entry.url === urlMatch[1])
          if (index >= 0 && tracks[index]) {
            tracks[index].stage = analyze ? "analyzing" : "ready"
            tracks[index].progress = analyze ? 58 : 100
          }
          progress = Math.max(progress, 15 + ((index + 1) / Math.max(entries.length, 1)) * 40)
        } else if (urlMatch) {
          const index = entries.findIndex((entry) => entry.url === urlMatch[1])
          if (index >= 0 && tracks[index]) {
            tracks[index].stage = "downloading"
            tracks[index].progress = 24
          }
        }
        if ((lowerValue.includes("trying klickaud fallback") || lowerValue.includes("klickaud:")) && statusEntryIndex >= 0 && tracks[statusEntryIndex]) {
          stage = "Trying KlickAud fallback"
          tracks[statusEntryIndex].stage = "fallback"
          tracks[statusEntryIndex].progress = Math.max(tracks[statusEntryIndex].progress, 36)
          tracks[statusEntryIndex].fallbackAttempted = true
        }
        if (lowerValue.includes("klickaud fallback succeeded") && statusEntryIndex >= 0 && tracks[statusEntryIndex]) {
          stage = "Fallback recovered"
          tracks[statusEntryIndex].stage = analyze ? "analyzing" : "ready"
          tracks[statusEntryIndex].progress = analyze ? 58 : 100
          tracks[statusEntryIndex].downloadMethod = "klickaud"
          tracks[statusEntryIndex].fallbackAttempted = true
        }
        if (lowerValue.includes("primary download succeeded") && statusEntryIndex >= 0 && tracks[statusEntryIndex]) {
          tracks[statusEntryIndex].stage = analyze ? "analyzing" : "ready"
          tracks[statusEntryIndex].progress = analyze ? 58 : 100
          tracks[statusEntryIndex].downloadMethod = "yt-dlp"
        }
        if (lowerValue.includes("analyzing")) {
          stage = "Analyzing"
          const analyzedCount = analysisProgressMatch ? Number(analysisProgressMatch[1]) : 0
          const analysisTotal = analysisProgressMatch ? Number(analysisProgressMatch[2]) : 0
          const analysisRatio = analysisTotal > 0 ? analyzedCount / analysisTotal : 0
          progress = Math.max(progress, 58 + analysisRatio * 28)
          if (analyzedFileMatch) {
            const filename = analyzedFileMatch[1]
            const index = tracks.findIndex((track) => filename.includes(`[${track.id}]`) || filename.includes(track.title))
            if (index >= 0) {
              tracks[index].stage = "analyzing"
              tracks[index].progress = 86
            }
          }
        }
        if (value.toLowerCase().includes("tagging")) stage = "Tagging"
        return { tracks, stage, progress }
      })
    }
  }

  const runImport = async (jobId: string, activePlan: SyncPlan) => {
    setError("")
    setEngineStatus("Preparing import...")
    onUpdateJob(jobId, (job) => {
      const knownIds = new Set(job.tracks.map((track) => track.id))
      const added: JobTrack[] = activePlan.added.filter((entry) => !knownIds.has(entry.id)).map((entry) => ({
        id: entry.id,
        title: entry.title || entry.label,
        artist: entry.artist || "SoundCloud",
        stage: "queued",
        progress: 0,
        cueStatus: cueMode === "fill" ? "proposed" : "off",
        sourceUrl: entry.url,
      }))
      const tracks = [...job.tracks, ...added]
      return { tracks, total: Math.max(tracks.length, 1) }
    })
    try {
      const result = await runBridge("sync", [], {
        url: activePlan.url,
        target_dir: outputDir,
        playlist_name: destination === "rekordbox" ? playlistName : "",
        analyze,
        write_tags: analyze,
        push: destination === "rekordbox",
        cue_mode: destination === "rekordbox" ? cueMode : "off",
      }, (message) => applyProgressMessage(jobId, message, activePlan.added))
      const done = finalMessage(result)
      const failedRaw = Array.isArray(done.failed_downloads) ? done.failed_downloads : Array.isArray(done.failures) ? done.failures : []
      const failed = failedRaw.map(asRecord)
      const outcomeRaw = Array.isArray(done.download_outcomes) ? done.download_outcomes : []
      const outcomes = outcomeRaw.map(asRecord)
      const featureRaw = Array.isArray(done.features) ? done.features : []
      const features = featureRaw.map((value) => asRecord(value) as unknown as TrackFeature)
      const completedPaths = Array.isArray(done.paths) ? done.paths.map((value) => asString(value)) : []
      const protectedIds = new Set(Array.isArray(done.protected_ids) ? done.protected_ids.map((value) => asString(value)) : [])
      const exceptionIds = new Set([
        ...failed.map((item) => asString(item.id)).filter(Boolean),
        ...protectedIds,
      ])
      const jobTracks = jobs.find((job) => job.id === jobId)?.tracks
      const relevantTrackIds = [...new Set([
        ...(jobTracks?.map((track) => track.id) ?? []),
        ...activePlan.added.map((entry) => entry.id),
      ])]
      const exceptionCount = relevantTrackIds.reduce((count, id) => count + (exceptionIds.has(id) ? 1 : 0), 0)
      const fallbackCount = outcomes.filter((item) => asString(item.outcome) === "fallback_succeeded").length
      const terminalFailureCount = failed.filter((item) => asString(item.reason) !== "protected").length
      const protectedFailureCount = Math.max(exceptionCount - terminalFailureCount, 0)
      const recoverySuffix = fallbackCount
        ? ` · ${fallbackCount} recovered with KlickAud`
        : ""
      const completionSummary = destination === "rekordbox"
        ? `${asNumber(asRecord(done.push).added_to_playlist, activePlan.added.length)} added to ${playlistName}${recoverySuffix}`
        : `${activePlan.added.length} downloaded to ${outputDir}${recoverySuffix}`
      const failureSummary = [
        terminalFailureCount
          ? `${terminalFailureCount} failed after both download methods.`
          : "",
        protectedFailureCount
          ? `${protectedFailureCount} protected track(s) need a licensed local audio file.`
          : "",
        fallbackCount
          ? `${fallbackCount} other track(s) were recovered with KlickAud.`
          : "",
      ].filter(Boolean).join(" ")
      onUpdateJob(jobId, (existing) => ({
        state: exceptionCount ? "partial" : "complete",
        stage: exceptionCount
          ? terminalFailureCount ? "Download failures" : "Needs source files"
          : "Complete",
        progress: 100,
        completed: Math.max(existing.total - exceptionCount, 0),
        summary: exceptionCount ? failureSummary : completionSummary,
        tracks: existing.tracks.map((track) => {
          const failure = failed.find((item) => asString(item.id) === track.id || asString(item.title) === track.title || asString(item.label) === track.title)
          const outcome = outcomes.find((item) => asString(item.id) === track.id || asString(item.title) === track.title || asString(item.url) === track.sourceUrl)
          const isProtected = protectedIds.has(track.id) || asString(failure?.reason) === "protected"
          const usedFallback = asString(outcome?.outcome) === "fallback_succeeded" || asString(outcome?.download_method) === "klickaud"
          const fallbackAttempted = outcome?.fallback_attempted === true || track.fallbackAttempted
          const primaryDownloadError = asString(outcome?.primary_error) || track.primaryDownloadError
          const feature = features.find((item) => item.source_id === track.id || item.path?.includes(`[${track.id}]`) || item.title === track.title)
          const completedPath = completedPaths.find((path) => path.includes(`[${track.id}]`))
          return isProtected ? {
            ...track,
            stage: "protected",
            progress: 100,
            cueStatus: "skipped",
            error: asString(failure?.error, "SoundCloud restricts this track to encrypted playback. Attach a licensed audio file to continue."),
            fallbackAttempted,
            primaryDownloadError,
          } : failure ? {
            ...track,
            stage: "failed",
            progress: 100,
            error: asString(failure.error, "Download unavailable"),
            fallbackAttempted,
            primaryDownloadError,
          } : {
            ...track,
            stage: "ready",
            progress: 100,
            title: feature?.title || track.title,
            artist: feature?.artist || track.artist,
            bpm: feature?.bpm,
            key: feature?.musical_key,
            path: feature?.path ?? completedPath,
            cueStatus: cueMode === "fill" && destination === "rekordbox" ? "filled" : "off",
            error: undefined,
            downloadMethod: usedFallback ? "klickaud" : asString(outcome?.download_method) === "yt-dlp" ? "yt-dlp" : track.downloadMethod,
            fallbackAttempted,
            primaryDownloadError,
          }
        }),
      }))
      setEngineStatus(exceptionCount ? `${exceptionCount} track(s) need attention` : fallbackCount ? `${fallbackCount} track(s) recovered with KlickAud` : "Import complete")
    } catch (reason) {
      const message = reason instanceof Error ? reason.message : String(reason)
      const blocked = message.toLowerCase().includes("rekordbox") && message.toLowerCase().includes("close")
      onUpdateJob(jobId, (existing) => ({
        state: blocked ? "blocked" : "failed",
        stage: blocked ? "Waiting for Rekordbox" : "Failed",
        summary: message,
        tracks: blocked ? existing.tracks : stopActiveTracks(existing.tracks, message),
      }))
      setError(message)
      setEngineStatus("")
    }
  }

  const startImport = () => {
    if (!plan) return
    setError("")
    const jobId = createId("job")
    const blocked = destination === "rekordbox" && rekordboxRunning === true
    const tracks: JobTrack[] = plan.added.map((entry, index) => ({
      id: entry.id || String(index),
      title: entry.title || entry.label,
      artist: entry.artist || "SoundCloud",
      stage: "queued",
      progress: 0,
      cueStatus: cueMode === "fill" ? "proposed" : "off",
      sourceUrl: entry.url,
    }))
    onCreateJob({
      id: jobId,
      title: plan.title,
      sourceUrl: plan.url,
      state: blocked ? "blocked" : "running",
      stage: blocked ? "Waiting for Rekordbox" : "Preparing",
      progress: blocked ? 0 : 3,
      completed: 0,
      total: Math.max(plan.added.length, 1),
      startedAt: new Date().toISOString(),
      tracks,
      destination,
      analyze,
      cueMode,
      playlistName: destination === "rekordbox" ? playlistName : undefined,
      outputDir,
      summary: blocked ? "Rekordbox must close before this playlist can be updated." : undefined,
    })
    setRunningJobId(jobId)
    if (!blocked) void runImport(jobId, plan)
  }

  const resumeImport = async (closeFirst: boolean) => {
    if (!currentJob || !plan) return
    setHandoffBusy(true)
    try {
      if (closeFirst) {
        const result = finalMessage(await runBridge("close-rekordbox"))
        if (!result.closed) throw new Error("Rekordbox did not close. Save any pending work there, then check again.")
      } else {
        const result = finalMessage(await runBridge("rekordbox-running"))
        if (result.running) throw new Error("Rekordbox is still open.")
      }
      onRekordboxStatus(false)
      onUpdateJob(currentJob.id, { state: "running", stage: "Preparing", progress: 3, summary: undefined })
      await runImport(currentJob.id, plan)
    } catch (reason) {
      const message = reason instanceof Error ? reason.message : String(reason)
      onUpdateJob(currentJob.id, { state: "blocked", stage: "Waiting for Rekordbox", summary: message })
      setError(message)
    } finally {
      setHandoffBusy(false)
    }
  }

  const reset = () => {
    setPlan(null)
    setRunningJobId(null)
    setEngineStatus("")
    setError("")
  }

  const retryImport = async () => {
    if (!currentJob || !plan) return
    setError("")
    setHandoffBusy(true)
    setEngineStatus("Refreshing source delta...")
    try {
      const refreshed = finalMessage(await runBridge("sync-plan", [currentJob.sourceUrl])) as unknown as SyncPlan
      setPlan(refreshed)
      onUpdateJob(currentJob.id, (job) => ({
        state: "running",
        stage: "Preparing",
        summary: undefined,
        tracks: job.tracks.map((track) => ({ ...track, error: undefined })),
      }))
      await runImport(currentJob.id, refreshed)
    } catch (reason) {
      const message = reason instanceof Error ? reason.message : String(reason)
      setError(message)
      onUpdateJob(currentJob.id, { state: "failed", summary: message })
    } finally {
      setHandoffBusy(false)
    }
  }

  if (currentJob) {
    return (
      <JobWorkspace
        job={currentJob}
        status={engineStatus}
        onInspect={(track) => onInspect({ kind: "track", track })}
        onNewImport={reset}
        onRetry={retryImport}
        onFinishLater={() => { reset(); onShowActivity() }}
        onCloseRekordbox={() => void resumeImport(true)}
        onCheckAgain={() => void resumeImport(false)}
        handoffBusy={handoffBusy}
      />
    )
  }

  return (
    <div className="import-flow">
    <main className={`workspace import-workspace ${plan ? "has-plan" : ""}`}>
      <section className="import-intro"><div className="import-art" aria-hidden="true"><div className="record-sleeve sleeve-back" /><div className="record-sleeve sleeve-front"><span>CRATE SELECTS</span><div className="vinyl-disc"><i /></div><small>READY FOR YOUR NEXT SET</small></div></div>
        <div className="eyebrow"><span className="live-dot" />FROM DISCOVERY TO DECKS</div>
        <h1>{plan ? "Make it yours." : "Good finds. Great sets."}</h1>
        <p>Bring your SoundCloud finds together. Download, prepare, and send them straight to Rekordbox.</p>
        <div className="import-source-label">Start with a SoundCloud link</div><form className={`url-command ${resolving ? "loading" : ""}`} onSubmit={resolveSource}>
          <div className="url-service"><AudioLines size={19} /></div>
          <input
            value={url}
            onChange={(event) => setUrl(event.target.value)}
            placeholder="https://soundcloud.com/artist/sets/playlist"
            aria-label="SoundCloud URL"
            autoFocus
          />
          {url ? <button type="button" className="clear-input" aria-label="Clear URL" onClick={() => { setUrl(""); setPlan(null) }}><X size={14} /></button> : null}
          <AppButton tone="primary" icon={ArrowRight} loading={resolving} disabled={!url.trim()}>
            Resolve
          </AppButton>
        </form>
        {resolving ? <div className="resolving-line" role="status"><LoaderCircle className="spin" size={14} /><span>{engineStatus || "Reading SoundCloud source..."}</span></div> : null}
        {error ? <InlineError message={error} onRetry={() => resolveSource()} /> : null}
      </section>

      {!plan ? (
        <RecentSources config={config} jobs={jobs} onPick={(value) => { setUrl(value); setTimeout(() => void resolveUrlDirect(value, setResolving, setError, setEngineStatus, setPlan, setPlaylistName, setOutputDir), 0) }} />
      ) : (
        <section className="resolved-source">
          <div className="section-toolbar">
            <div>
              <span className="section-kicker">Resolved source</span>
              <h2>{plan.title}</h2>
            </div>
            <AppButton tone="ghost" size="small" icon={RotateCcw} onClick={reset}>Change source</AppButton>
          </div>
          <div className="source-overview">
            <SourceIcon url={plan.url} />
            <div className="source-meta">
              <strong>{plan.title}</strong>
              <a href={plan.url} target="_blank" rel="noreferrer">{plan.url.replace("https://soundcloud.com/", "soundcloud.com/")} <ExternalLink size={11} /></a>
              <span>{plan.is_first_sync ? "First sync" : "Delta checked just now"}</span>
            </div>
            <div className="source-health"><CheckCircle2 size={16} /><span>Source available</span></div>
          </div>
          <PlanSummary plan={plan} />
          {plan.added.length ? <SourceTrackList entries={plan.added} /> : (
            <div className="up-to-date"><CheckCircle2 size={20} /><div><strong>Everything is up to date</strong><span>No new SoundCloud tracks need downloading.</span></div></div>
          )}
          <div className="import-config">
            <div className="config-row">
              <div className="config-label"><span className="config-icon"><HardDrive size={15} /></span><div><strong>Destination</strong><span>Choose a plain folder or sync into Rekordbox.</span></div></div>
              <Segmented value={destination} onChange={setDestination} options={[
                { value: "rekordbox", label: "Rekordbox", icon: Disc3 },
                { value: "download", label: "Downloads only", icon: Download },
              ]} />
            </div>
            <div className="config-row split-inputs">
              <label><span>Output folder</span><input value={outputDir} onChange={(event) => setOutputDir(event.target.value)} /></label>
              {destination === "rekordbox" ? (
                <label><span>Rekordbox playlist</span><div className="select-wrap"><select value={playlistName} onChange={(event) => setPlaylistName(event.target.value)}>
                  {!playlists.some((playlist) => playlist.name === playlistName) ? <option value={playlistName}>Create "{playlistName}"</option> : null}
                  {playlists.filter((playlist) => !playlist.is_folder).map((playlist) => <option value={playlist.name} key={playlist.id}>{playlist.path} ({playlist.song_count})</option>)}
                </select><ChevronDown size={14} /></div></label>
              ) : null}
            </div>
            <div className="config-row">
              <div className="config-label"><span className="config-icon"><WandSparkles size={15} /></span><div><strong>Analyze and tag</strong><span>BPM, alphanumeric key, energy, and metadata.</span></div></div>
              <button className={`toggle ${analyze ? "on" : ""}`} role="switch" aria-label="Analyze and tag" aria-checked={analyze} onClick={() => setAnalyze((value) => !value)}><span /></button>
            </div>
            {destination === "rekordbox" && analyze ? (
              <div className="config-row cue-policy-row">
                <div className="config-label"><span className="config-icon"><CircleDot size={15} /></span><div><strong>Hot cues</strong><span>Existing slots are never moved, renamed, or overwritten.</span></div></div>
                <Segmented value={cueMode} onChange={setCueMode} options={[
                  { value: "off", label: "Off" },
                  { value: "fill", label: "Fill empty slots" },
                ]} />
              </div>
            ) : null}
          </div>

        </section>
      )}
    </main>
    {plan ? <footer className="import-footer" aria-label="Import actions">
          <div className="import-actionbar">
            <div className="action-safety"><ShieldCheck size={16} /><span>{destination === "rekordbox" ? "A database backup is created before every write." : "No Rekordbox changes will be made."}</span></div>
            <AppButton tone="primary" icon={destination === "rekordbox" ? Disc3 : Download} onClick={startImport} disabled={!plan.added.length && plan.is_first_sync}>
              {destination === "rekordbox" ? `Import ${plan.added.length || plan.total_count} tracks` : `Download ${plan.added.length || plan.total_count} tracks`}
            </AppButton>
          </div>
    </footer> : null}
    </div>
  )
}

async function resolveUrlDirect(
  url: string,
  setResolving: (value: boolean) => void,
  setError: (value: string) => void,
  setStatus: (value: string) => void,
  setPlan: (value: SyncPlan | null) => void,
  setPlaylist: (value: string) => void,
  setOutput: (value: string) => void,
) {
  setResolving(true)
  setError("")
  try {
    const result = await runBridge("sync-plan", [url], undefined, (message) => message.message && setStatus(message.message))
    const plan = finalMessage(result) as unknown as SyncPlan
    setPlan(plan)
    setPlaylist(plan.suggested_playlist)
    setOutput(plan.target_dir)
  } catch (reason) {
    setError(reason instanceof Error ? reason.message : String(reason))
  } finally {
    setResolving(false)
    setStatus("")
  }
}

function RecentSources({ config, jobs, onPick }: { config: AppConfig | null; jobs: ActivityJob[]; onPick: (url: string) => void }) {
  const seen = new Set<string>()
  const items = jobs.filter((job) => {
    if (!job.sourceUrl || seen.has(job.sourceUrl)) return false
    seen.add(job.sourceUrl)
    return true
  }).slice(0, 3).map((job) => ({
    title: job.title,
    detail: `${job.total} track${job.total === 1 ? "" : "s"}`,
    state: job.state === "running" ? `${Math.round(job.progress)}%` : job.state === "complete" ? "Synced" : "Review",
    time: timeAgo(job.startedAt),
    url: job.sourceUrl,
    icon: ListMusic,
  }))
  if (config?.likes_url && !seen.has(config.likes_url)) items.push({
    title: config.soundcloud_username ? `@${config.soundcloud_username} Likes` : "SoundCloud Likes",
    detail: "Saved source",
    state: "Sync",
    time: "",
    url: config.likes_url,
    icon: Heart,
  })
  return (
    <section className="recent-sources">
      <div className="section-toolbar compact"><div><span className="section-kicker">Continue</span><h2>Recent sources</h2></div></div>
      <div className="recent-list">
        {items.map((item) => {
          const Icon = item.icon
          return (
            <button className="recent-row" key={item.title} onClick={() => onPick(item.url)}>
              <span className="recent-icon"><Icon size={16} /></span>
              <span className="recent-name"><strong>{item.title}</strong><small>{item.detail}</small></span>
              <span className="recent-time">{item.time}</span>
              <StatusBadge tone={item.state === "Review" ? "warning" : item.state.endsWith("%") ? "blue" : "neutral"}>{item.state}</StatusBadge>
              <ArrowRight className="row-arrow" size={15} aria-hidden="true" />
            </button>
          )
        })}
      </div>
      {!items.length ? <EmptyState icon={ListMusic} title="No recent sources" description="Resolved SoundCloud sources appear here after the first import." /> : null}
      <div className="workflow-note">
        <Zap size={15} />
        <div><strong>Your collection, kept up to date</strong><span>Come back to any source. Crate picks up new tracks and keeps the music you already have.</span></div>
      </div>
    </section>
  )
}

function InlineError({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="inline-error" role="alert">
      <AlertCircle size={16} />
      <div><strong>Could not continue</strong><span>{message}</span></div>
      {onRetry ? <AppButton tone="ghost" size="small" icon={RefreshCw} onClick={onRetry}>Retry</AppButton> : null}
    </div>
  )
}

function JobWorkspace({ job, status, onInspect, onNewImport, onRetry, onFinishLater, onCloseRekordbox, onCheckAgain, handoffBusy }: { job: ActivityJob; status: string; onInspect: (track: JobTrack) => void; onNewImport: () => void; onRetry: () => void; onFinishLater: () => void; onCloseRekordbox: () => void; onCheckAgain: () => void; handoffBusy: boolean }) {
  const failed = job.tracks.filter((track) => track.stage === "failed").length
  const protectedCount = job.tracks.filter((track) => track.stage === "protected").length
  const fallbackRecovered = job.tracks.filter((track) => track.stage === "ready" && track.downloadMethod === "klickaud").length
  const complete = job.state === "complete"
  const finished = complete || job.state === "partial"
  const analysisFailed = job.state === "failed" && job.summary?.toLowerCase().includes("analysis failed")
  const progressDetail = analysisFailed && job.progress >= 58
    ? `${job.total} downloaded · analysis interrupted`
    : `${job.completed} of ${job.total} tracks complete`
  return (
    <main className="workspace job-workspace">
      <section className="job-header">
        <div className={`job-state-icon ${complete ? "complete" : job.state === "blocked" || job.state === "failed" || job.state === "partial" ? "problem" : ""}`}>
          {complete ? <Check size={20} /> : job.state === "blocked" || job.state === "failed" || job.state === "partial" ? <TriangleAlert size={20} /> : <LoaderCircle size={20} className="spin" />}
        </div>
        <div className="job-title">
          <span className="section-kicker">{complete ? "Import complete" : job.state === "partial" ? "Source files needed" : job.state === "blocked" ? "Action required" : job.state === "failed" ? "Import failed" : "Import in progress"}</span>
          <h1>{job.title}</h1>
          <p>{job.summary || status || job.stage}</p>
        </div>
        <div className="job-header-actions">
          {finished ? <AppButton tone="secondary" icon={Plus} onClick={onNewImport}>New import</AppButton> : null}
          {job.state === "failed" || job.state === "partial" ? <AppButton tone="primary" icon={RefreshCw} disabled={handoffBusy} onClick={onRetry}>Retry sync</AppButton> : null}
        </div>
      </section>
      <section className={`job-progress-panel ${complete ? "is-complete" : ""}`}>
        <div className="job-progress-top">
          <div><strong>{job.stage}</strong><span>{progressDetail}</span></div>
          {!complete ? <strong className="progress-number">{Math.round(job.progress)}%</strong> : <StatusBadge tone="success">Ready to play</StatusBadge>}
        </div>
        {!complete ? <div className="progress-track" role="progressbar" aria-label="Import progress" aria-valuenow={Math.round(job.progress)} aria-valuemin={0} aria-valuemax={100}><span style={{ width: `${Math.max(job.progress, 2)}%` }} /></div> : null}
        <div className="job-metrics">
          <span><Clock3 size={13} />Started {timeAgo(job.startedAt)}</span>
          <span><Folder size={13} />{job.outputDir}</span>
          {job.playlistName ? <span><Disc3 size={13} />{job.playlistName}</span> : null}
          {fallbackRecovered ? <span className="metric-fallback"><RefreshCw size={13} />{fallbackRecovered} recovered by KlickAud</span> : null}
          {protectedCount ? <span className="metric-warning"><LockKeyhole size={13} />{protectedCount} protected</span> : null}
          {failed ? <span className="metric-danger"><AlertCircle size={13} />{failed} failed</span> : null}
        </div>
      </section>
      {fallbackRecovered || failed || protectedCount ? (
        <section className="download-outcome-strip" aria-label="Download fallback results" aria-live="polite">
          {fallbackRecovered ? (
            <div className="download-outcome recovered">
              <span><RefreshCw size={16} /></span>
              <div><strong>{fallbackRecovered} recovered by KlickAud</strong><small>The primary download failed, but the fallback returned a valid MP3.</small></div>
            </div>
          ) : null}
          {failed || protectedCount ? (
            <div className="download-outcome unresolved">
              <span><AlertCircle size={16} /></span>
              <div><strong>{failed + protectedCount} automatic download{failed + protectedCount === 1 ? "" : "s"} failed</strong><small>Both the primary method and KlickAud failed. Select a track for the full error.</small></div>
            </div>
          ) : null}
        </section>
      ) : null}
      {job.state === "blocked" ? (
        <div className="blocked-callout">
          <div className="blocked-icon"><Disc3 size={19} /></div>
          <div><strong>Close Rekordbox to finish this import</strong><span>{job.summary || "Downloads and analysis are safe. No database changes have been made."}</span></div>
          <AppButton tone="primary" icon={Square} loading={handoffBusy} onClick={onCloseRekordbox}>Save and close Rekordbox</AppButton>
          <AppButton tone="secondary" icon={RefreshCw} disabled={handoffBusy} onClick={onCheckAgain}>Check again</AppButton>
          <AppButton tone="ghost" disabled={handoffBusy} onClick={onFinishLater}>Finish later</AppButton>
        </div>
      ) : null}
      <TrackTable tracks={job.tracks} onInspect={onInspect} />
    </main>
  )
}

function TrackTable({ tracks, onInspect, library = false }: { tracks: JobTrack[]; onInspect: (track: JobTrack) => void; library?: boolean }) {
  return (
    <section className="data-table-wrap">
      {!library ? <div className="table-toolbar"><div><strong>Tracks</strong><span>{tracks.length} in this job</span></div></div> : null}
      <div className="track-table" role="table">
        <div className="track-table-head" role="row">
          <span className="col-status" /><span>Track</span><span>Stage</span><span>BPM</span><span>Key</span><span>Cues</span><span />
        </div>
        {tracks.map((track, index) => (
          <button className={`track-table-row ${track.stage === "failed" ? "failed" : track.stage === "protected" ? "protected" : track.downloadMethod === "klickaud" ? "fallback-recovered" : ""}`} role="row" key={track.id} onClick={() => onInspect(track)}>
            <span className="col-status">
              {track.stage === "ready" && track.downloadMethod === "klickaud" ? <RefreshCw size={15} className="fallback-icon" /> : track.stage === "ready" || track.stage === "resolved" ? <CheckCircle2 size={16} className="success-icon" /> : track.stage === "protected" ? <LockKeyhole size={15} className="warning-icon" /> : track.stage === "failed" || track.stage === "interrupted" ? <AlertCircle size={16} className="danger-icon" /> : track.stage === "queued" ? <span className="queue-dot" /> : <LoaderCircle size={15} className="spin blue-icon" />}
            </span>
            <span className="track-cell"><span className="mini-art">{String(index + 1).padStart(2, "0")}</span><span><strong title={track.title}>{track.title}</strong><small title={track.artist}>{track.artist || "Unknown artist"}</small></span></span>
            <span className="stage-cell"><span>{track.stage === "ready" && track.downloadMethod === "klickaud" ? "Ready via KlickAud" : track.stage === "fallback" ? "Trying fallback" : track.stage === "ready" ? "Ready" : track.stage.charAt(0).toUpperCase() + track.stage.slice(1)}</span>{["downloading", "converting", "analyzing", "syncing", "fallback"].includes(track.stage) ? <span className="tiny-progress"><i style={{ width: `${track.progress}%` }} /></span> : null}</span>
            <span className="mono">{track.bpm ? track.bpm.toFixed(1) : "--"}</span>
            <span className="mono key-cell">{track.key || "--"}</span>
            <span>{track.cueStatus === "filled" ? <StatusBadge tone="success">Filled</StatusBadge> : track.cueStatus === "proposed" ? <StatusBadge tone="blue">Proposed</StatusBadge> : <span className="muted">Off</span>}</span>
            <ArrowRight className="row-arrow" size={15} aria-hidden="true" />
            {track.error ? <span className="row-error-detail">{track.error}</span> : null}
          </button>
        ))}
      </div>
    </section>
  )
}

function ActivityView({ jobs, onSelectJob, onClearCompleted }: { jobs: ActivityJob[]; onSelectJob: (job: ActivityJob) => void; onClearCompleted: () => void }) {
  const running = jobs.filter((job) => job.state === "running" || job.state === "blocked")
  return (
    <main className="workspace standard-workspace">
      <PageIntro eyebrow="Job history" title="Activity" description="Follow your imports, pick up where you left off." />
      {jobs.length === 0 ? <EmptyState icon={Activity} title="No jobs yet" description="Your first SoundCloud import will appear here with track-level progress." /> : (
        <>
          {running.length ? <div className="activity-section"><div className="section-toolbar compact"><div><span className="section-kicker">Now</span><h2>In progress</h2></div></div>{running.map((job) => <JobRow key={job.id} job={job} onClick={() => onSelectJob(job)} />)}</div> : null}
          <div className="activity-section"><div className="section-toolbar compact"><div><span className="section-kicker">History</span><h2>Recent jobs</h2></div><button className="text-button" onClick={onClearCompleted}>Clear completed</button></div>
            {jobs.filter((job) => !running.includes(job)).map((job) => <JobRow key={job.id} job={job} onClick={() => onSelectJob(job)} />)}
          </div>
        </>
      )}
    </main>
  )
}

function JobRow({ job, onClick }: { job: ActivityJob; onClick: () => void }) {
  const tone = job.state === "complete" ? "success" : job.state === "partial" || job.state === "blocked" ? "warning" : job.state === "failed" ? "danger" : "blue"
  const fallbackRecovered = job.tracks.filter((track) => track.stage === "ready" && track.downloadMethod === "klickaud").length
  return (
    <button className="job-row" onClick={onClick}>
      <span className={`job-row-icon job-${tone}`}>{job.state === "complete" ? <Check size={16} /> : job.state === "running" ? <LoaderCircle size={16} className="spin" /> : <AlertCircle size={16} />}</span>
      <span className="job-row-name"><strong>{job.title}</strong><small>{job.stage} · {job.completed}/{job.total} tracks{fallbackRecovered ? ` · ${fallbackRecovered} fallback` : ""}</small></span>
      <span className="job-row-destination">{job.playlistName || job.outputDir}</span>
      <span className="job-row-progress"><i><b style={{ width: `${job.progress}%` }} /></i><small>{Math.round(job.progress)}%</small></span>
      <span className="job-row-time">{timeAgo(job.startedAt)}</span>
      <ArrowRight className="row-arrow" size={15} aria-hidden="true" />
    </button>
  )
}

function DownloadsView({ jobs, onInspect, onAddSource }: { jobs: ActivityJob[]; onInspect: (track: JobTrack) => void; onAddSource: () => void }) {
  const tracks = useMemo(() => {
    const unique = new Map<string, JobTrack>()
    for (const job of jobs) for (const track of job.tracks) {
      const key = track.id || track.path || `${track.title}:${track.artist}`
      if (["ready", "resolved"].includes(track.stage) && !unique.has(key)) unique.set(key, track)
    }
    return [...unique.values()]
  }, [jobs])
  const [sort, setSort] = useState("recent")
  const [query, setQuery] = useState("")
  const filtered = tracks.filter((track) => `${track.title} ${track.artist} ${track.key || ""}`.toLowerCase().includes(query.toLowerCase())).sort((a, b) => sort === "title" ? a.title.localeCompare(b.title) : sort === "artist" ? a.artist.localeCompare(b.artist) : sort === "bpm" ? (b.bpm ?? 0) - (a.bpm ?? 0) : 0)
  return (
    <main className="workspace standard-workspace">
      <PageIntro eyebrow="Local library" title="Downloads" description="Your music, prepared and ready for the next set." actions={<AppButton tone="primary" icon={Plus} onClick={onAddSource}>Add source</AppButton>} />
      <div className="library-toolbar">
        <label className="search-field"><Search size={14} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search tracks, artists, keys..." /></label>
        <select className="sort-select" aria-label="Sort tracks" value={sort} onChange={(event) => setSort(event.target.value)}><option value="recent">Recently added</option><option value="title">Title A–Z</option><option value="artist">Artist A–Z</option><option value="bpm">BPM high to low</option></select><span className="toolbar-count">{filtered.length} tracks</span>
      </div>
      {filtered.length ? <TrackTable tracks={filtered} onInspect={onInspect} library /> : <EmptyState icon={query ? Search : FileAudio} title={query ? "No matching tracks" : "Your collection starts here"} description={query ? "Try another title, artist, or key." : "Add a SoundCloud source to prepare your first tracks."} />}
    </main>
  )
}

function RekordboxView({ playlists, onReload, loading, onInspect, onToast }: { playlists: Playlist[]; onReload: () => void; loading: boolean; onInspect: (playlist: Playlist) => void; onToast: (message: string, tone?: ToastTone) => void }) {
  const [query, setQuery] = useState("")
  const [removing, setRemoving] = useState<Playlist | null>(null)
  const [removeBusy, setRemoveBusy] = useState(false)
  const visible = playlists.filter((playlist) => !playlist.is_folder && `${playlist.name} ${playlist.path}`.toLowerCase().includes(query.toLowerCase()))

  const removeCues = async () => {
    if (!removing) return
    setRemoveBusy(true)
    try {
      const done = finalMessage(await runBridge("remove-generated-cues", ["--playlist-id", removing.id]))
      onToast(`Removed ${asNumber(done.removed_cues)} generated cues from ${removing.name}. Manual cues were preserved.`, "success")
      setRemoving(null)
    } catch (reason) {
      onToast(reason instanceof Error ? reason.message : String(reason), "danger")
    } finally {
      setRemoveBusy(false)
    }
  }

  return (
    <main className="workspace standard-workspace">
      <PageIntro eyebrow="Collection" title="Rekordbox playlists" description="Explore your playlists, fine-tune tracks, and get ready to play." actions={<AppButton tone="secondary" icon={RefreshCw} loading={loading} onClick={onReload}>Refresh</AppButton>} />
      <div className="library-toolbar">
        <label className="search-field"><Search size={14} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search playlists..." /></label>
        <span className="toolbar-count">{visible.length} playlists</span>
      </div>
      <section className="playlist-table">
        <div className="playlist-table-head"><span>Playlist</span><span>Tracks</span><span /><span /></div>
        {visible.map((playlist) => (
          <div className="playlist-row" key={playlist.id}>
            <button className="playlist-main" onClick={() => onInspect(playlist)}><span className="playlist-icon"><ListMusic size={16} /></span><span><strong>{playlist.name}</strong>{playlist.path !== playlist.name ? <small>{playlist.path}</small> : <small>Rekordbox playlist</small>}</span></button>
            <span className="mono">{playlist.song_count}</span>
            <ArrowRight size={15} className="muted" aria-hidden="true" />
            <div className="playlist-actions"><button className="icon-button" title="Remove generated hot cues" aria-label={`Remove generated hot cues from ${playlist.name}`} onClick={() => setRemoving(playlist)}><Trash2 size={15} /></button></div>
          </div>
        ))}
      </section>
      {!visible.length ? <EmptyState icon={query ? Search : ListMusic} title={query ? "No matching playlists" : loading ? "Loading playlists…" : "No playlists yet"} description={query ? "Try a different playlist name." : "Import a source into Rekordbox to start your collection."} /> : null}
      {removing ? (
        <Modal title="Remove generated hot cues?" icon={Trash2} onClose={() => !removeBusy && setRemoving(null)}>
          <p>This removes only cues verified as generated by this app from tracks in <strong>{removing.name}</strong>.</p>
          <div className="modal-warning"><TriangleAlert size={16} /><span>Cues belong to tracks, so the change appears everywhere those tracks are used. Manual and unrecognized cues remain untouched.</span></div>
          <div className="modal-detail"><ShieldCheck size={15} /><span>A timestamped Rekordbox database backup is created first.</span></div>
          <div className="modal-actions"><AppButton tone="secondary" onClick={() => setRemoving(null)} disabled={removeBusy}>Cancel</AppButton><AppButton tone="danger" icon={Trash2} loading={removeBusy} onClick={removeCues}>Remove generated cues</AppButton></div>
        </Modal>
      ) : null}
    </main>
  )
}

function DoctorView({ onToast, usbDevices, onRekordboxStatus }: { onToast: (message: string, tone?: ToastTone) => void; usbDevices: UsbDevice[]; onRekordboxStatus: (running: boolean) => void }) {
  const [doctor, setDoctor] = useState<DoctorResult | null>(null)
  const [loading, setLoading] = useState(false)
  const [repair, setRepair] = useState<"active" | "grid" | null>(null)
  const [repairing, setRepairing] = useState(false)
  const load = useCallback(async () => {
    setLoading(true)
    try {
      const message = finalMessage(await runBridge("doctor"))
      setDoctor(asRecord(message.doctor) as DoctorResult)
    } catch (reason) {
      onToast(reason instanceof Error ? reason.message : String(reason), "danger")
    } finally {
      setLoading(false)
    }
  }, [onToast])
  useEffect(() => { void load() }, [load])
  const activeLoops = Array.isArray(doctor?.generated_active_loops) ? doctor.generated_active_loops.length : asNumber(doctor?.active_generated_loops)
  const offGridLoops = Array.isArray(doctor?.generated_off_grid_loops) ? doctor.generated_off_grid_loops.length : asNumber(doctor?.off_grid_generated_loops)
  const missingFiles = Array.isArray(doctor?.missing_files) ? doctor.missing_files.length : 0
  const issueCount = activeLoops + offGridLoops + missingFiles + (doctor?.rekordbox_running ? 1 : 0)
  const closeRekordboxNow = async () => {
    try {
      const done = finalMessage(await runBridge("close-rekordbox"))
      if (!done.closed) throw new Error("Rekordbox did not close. Save pending work there and retry.")
      onRekordboxStatus(false)
      onToast("Rekordbox closed", "success")
      await load()
    } catch (reason) {
      onToast(reason instanceof Error ? reason.message : String(reason), "danger")
    }
  }
  const applyRepair = async () => {
    if (!repair) return
    setRepairing(true)
    try {
      const command = repair === "active" ? "repair-generated-active-loops" : "repair-generated-off-grid-loops"
      const done = finalMessage(await runBridge(command))
      onToast(`Repaired ${asNumber(done.repaired)} generated loop${asNumber(done.repaired) === 1 ? "" : "s"}.`, "success")
      setRepair(null)
      await load()
    } catch (reason) {
      onToast(reason instanceof Error ? reason.message : String(reason), "danger")
    } finally {
      setRepairing(false)
    }
  }
  const usb = usbDevices[0]
  const checks = [
    { icon: Disc3, label: "Rekordbox process", detail: doctor?.rekordbox_running ? "Open and blocking writes" : "Closed; writes are available", tone: doctor?.rekordbox_running ? "warning" : "success", action: doctor?.rekordbox_running ? "Request close" : "Check again", onAction: doctor?.rekordbox_running ? closeRekordboxNow : load },
    { icon: FileAudio, label: "Collection files", detail: missingFiles ? `${missingFiles} local files are missing` : "All referenced local files are present", tone: missingFiles ? "warning" : "success" },
    { icon: CircleDot, label: "Generated active loops", detail: `${activeLoops} loops require attention`, tone: activeLoops ? "warning" : "success", action: activeLoops ? "Review" : undefined, onAction: activeLoops ? () => setRepair("active") : undefined },
    { icon: Gauge, label: "Beat-grid alignment", detail: `${offGridLoops} generated loops can be aligned`, tone: offGridLoops ? "warning" : "success", action: offGridLoops ? "Review" : undefined, onAction: offGridLoops ? () => setRepair("grid") : undefined },
    { icon: HardDrive, label: "Bundled media engine", detail: isDesktopRuntime ? "Self-contained worker and ffmpeg available" : "Browser preview uses simulated engine data", tone: "success" },
    { icon: Usb, label: "USB export", detail: usb ? `${usb.name} · ${usb.rekordbox_export ? "Rekordbox export found" : "Ready for export from Rekordbox"}` : "No removable USB device detected", tone: usb ? "success" : "neutral" },
  ] as Array<{ icon: LucideIcon; label: string; detail: string; tone: "neutral" | "success" | "warning" | "danger" | "blue"; action?: string; onAction?: () => void }>
  if (!doctor) return <main className="workspace standard-workspace"><PageIntro eyebrow="Diagnostics" title="Rekordbox Doctor" description="Check your collection before the next set." /><EmptyState icon={loading ? LoaderCircle : ShieldCheck} title={loading ? "Checking your library…" : "Could not read diagnostics"} description={loading ? "Reading local files, cues, and the Rekordbox beat grid." : "Try running the checks again."} />{!loading ? <AppButton onClick={load} icon={RefreshCw}>Run checks</AppButton> : null}</main>
  return (
    <main className="workspace standard-workspace">
      <PageIntro eyebrow="Safety and repair" title="Rekordbox Doctor" description="Inspect every repair before it touches the library." actions={<AppButton tone="secondary" icon={RefreshCw} loading={loading} onClick={load}>Run checks</AppButton>} />
      <div className="doctor-summary">
        <div className="doctor-score"><span><ShieldCheck size={22} /></span><div><strong>{issueCount ? "Review recommended" : "Library looks healthy"}</strong><small>{loading ? "Running diagnostics..." : "Last checked just now"}</small></div></div>
        <div className="doctor-stat"><strong>{Math.max(0, checks.filter((check) => check.tone === "success").length)}</strong><span>Checks passed</span></div>
        <div className="doctor-stat warning"><strong>{activeLoops + offGridLoops}</strong><span>Repairs available</span></div>
      </div>
      <section className="doctor-list">
        {checks.map((check) => {
          const Icon = check.icon
          return (
            <div className="doctor-row" key={check.label}>
              <span className="doctor-row-icon"><Icon size={17} /></span>
              <span className="doctor-row-name"><strong>{check.label}</strong><small>{check.detail}</small></span>
              <StatusBadge tone={check.tone}>{check.tone === "success" ? "Ready" : check.tone === "warning" ? "Review" : "Idle"}</StatusBadge>
              {check.action ? <AppButton tone="ghost" size="small" onClick={check.onAction}>{check.action}</AppButton> : <span />}
            </div>
          )
        })}
      </section>
      <div className="backup-callout"><ShieldCheck size={16} /><div><strong>Repairs are transactional</strong><span>Every database mutation creates a timestamped backup, applies one scoped change, and verifies the result.</span></div></div>
      {repair ? <Modal title={repair === "active" ? "Disable generated active loops?" : "Align generated loops to the beat grid?"} icon={repair === "active" ? CircleDot : Gauge} onClose={() => !repairing && setRepair(null)}>
        <p>{repair === "active" ? `${activeLoops} generated loops will stop auto-activating when a track loads.` : `${offGridLoops} generated loops will be moved onto Rekordbox beat-grid lines.`}</p>
        <div className="modal-detail"><ShieldCheck size={15} /><span>Only verified app-generated loops are changed. A database backup is created first.</span></div>
        <div className="modal-actions"><AppButton tone="secondary" onClick={() => setRepair(null)} disabled={repairing}>Cancel</AppButton><AppButton tone="primary" icon={repair === "active" ? CircleDot : Gauge} loading={repairing} onClick={applyRepair}>Apply repair</AppButton></div>
      </Modal> : null}
    </main>
  )
}

function SettingsView({ config, onSave, theme, onTheme }: { config: AppConfig | null; onSave: (config: AppConfig) => Promise<void>; theme: Theme; onTheme: (theme: Theme) => void }) {
  const [draft, setDraft] = useState<AppConfig>(config ?? mockConfig)
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState("")
  const dirty = JSON.stringify(config) !== JSON.stringify(draft)
  useEffect(() => { if (config) setDraft(config) }, [config])
  const update = <K extends keyof AppConfig>(key: K, value: AppConfig[K]) => setDraft((current) => ({ ...current, [key]: value }))
  const save = async () => {
    setSaving(true)
    setSaveError("")
    try { await onSave(draft) } catch (reason) { setSaveError(reason instanceof Error ? reason.message : String(reason)) } finally { setSaving(false) }
  }
  return (
    <main className="workspace standard-workspace settings-workspace">
      <PageIntro eyebrow="Preferences" title="Settings" description="Make Crate feel at home in your workflow." actions={<AppButton tone="primary" icon={Check} loading={saving} disabled={!dirty || !config} onClick={save}>Save changes</AppButton>} />
      {saveError ? <InlineError message={saveError} /> : null}
      <section className="settings-section">
        <div className="settings-section-title"><h2>SoundCloud</h2><p>Saved public source identity and default location.</p></div>
        <div className="settings-fields">
          <label><span>Likes username</span><input value={draft.soundcloud_username} onChange={(event) => update("soundcloud_username", event.target.value.replace(/^@/, ""))} /><small>Used to build your public likes URL.</small></label>
          <label><span>Default output folder</span><input value={draft.output_dir} onChange={(event) => update("output_dir", event.target.value)} /><small>A plain folder name in the import flow resolves under Downloads.</small></label>
        </div>
      </section>
      <section className="settings-section">
        <div className="settings-section-title"><h2>Rekordbox</h2><p>Defaults never override the review step.</p></div>
        <div className="settings-fields">
          <label><span>Default playlist</span><input value={draft.rekordbox_playlist} onChange={(event) => update("rekordbox_playlist", event.target.value)} /></label>
          <SettingsToggle label="Analyze after download" detail="Detect BPM, key, energy, phrases, and loop suggestions." value={draft.analyze_after_download} onChange={(value) => update("analyze_after_download", value)} />
          <SettingsToggle label="Write ID3 tags" detail="Store analysis metadata in local MP3 files." value={draft.write_tags} onChange={(value) => update("write_tags", value)} />
          <SettingsToggle label="Direct playlist sync" detail="Review and write to Rekordbox after creating a backup." value={draft.direct_rekordbox_push} onChange={(value) => update("direct_rekordbox_push", value)} />
        </div>
      </section>
      <section className="settings-section">
        <div className="settings-section-title"><h2>Appearance</h2><p>Optimized for long preparation sessions.</p></div>
        <div className="settings-fields"><div className="settings-inline"><span><strong>Theme</strong><small>Use a precise light or dark neutral workspace.</small></span><Segmented value={theme} onChange={onTheme} options={[{ value: "dark", label: "Dark" }, { value: "light", label: "Light" }]} /></div></div>
      </section>
      <section className="settings-section">
        <div className="settings-section-title"><h2>Engine</h2><p>Bundled and versioned with the app.</p></div>
        <div className="settings-fields"><div className="engine-info"><span className="engine-ready"><Check size={14} /></span><span><strong>Crate desktop</strong><small>MP3 audio · automatic parallel downloads</small></span><span className="mono">v0.1.0</span></div></div>
      </section>
    </main>
  )
}

function SettingsToggle({ label, detail, value, onChange }: { label: string; detail: string; value: boolean; onChange: (value: boolean) => void }) {
  return <div className="settings-inline"><span><strong>{label}</strong><small>{detail}</small></span><button className={`toggle ${value ? "on" : ""}`} role="switch" aria-label={label} aria-checked={value} onClick={() => onChange(!value)}><span /></button></div>
}

function PageIntro({ eyebrow, title, description, actions }: { eyebrow: string; title: string; description: string; actions?: ReactNode }) {
  return <section className="page-intro"><div><span className="section-kicker">{eyebrow}</span><h1>{title}</h1><p>{description}</p></div>{actions ? <div className="page-actions">{actions}</div> : null}</section>
}

function EmptyState({ icon: Icon, title, description }: { icon: LucideIcon; title: string; description: string }) {
  return <div className="empty-state"><span><Icon size={22} /></span><strong>{title}</strong><p>{description}</p></div>
}

function Modal({ title, icon: Icon, children, onClose }: { title: string; icon: LucideIcon; children: ReactNode; onClose: () => void }) {
  const dialogRef = useDialogFocus(onClose)

  return <div className="modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose() }}><div ref={dialogRef} className="modal" role="dialog" aria-modal="true" aria-labelledby="modal-title"><div className="modal-head"><span><Icon size={17} /></span><h2 id="modal-title">{title}</h2><button className="icon-button" onClick={onClose} aria-label="Close"><X size={16} /></button></div><div className="modal-body">{children}</div></div></div>
}

function LocalWaveform({ compact = false }: { seed?: string; compact?: boolean }) {
  return <div className={`local-waveform-placeholder ${compact ? "compact" : ""}`}><AudioLines size={28} aria-hidden="true" /><span>Open the editor to preview this audio</span><small>Rekordbox waveform available after analysis in Rekordbox</small></div>
}

function cuePad(cue: RekordboxCuePoint): string {
  const byName: Record<string, string> = {
    Intro: "A",
    "Phrase 16": "B",
    "Phrase 32": "C",
    "Intro Loop": "D",
    "Exit Loop": "E",
    Outro: "M",
  }
  if (byName[cue.name]) return byName[cue.name]
  if (cue.kind >= 1 && cue.kind <= 8) return String.fromCharCode(64 + cue.kind)
  return "M"
}

function cueTone(pad: string): string {
  return ({ A: "pink", B: "blue", C: "green", D: "orange", E: "amber" } as Record<string, string>)[pad] ?? "blue"
}

function RekordboxWaveformCanvas({ waveform, lane, label }: { waveform: RekordboxWaveform; lane: RekordboxWaveformLane; label: string }) {
  const canvasRef = useRef<HTMLCanvasElement>(null)

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
        durationSec: waveform.duration_sec,
        layout: lane.sample_rate_hz ? "mirrored" : "overview",
        topPadding: lane.sample_rate_hz ? 5 : 2,
        bottomPadding: lane.sample_rate_hz ? 5 : 2,
        opacity: .94,
      })

      drawRekordboxBeatGrid(context, waveform.beat_grid, {
        width: rect.width,
        height: rect.height,
        durationSec: waveform.duration_sec,
        top: 2,
        bottom: rect.height - 2,
      })

      for (const cue of waveform.cues) {
        if (cue.in_sec < 0 || cue.in_sec > waveform.duration_sec) continue
        const x = cue.in_sec / waveform.duration_sec * rect.width
        const pad = cuePad(cue)
        const color = pad === "A" ? "#f33c82" : pad === "B" ? "#38a8e8" : pad === "C" ? "#83cc45" : "#f19a23"
        if (cue.out_sec !== null && cue.out_sec > cue.in_sec) {
          const endX = Math.min(rect.width, cue.out_sec / waveform.duration_sec * rect.width)
          context.fillStyle = "rgba(241, 154, 35, 0.14)"
          context.fillRect(x, 0, Math.max(1, endX - x), rect.height)
          context.strokeStyle = "rgba(241, 154, 35, 0.78)"
          context.strokeRect(Math.round(x) + 0.5, 0.5, Math.max(1, endX - x), rect.height - 1)
        }
        context.fillStyle = color
        context.fillRect(Math.round(x), 0, 1, rect.height)
        context.fillRect(Math.max(0, Math.min(rect.width - 14, x - 7)), 2, 14, 13)
        context.fillStyle = "#080808"
        context.font = "700 8px Geist Mono, monospace"
        context.textAlign = "center"
        context.fillText(pad, Math.max(7, Math.min(rect.width - 7, x)), 11)
      }
    }
    draw()
    const observer = new ResizeObserver(draw)
    observer.observe(canvas)
    return () => observer.disconnect()
  }, [lane, waveform])

  return <canvas ref={canvasRef} className="rekordbox-waveform-canvas" aria-label={label} />
}

function PlaylistInspector({ playlist, onClose, onInspect, onOpenRekordbox }: { playlist: Playlist; onClose: () => void; onInspect: (item: InspectorItem) => void; onOpenRekordbox: () => void }) {
  const [tracks, setTracks] = useState<PlaylistTrack[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState("")

  useEffect(() => {
    let canceled = false
    setLoading(true)
    setError("")
    void runBridge("list-playlist-tracks", ["--playlist-id", playlist.id])
      .then((result) => {
        const message = finalMessage(result)
        if (!canceled) setTracks(Array.isArray(message.tracks) ? message.tracks as unknown as PlaylistTrack[] : [])
      })
      .catch((reason) => { if (!canceled) setError(reason instanceof Error ? reason.message : String(reason)) })
      .finally(() => { if (!canceled) setLoading(false) })
    return () => { canceled = true }
  }, [playlist.id])

  const missing = tracks.filter((track) => !track.file_exists).length
  return (
    <aside className="inspector">
      <div className="inspector-head"><strong>Playlist</strong><button className="icon-button" onClick={onClose} aria-label="Close inspector"><X size={15} /></button></div>
      <div className="inspector-scroll">
        <div className="playlist-inspector-art" aria-hidden="true"><ListMusic size={36} /><span className="playlist-cover-type">CRATE / COLLECTION</span></div>
        <div className="inspector-title"><span className="section-kicker">Rekordbox playlist</span><h2>{playlist.name}</h2><p>{playlist.path}</p></div>
        <div className="inspector-metrics"><div><strong>{playlist.song_count}</strong><span>Tracks</span></div><div><strong>{missing}</strong><span>Missing</span></div><div><strong>{tracks.filter((track) => track.soundcloud_dl_managed).length}</strong><span>Managed</span></div></div>
        <div className="inspector-section playlist-track-section">
          <div className="inspector-section-head"><h3>Tracks</h3>{loading ? <LoaderCircle className="spin" size={14} /> : <span>{tracks.length}</span>}</div>
          {error ? <InlineError message={error} /> : null}
          {!loading && !error && !tracks.length ? <p className="inspector-helper">This playlist is empty.</p> : null}
          <div className="inspector-track-list">
            {tracks.map((track) => (
              <button key={track.content_id} onClick={() => onInspect({ kind: "rekordbox-track", track, playlist })}>
                <span className="inspector-track-state">{track.file_exists ? <AudioLines size={13} /> : <AlertCircle size={13} />}</span>
                <span><strong>{track.title}</strong><small>{track.artist || "Unknown artist"}</small></span>
                <ArrowRight size={13} />
              </button>
            ))}
          </div>
        </div>
        <div className="inspector-actions"><AppButton tone="secondary" icon={ExternalLink} onClick={onOpenRekordbox}>Open Rekordbox</AppButton></div>
      </div>
    </aside>
  )
}

function LocalTrackInspector({ track, onClose, onEdit, onResolveProtected }: { track: JobTrack; onClose: () => void; onEdit: (target: EditorTarget) => void; onResolveProtected: (track: JobTrack) => Promise<void> }) {
  const [resolving, setResolving] = useState(false)
  const [confirmingSource, setConfirmingSource] = useState(false)
  const [resolveError, setResolveError] = useState("")

  const resolveProtected = async () => {
    setConfirmingSource(false)
    setResolving(true)
    setResolveError("")
    try {
      await onResolveProtected(track)
    } catch (reason) {
      setResolveError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setResolving(false)
    }
  }

  if (track.stage === "failed") return (
    <aside className="inspector">
      <div className="inspector-head"><strong>Failed download</strong><button className="icon-button" onClick={onClose} aria-label="Close inspector"><X size={15} /></button></div>
      <div className="inspector-scroll">
        <div className="track-inspector-top"><div className="large-art failed-art"><AlertCircle size={27} /></div><div><h2>{track.title}</h2><p>{track.artist}</p><span className="source-link">SoundCloud <ExternalLink size={10} /></span></div></div>
        <div className="download-attempt-notice failed">
          <TriangleAlert size={18} />
          <div><strong>Both download methods failed</strong><span>yt-dlp could not download this track, and KlickAud did not recover it.</span></div>
        </div>
        <div className="inspector-section download-error-section">
          <div className="inspector-section-head"><h3>Download error</h3><StatusBadge tone="danger">Failed</StatusBadge></div>
          <p>{track.error || "No detailed error was returned."}</p>
          {track.primaryDownloadError ? <div className="download-attempt-detail"><strong>Primary attempt</strong><span>{track.primaryDownloadError}</span></div> : null}
        </div>
      </div>
    </aside>
  )

  if (track.stage === "protected") return (
    <aside className="inspector">
      <div className="inspector-head"><strong>Protected track</strong><button className="icon-button" onClick={onClose} aria-label="Close inspector"><X size={15} /></button></div>
      <div className="inspector-scroll">
        <div className="track-inspector-top"><div className="large-art protected-art"><LockKeyhole size={27} /></div><div><h2>{track.title}</h2><p>{track.artist}</p><span className="source-link">SoundCloud <ExternalLink size={10} /></span></div></div>
        <div className="protected-track-notice">
          <LockKeyhole size={18} />
          <div><strong>Encrypted playback only</strong><span>SoundCloud does not expose an authorized downloadable stream for this track.</span></div>
        </div>
        {track.error ? <div className="download-attempt-detail"><strong>Automatic attempts</strong><span>{track.error}</span></div> : null}
        <div className="inspector-section protected-resolution">
          <div className="inspector-section-head"><h3>Attach a source file</h3><StatusBadge tone="warning">Required</StatusBadge></div>
          <p>Select an artist-authorized download, purchased copy, or another audio file you are licensed to use. Crate will copy or convert it, analyze it, and bind it to track ID <span className="mono">{track.id}</span>.</p>
          <div className="rights-note"><ShieldCheck size={15} /><span>You are responsible for having rights for your intended use. Non-commercial use alone does not grant download or performance rights.</span></div>
          {resolveError ? <InlineError message={resolveError} /> : null}
        </div>
        <div className="inspector-actions"><AppButton tone="primary" icon={FileUp} loading={resolving} onClick={() => setConfirmingSource(true)}>Choose licensed audio</AppButton></div>
      </div>
      {confirmingSource ? (
        <Modal title="Attach a licensed source?" icon={ShieldCheck} onClose={() => !resolving && setConfirmingSource(false)}>
          <p>Continue only with an artist-authorized download, purchased copy, promotional file, or other audio you have permission to use.</p>
          <div className="modal-warning"><TriangleAlert size={16} /><span>SoundCloud playback access does not grant download, public-performance, distribution, or commercial-use rights. Non-commercial use by itself is not permission.</span></div>
          <div className="modal-detail"><LockKeyhole size={15} /><span>The app will not decrypt SoundCloud's protected stream. It will manage only the local file you select.</span></div>
          <div className="modal-actions"><AppButton tone="secondary" onClick={() => setConfirmingSource(false)}>Cancel</AppButton><AppButton tone="primary" icon={FileUp} onClick={() => void resolveProtected()}>Choose file</AppButton></div>
        </Modal>
      ) : null}
    </aside>
  )

  return (
    <aside className="inspector">
      <div className="inspector-head"><strong>Track inspector</strong><button className="icon-button" onClick={onClose} aria-label="Close inspector"><X size={15} /></button></div>
      <div className="inspector-scroll">
        <div className="track-inspector-top"><div className="large-art"><Music2 size={27} /><span>{track.key || "--"}</span></div><div><h2>{track.title}</h2><p>{track.artist}</p><span className="source-link">SoundCloud <ExternalLink size={10} /></span></div></div>
        {track.downloadMethod === "klickaud" ? (
          <div className="download-attempt-notice recovered">
            <RefreshCw size={18} />
            <div><strong>Recovered by KlickAud</strong><span>The primary download failed, then the fallback returned and saved a valid MP3.</span></div>
          </div>
        ) : null}
        {track.downloadMethod === "klickaud" && track.primaryDownloadError ? <div className="download-attempt-detail"><strong>Primary attempt</strong><span>{track.primaryDownloadError}</span></div> : null}
        {track.stage === "interrupted" ? <InlineError message={track.error || "Import interrupted. Retry sync to continue."} /> : null}
        <div className="wave-source-row"><StatusBadge tone="neutral">Local preview</StatusBadge><span>Available before Rekordbox analysis</span></div>
        <LocalWaveform seed={track.title} />
        <div className="inspector-metrics"><div><strong className="mono">{track.bpm?.toFixed(1) || "--"}</strong><span>BPM</span></div><div><strong className="mono">{track.key || "--"}</strong><span>Key</span></div><div><strong>{track.path ? track.path.split(".").pop()?.toUpperCase() : "—"}</strong><span>Format</span></div></div>
        <div className="inspector-section cue-preview"><div className="inspector-section-head"><h3>Hot cues</h3><StatusBadge tone={track.cueStatus === "filled" ? "success" : "neutral"}>{track.cueStatus}</StatusBadge></div>
          <p className="inspector-helper">{track.cueStatus === "off" ? "Hot-cue generation is off for this import." : "Open this track from Rekordbox playlists to inspect the actual saved cues and their timing."}</p>
        </div>
        <div className="cue-safety-note"><ShieldCheck size={15} /><span>Existing occupied slots are always preserved.</span></div>
        <div className="inspector-actions"><AppButton tone="primary" icon={SlidersHorizontal} disabled={!track.path} onClick={() => track.path && onEdit({ path: track.path, title: track.title, artist: track.artist })}>Open editor</AppButton></div>
      </div>
    </aside>
  )
}

function RekordboxTrackInspector({ track, playlist, onClose, onBack, onEdit }: { onBack: () => void; track: PlaylistTrack; playlist: Playlist; onClose: () => void; onEdit: (target: EditorTarget) => void }) {
  const [waveform, setWaveform] = useState<RekordboxWaveform | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState("")

  useEffect(() => {
    let canceled = false
    const load = async (initial: boolean) => {
      if (initial) {
        setLoading(true)
        setError("")
      }
      try {
        const message = finalMessage(await runBridge("rekordbox-waveform", ["--content-id", track.content_id, "--max-points", "2400"]))
        if (!canceled) {
          setWaveform(asRecord(message.waveform) as unknown as RekordboxWaveform)
          setError("")
        }
      } catch (reason) {
        if (!canceled && initial) setError(reason instanceof Error ? reason.message : String(reason))
      } finally {
        if (!canceled && initial) setLoading(false)
      }
    }
    void load(true)
    const refresh = window.setInterval(() => void load(false), 15_000)
    return () => { canceled = true; window.clearInterval(refresh) }
  }, [track.content_id])

  const bpm = waveform?.beat_grid.find((beat) => beat.bpm > 0)?.bpm
  return (
    <aside className="inspector">
      <div className="inspector-head"><button className="icon-button" aria-label="Back to playlist" onClick={onBack}><ArrowLeft size={16} /></button><strong>Track details</strong><button className="icon-button" onClick={onClose} aria-label="Close inspector"><X size={15} /></button></div>
      <div className="inspector-scroll">
        <div className="track-inspector-top"><div className="large-art"><Disc3 size={27} /><span>RB</span></div><div><h2>{track.title}</h2><p>{track.artist || "Unknown artist"}</p><span className="source-link">{playlist.name}</span></div></div>
        <div className="wave-source-row">{waveform ? <><StatusBadge tone="success">Rekordbox analysis</StatusBadge><span>{waveformFormatLabel(waveform.preview)} overview · {waveformFormatLabel(waveform.detail)} detail</span></> : <><StatusBadge tone="neutral">Awaiting analysis</StatusBadge><span>No authoritative ANLZ waveform yet</span></>}</div>
        {loading ? <div className="waveform-loading"><LoaderCircle className="spin" size={16} /><span>Reading Rekordbox ANLZ data...</span></div> : null}
        {error ? <InlineError message={error} /> : null}
        {waveform?.preview ? <div className="authoritative-waveform"><span>Full track</span><RekordboxWaveformCanvas waveform={waveform} lane={waveform.preview} label={`${track.title} native Rekordbox overview waveform`} /></div> : null}
        {waveform?.detail ? <div className="authoritative-waveform detail-waveform"><span>Detail</span><RekordboxWaveformCanvas waveform={waveform} lane={waveform.detail} label={`${track.title} native Rekordbox detail waveform`} /></div> : null}
        {waveform ? <div className="wave-legend"><span><i className="legend-downbeat" />PQTZ downbeat</span><span><i className="legend-cue" />Database cue</span><span><i className="legend-loop" />Loop range</span></div> : null}
        <div className="inspector-metrics"><div><strong className="mono">{bpm?.toFixed(1) || "--"}</strong><span>BPM</span></div><div><strong>{waveform?.beat_grid.length ?? "--"}</strong><span>Beats</span></div><div><strong className="mono">{formatDuration(waveform?.duration_sec ?? 0)}</strong><span>Length</span></div></div>
        {waveform ? <div className="inspector-section cue-preview"><div className="inspector-section-head"><h3>Database cues</h3><StatusBadge tone="success">Exact timing</StatusBadge></div>
          {waveform.cues.map((cue) => { const pad = cuePad(cue); return <div className="cue-row" key={cue.id}><span className={`cue-pad cue-${cueTone(pad)}`}>{pad}</span><strong>{cue.name || "Unnamed cue"}</strong><span className="mono">{formatTimestamp(cue.in_sec)}{cue.out_sec !== null ? ` - ${formatTimestamp(cue.out_sec)}` : ""}</span></div> })}
          {!waveform.cues.length ? <p className="inspector-helper">No database cues are set.</p> : null}
        </div> : null}
        {waveform ? <div className="anlz-proof"><ShieldCheck size={14} /><span><strong>Authoritative Rekordbox data</strong><small>{waveform.fingerprint.slice(0, 12)} · {waveform.files.map((file) => file.kind).join(" + ")}</small></span></div> : null}
        {!track.file_exists ? <div className="cue-safety-note missing-file-note"><AlertCircle size={15} /><span>The audio file is missing, but Rekordbox's cached waveform remains inspectable.</span></div> : null}
        <div className="inspector-actions"><AppButton tone="primary" icon={SlidersHorizontal} disabled={!track.file_exists || !waveform} onClick={() => onEdit({ contentId: track.content_id, path: track.folder_path, title: track.title, artist: track.artist })}>Open editor</AppButton></div>
      </div>
    </aside>
  )
}

function Inspector({ item, onClose, onInspect, onOpenRekordbox, onEdit, onResolveProtected }: { item: InspectorItem; onClose: () => void; onInspect: (item: InspectorItem) => void; onOpenRekordbox: () => void; onEdit: (target: EditorTarget) => void; onResolveProtected: (track: JobTrack) => Promise<void> }) {
  if (!item) return (
    <aside className="inspector empty-inspector">
      <div className="inspector-head"><strong>Inspector</strong><button className="icon-button" onClick={onClose} aria-label="Close inspector"><X size={15} /></button></div>
      <div className="inspector-empty"><AudioLines size={24} /><strong>Nothing selected</strong><span>Select a track or playlist to inspect it without leaving your place.</span></div>
    </aside>
  )
  if (item.kind === "playlist") return <PlaylistInspector playlist={item.playlist} onClose={onClose} onInspect={onInspect} onOpenRekordbox={onOpenRekordbox} />
  if (item.kind === "rekordbox-track") return <RekordboxTrackInspector track={item.track} playlist={item.playlist} onBack={() => onInspect({ kind: "playlist", playlist: item.playlist })} onClose={onClose} onEdit={onEdit} />
  return <LocalTrackInspector track={item.track} onClose={onClose} onEdit={onEdit} onResolveProtected={onResolveProtected} />
}

type ToastTone = "success" | "danger" | "neutral"
interface ToastItem { id: string; message: string; tone: ToastTone }

function Toasts({ items, onDismiss }: { items: ToastItem[]; onDismiss: (id: string) => void }) {
  return <div className="toasts" aria-live="polite">{items.map((item) => <div className={`toast toast-${item.tone}`} key={item.id}>{item.tone === "success" ? <CheckCircle2 size={16} /> : item.tone === "danger" ? <AlertCircle size={16} /> : <Info size={16} />}<span>{item.message}</span><button onClick={() => onDismiss(item.id)} aria-label="Dismiss"><X size={14} /></button></div>)}</div>
}

function Statusbar({ jobs, engineReady, rekordboxRunning, usbDevices }: { jobs: ActivityJob[]; engineReady: boolean; rekordboxRunning: boolean | null; usbDevices: UsbDevice[] }) {
  const active = jobs.find((job) => job.state === "running")
  const usb = usbDevices[0]
  return <footer className="statusbar"><span className={engineReady ? "status-ready" : ""}><i />{engineReady ? "Engine ready" : "Starting engine"}</span><span><Disc3 size={12} />Rekordbox {rekordboxRunning ? "open" : rekordboxRunning === false ? "closed" : "checking"}</span><span><Usb size={12} />{usb ? `${usb.name} · ${usb.rekordbox_export ? "export ready" : "detected"}` : "No USB"}</span><span className="statusbar-spacer" />{active ? <span className="active-job-status"><LoaderCircle size={12} className="spin" />{active.title}: {Math.round(active.progress)}%</span> : <span><HardDrive size={12} />Local only</span>}<span className="mono">v0.1.0</span></footer>
}

export function App() {
  const [view, setView] = useState<ViewId>("import")
  const [config, setConfig] = useState<AppConfig | null>(null)
  const [playlists, setPlaylists] = useState<Playlist[]>([])
  const [playlistsLoading, setPlaylistsLoading] = useState(false)
  const [jobs, setJobs] = useState<ActivityJob[]>(loadStoredJobs)
  const [hiddenJobs, setHiddenJobs] = useState<string[]>(() => { try { return JSON.parse(localStorage.getItem("crate.hidden-jobs") || "[]") } catch { return [] } })
  const [inspector, setInspector] = useState<InspectorItem>(null)
  const [inspectorOpen, setInspectorOpen] = useState(false)
  const [paletteOpen, setPaletteOpen] = useState(false)
  const [rekordboxRunning, setRekordboxRunning] = useState<boolean | null>(null)
  const [engineReady, setEngineReady] = useState(false)
  const [usbDevices, setUsbDevices] = useState<UsbDevice[]>([])
  const [toasts, setToasts] = useState<ToastItem[]>([])
  const [theme, setTheme] = useState<Theme>(() => (localStorage.getItem(THEME_KEY) as Theme) || "dark")
  const [initialUrl, setInitialUrl] = useState("")
  const [resumeJob, setResumeJob] = useState<ActivityJob | null>(null)
  const [editorTarget, setEditorTarget] = useState<EditorTarget | null>(null)
  const toastTimers = useRef<Map<string, number>>(new Map())

  const showToast = useCallback((message: string, tone: ToastTone = "neutral") => {
    const id = createId("toast")
    setToasts((current) => [...current, { id, message, tone }])
    const timer = window.setTimeout(() => { setToasts((current) => current.filter((item) => item.id !== id)); toastTimers.current.delete(id) }, 6000)
    toastTimers.current.set(id, timer)
  }, [])

  const dismissToast = (id: string) => {
    const timer = toastTimers.current.get(id)
    if (timer) window.clearTimeout(timer)
    toastTimers.current.delete(id)
    setToasts((current) => current.filter((item) => item.id !== id))
  }

  const loadPlaylists = useCallback(async () => {
    setPlaylistsLoading(true)
    try {
      const message = finalMessage(await runBridge("list-playlists"))
      setPlaylists(Array.isArray(message.playlists) ? message.playlists as unknown as Playlist[] : [])
    } catch (reason) {
      showToast(reason instanceof Error ? reason.message : String(reason), "danger")
    } finally {
      setPlaylistsLoading(false)
    }
  }, [showToast])

  const loadUsbDevices = useCallback(async () => {
    try {
      const message = finalMessage(await runBridge("usb-status"))
      setUsbDevices(Array.isArray(message.devices) ? message.devices as unknown as UsbDevice[] : [])
    } catch {
      setUsbDevices([])
    }
  }, [])

  useEffect(() => {
    let canceled = false
    const initialize = async () => {
      try {
        const configMessage = finalMessage(await runBridge("config"))
        if (!canceled) setConfig(asRecord(configMessage.config) as unknown as AppConfig)
        const statusMessage = finalMessage(await runBridge("rekordbox-running"))
        if (!canceled) setRekordboxRunning(Boolean(statusMessage.running))
        if (!canceled) setEngineReady(true)
        await loadPlaylists()
        await loadUsbDevices()
      } catch (reason) {
        if (!canceled) {
          if (!isDesktopRuntime) setConfig(mockConfig)
          showToast(reason instanceof Error ? reason.message : String(reason), "danger")
        }
      }
    }
    void initialize()
    return () => { canceled = true }
  }, [loadPlaylists, loadUsbDevices, showToast])

  useEffect(() => {
    const timer = window.setInterval(() => void loadUsbDevices(), 15_000)
    return () => window.clearInterval(timer)
  }, [loadUsbDevices])

  useEffect(() => {
    localStorage.setItem(JOBS_KEY, JSON.stringify(jobs.slice(0, 40)))
  }, [jobs])

  useEffect(() => {
    document.documentElement.dataset.theme = theme
    localStorage.setItem(THEME_KEY, theme)
  }, [theme])

  useEffect(() => {
    const listener = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault()
        setPaletteOpen((value) => !value)
      }
      if ((event.metaKey || event.ctrlKey) && event.altKey && event.key.toLowerCase() === "i") {
        event.preventDefault()
        setInspectorOpen((value) => !value)
      }
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "n") {
        event.preventDefault()
        setEditorTarget(null)
        setInspectorOpen(false)
        setView("import")
      }
      if (event.key === "Escape" && paletteOpen) setPaletteOpen(false)
    }
    window.addEventListener("keydown", listener)
    return () => window.removeEventListener("keydown", listener)
  }, [paletteOpen])

  const createJob = (job: ActivityJob) => setJobs((current) => [job, ...current.filter((item) => item.id !== job.id)])
  const updateJob = (id: string, update: Partial<ActivityJob> | ((job: ActivityJob) => Partial<ActivityJob>)) => {
    setJobs((current) => current.map((job) => job.id === id ? { ...job, ...(typeof update === "function" ? update(job) : update) } : job))
  }
  const inspect = (item: InspectorItem) => { setInspector(item); setInspectorOpen(true) }

  const resolveProtectedTrack = async (track: JobTrack) => {
    const source = await selectReplacementAudio()
    if (!source) return
    const job = jobs.find((candidate) => candidate.tracks.some((item) => item.id === track.id))
    if (!job) throw new Error("The import job for this track is no longer available.")
    const done = finalMessage(await runBridge("import-replacement", [], {
      source,
      output_dir: job.outputDir,
      track_id: track.id,
      title: track.title,
    }))
    const feature = asRecord(done.features) as unknown as TrackFeature
    const updated: JobTrack = {
      ...track,
      stage: "resolved",
      progress: 100,
      path: asString(done.path),
      bpm: feature.bpm,
      key: feature.musical_key,
      cueStatus: job.cueMode === "fill" ? "proposed" : "off",
      error: undefined,
    }
    const remaining = job.tracks.filter((item) => item.stage === "protected" && item.id !== track.id).length
    updateJob(job.id, (current) => ({
      summary: remaining
        ? `${remaining} protected track(s) still need source files. Retry sync after resolving them.`
        : "All protected tracks have source files. Retry sync to finish Rekordbox import.",
      tracks: current.tracks.map((item) => item.id === track.id ? updated : item),
    }))
    setInspector({ kind: "track", track: updated })
    showToast(`${track.title} is analyzed and ready for Retry sync.`, "success")
  }

  const saveConfig = async (next: AppConfig) => {
    const message = finalMessage(await runBridge("save-config", [], next as unknown as Record<string, unknown>))
    setConfig(asRecord(message.config) as unknown as AppConfig)
    showToast("Settings saved", "success")
  }

  const openRekordboxNow = async () => {
    try {
      const message = finalMessage(await runBridge("open-rekordbox"))
      if (!message.opened) throw new Error("Rekordbox could not be opened.")
      setRekordboxRunning(true)
      showToast("Rekordbox opened", "success")
    } catch (reason) {
      showToast(reason instanceof Error ? reason.message : String(reason), "danger")
    }
  }

  const navigate = (next: ViewId) => {
    setEditorTarget(null)
    setView(next)
    setInspectorOpen(false)
    setResumeJob(null)
    if (next === "import") setInitialUrl("")
  }

  const openLikes = () => {
    setEditorTarget(null)
    setInspectorOpen(false)
    setResumeJob(null)
    setInitialUrl(config?.likes_url || "")
    setView("import")
  }

  const consumeResumeJob = useCallback(() => setResumeJob(null), [])

  const openJob = (job: ActivityJob) => {
    if (job) {
      setInitialUrl("")
      setResumeJob(job)
      setView("import")
    }
  }

  const content = (() => {
    if (view === "import") return null

    if (view === "activity") return <ActivityView jobs={jobs.filter((job) => !hiddenJobs.includes(job.id))} onSelectJob={openJob} onClearCompleted={() => { const hidden = jobs.filter((job) => job.state === "complete").map((job) => job.id); setHiddenJobs(hidden); localStorage.setItem("crate.hidden-jobs", JSON.stringify(hidden)); showToast("Completed imports hidden. Your downloads are still in the library.") }} />
    if (view === "downloads") return <DownloadsView jobs={jobs} onInspect={(track) => inspect({ kind: "track", track })} onAddSource={() => navigate("import")} />
    if (view === "rekordbox") return <RekordboxView playlists={playlists} onReload={loadPlaylists} loading={playlistsLoading} onInspect={(playlist) => inspect({ kind: "playlist", playlist })} onToast={showToast} />
    if (view === "doctor") return <DoctorView onToast={showToast} usbDevices={usbDevices} onRekordboxStatus={setRekordboxRunning} />
    return <SettingsView config={config} onSave={saveConfig} theme={theme} onTheme={setTheme} />
  })()

  return (
    <div className={`app-shell ${inspectorOpen && inspector && !editorTarget ? "with-inspector" : ""} ${editorTarget ? "editor-mode" : ""}`}>
      <Sidebar active={view} onChange={navigate} onLikes={openLikes} jobs={jobs} config={config} />
      <div className="main-column">
        {editorTarget ? <TrackEditor target={editorTarget} rekordboxRunning={rekordboxRunning} onClose={() => setEditorTarget(null)} onRekordboxStatus={setRekordboxRunning} onToast={showToast} /> : <><Topbar hasSelection={Boolean(inspector)} view={view} inspectorOpen={inspectorOpen} onToggleInspector={() => setInspectorOpen((value) => !value)} onOpenPalette={() => setPaletteOpen(true)} />{content}</>}<div className="import-page" hidden={view !== "import" || Boolean(editorTarget)}>{<ImportWorkspace config={config} playlists={playlists} jobs={jobs} onCreateJob={createJob} onUpdateJob={updateJob} onInspect={inspect} initialUrl={initialUrl} resumeJob={resumeJob} onResumeJobConsumed={consumeResumeJob} onShowActivity={() => setView("activity")} rekordboxRunning={rekordboxRunning} onRekordboxStatus={setRekordboxRunning} />}</div>
      </div>
      {inspectorOpen && inspector && !editorTarget ? <Inspector item={inspector} onClose={() => setInspectorOpen(false)} onInspect={inspect} onOpenRekordbox={() => void openRekordboxNow()} onEdit={setEditorTarget} onResolveProtected={resolveProtectedTrack} /> : null}
      <Statusbar jobs={jobs} engineReady={engineReady} rekordboxRunning={rekordboxRunning} usbDevices={usbDevices} />
      {paletteOpen ? <CommandPalette onClose={() => setPaletteOpen(false)} onNavigate={navigate} /> : null}
      <Toasts items={toasts} onDismiss={dismissToast} />
    </div>
  )
}
