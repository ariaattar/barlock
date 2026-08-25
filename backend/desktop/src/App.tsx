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
  Command,
  Disc3,
  Download,
  ExternalLink,
  FileAudio,
  Folder,
  Gauge,
  HardDrive,
  Heart,
  Info,
  ListMusic,
  LoaderCircle,
  MoreHorizontal,
  Music2,
  PanelRightClose,
  PanelRightOpen,
  Plus,
  RefreshCw,
  RotateCcw,
  Search,
  Settings,
  ShieldCheck,
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
import { finalMessage, isDesktopRuntime, runBridge } from "./lib/bridge"
import { mockConfig } from "./lib/mock"
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

interface NavItem {
  id: ViewId
  label: string
  icon: LucideIcon
  badge?: number
}

function asString(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback
}

function asNumber(value: unknown, fallback = 0): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" ? value as Record<string, unknown> : {}
}

function loadStoredJobs(): ActivityJob[] {
  try {
    const value = JSON.parse(localStorage.getItem(JOBS_KEY) ?? "[]")
    if (!Array.isArray(value)) return []
    return value.map((job: ActivityJob) => job.state === "running" ? {
      ...job,
      state: "failed",
      stage: "Interrupted",
      summary: "The app closed before this job finished. Resolve the source again to retry its remaining delta.",
    } : job)
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

function AppButton({
  children,
  icon: Icon,
  tone = "secondary",
  size = "default",
  loading = false,
  ...props
}: {
  children?: ReactNode
  icon?: LucideIcon
  tone?: "primary" | "secondary" | "ghost" | "danger"
  size?: "default" | "small" | "icon"
  loading?: boolean
} & React.ButtonHTMLAttributes<HTMLButtonElement>) {
  const CurrentIcon = loading ? LoaderCircle : Icon
  return (
    <button className={`button button-${tone} button-${size}`} {...props} disabled={props.disabled || loading}>
      {CurrentIcon ? <CurrentIcon size={size === "small" ? 14 : 16} className={loading ? "spin" : ""} aria-hidden="true" /> : null}
      {children ? <span>{children}</span> : null}
    </button>
  )
}

function StatusBadge({ tone, children }: { tone: "neutral" | "success" | "warning" | "danger" | "blue"; children: ReactNode }) {
  return <span className={`status-badge status-${tone}`}><span className="status-dot" />{children}</span>
}

function AppLogo() {
  return (
    <div className="brand-lockup">
      <div className="brand-mark" aria-hidden="true">
        <span />
        <span />
        <span />
        <span />
        <span />
      </div>
      <div>
        <strong>SoundCloud DL</strong>
        <span>Rekordbox prep</span>
      </div>
    </div>
  )
}

function Sidebar({ active, onChange, onLikes, jobs, config }: { active: ViewId; onChange: (id: ViewId) => void; onLikes: () => void; jobs: ActivityJob[]; config: AppConfig | null }) {
  const activeJobs = jobs.filter((job) => job.state === "running" || job.state === "blocked").length
  const nav: NavItem[] = [
    { id: "import", label: "New import", icon: Plus },
    { id: "activity", label: "Activity", icon: Activity, badge: activeJobs || undefined },
    { id: "downloads", label: "Downloads", icon: Download },
    { id: "rekordbox", label: "Rekordbox", icon: Disc3 },
    { id: "doctor", label: "Doctor", icon: ShieldCheck },
  ]
  return (
    <aside className="sidebar">
      <div className="sidebar-drag" data-tauri-drag-region />
      <AppLogo />
      <nav className="sidebar-nav" aria-label="Primary">
        <div className="nav-section-label">Workspace</div>
        {nav.map((item) => {
          const Icon = item.icon
          return (
            <button key={item.id} className={`nav-item ${active === item.id ? "active" : ""}`} onClick={() => onChange(item.id)}>
              <Icon size={16} aria-hidden="true" />
              <span>{item.label}</span>
              {item.badge ? <span className="nav-badge">{item.badge}</span> : null}
            </button>
          )
        })}
      </nav>
      <div className="sidebar-spacer" />
      {config?.likes_url ? (
        <button className="saved-source" onClick={onLikes}>
          <span className="saved-source-icon"><Heart size={14} /></span>
          <span><strong>@{config.soundcloud_username}</strong><small>Likes source</small></span>
        </button>
      ) : null}
      <button className={`nav-item settings-link ${active === "settings" ? "active" : ""}`} onClick={() => onChange("settings")}>
        <Settings size={16} aria-hidden="true" />
        <span>Settings</span>
      </button>
      <div className="user-block">
        <div className="user-avatar">AA</div>
        <div><strong>Local library</strong><span>{isDesktopRuntime ? "Desktop engine" : "Preview mode"}</span></div>
        <MoreHorizontal size={16} />
      </div>
    </aside>
  )
}

function Topbar({
  view,
  inspectorOpen,
  onToggleInspector,
  onOpenPalette,
  rekordboxRunning,
}: {
  view: ViewId
  inspectorOpen: boolean
  onToggleInspector: () => void
  onOpenPalette: () => void
  rekordboxRunning: boolean | null
}) {
  const titles: Record<ViewId, { title: string; description: string }> = {
    import: { title: "New import", description: "SoundCloud to Rekordbox" },
    activity: { title: "Activity", description: "Jobs and exceptions" },
    downloads: { title: "Downloads", description: "Local prepared tracks" },
    rekordbox: { title: "Rekordbox", description: "Playlists and cue safety" },
    doctor: { title: "Doctor", description: "Diagnostics and repairs" },
    settings: { title: "Settings", description: "Storage and defaults" },
  }
  const current = titles[view]
  return (
    <header className="topbar" data-tauri-drag-region>
      <div className="window-controls-space" data-tauri-drag-region />
      <button className="icon-button subtle" aria-label="Back" disabled><ArrowLeft size={16} /></button>
      <div className="view-heading" data-tauri-drag-region>
        <strong>{current.title}</strong>
        <span>{current.description}</span>
      </div>
      <div className="topbar-spacer" data-tauri-drag-region />
      <button className="command-trigger" onClick={onOpenPalette}>
        <Search size={14} />
        <span>Search or run a command</span>
        <kbd><Command size={11} />K</kbd>
      </button>
      <StatusBadge tone={rekordboxRunning ? "warning" : rekordboxRunning === false ? "success" : "neutral"}>
        Rekordbox {rekordboxRunning ? "open" : rekordboxRunning === false ? "closed" : "checking"}
      </StatusBadge>
      <button className="icon-button" onClick={onToggleInspector} aria-label={inspectorOpen ? "Close inspector" : "Open inspector"} title={inspectorOpen ? "Close inspector" : "Open inspector"}>
        {inspectorOpen ? <PanelRightClose size={16} /> : <PanelRightOpen size={16} />}
      </button>
    </header>
  )
}

function Segmented<T extends string>({ value, onChange, options }: { value: T; onChange: (value: T) => void; options: { value: T; label: string; icon?: LucideIcon }[] }) {
  return (
    <div className="segmented">
      {options.map((option) => {
        const Icon = option.icon
        return (
          <button key={option.value} className={value === option.value ? "active" : ""} onClick={() => onChange(option.value)}>
            {Icon ? <Icon size={14} /> : null}{option.label}
          </button>
        )
      })}
    </div>
  )
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
      <div className={plan.removed_ids.length ? "plan-warning" : ""}><strong>{plan.removed_ids.length}</strong><span>Removed upstream</span></div>
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
          <span className="track-mini-wave"><i /><i /><i /><i /><i /><i /><i /><i /></span>
          <span className="source-track-title">{entry.title || entry.label}</span>
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
    setHandoffBusy(true)
    setEngineStatus("Refreshing source delta...")
    setError("")

    const rehydrate = async () => {
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
    if (message.event === "status" && message.message) {
      setEngineStatus(message.message)
      const value = message.message
      onUpdateJob(jobId, (job) => {
        const tracks = job.tracks.map((track) => ({ ...track }))
        let stage = job.stage
        let progress = job.progress
        const urlMatch = value.match(/(?:downloading|finished) (https?:\/\/\S+)$/)
        const analysisMatch = value.match(/analyzing (.+)$/i)
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
        if (value.toLowerCase().includes("analyzing")) {
          stage = "Analyzing"
          progress = Math.max(progress, 58)
          if (analysisMatch) {
            const index = tracks.findIndex((track) => analysisMatch[1].includes(track.title))
            if (index >= 0) {
              tracks[index].stage = "analyzing"
              tracks[index].progress = 76
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
      const featureRaw = Array.isArray(done.features) ? done.features : []
      const features = featureRaw.map((value) => asRecord(value) as unknown as TrackFeature)
      onUpdateJob(jobId, (existing) => ({
        state: failed.length ? "partial" : "complete",
        stage: failed.length ? "Complete with exceptions" : "Complete",
        progress: 100,
        completed: Math.max(existing.total - failed.length, 0),
        summary: destination === "rekordbox"
          ? `${asNumber(asRecord(done.push).added_to_playlist, activePlan.added.length)} added to ${playlistName}`
          : `${Math.max(activePlan.added.length - failed.length, 0)} downloaded to ${outputDir}`,
        tracks: existing.tracks.map((track, index) => {
          const failure = failed.find((item) => asString(item.id) === track.id || asString(item.title) === track.title || asString(item.label) === track.title)
          const feature = features.find((item) => item.title === track.title) ?? features[index]
          return failure ? {
            ...track,
            stage: "failed",
            progress: 100,
            error: asString(failure.error, "Download unavailable"),
          } : {
            ...track,
            stage: "ready",
            progress: 100,
            bpm: feature?.bpm,
            key: feature?.musical_key,
            cueStatus: cueMode === "fill" && destination === "rekordbox" ? "filled" : "off",
          }
        }),
      }))
      setEngineStatus(failed.length ? `${failed.length} track(s) need attention` : "Import complete")
    } catch (reason) {
      const message = reason instanceof Error ? reason.message : String(reason)
      const blocked = message.toLowerCase().includes("rekordbox") && message.toLowerCase().includes("close")
      onUpdateJob(jobId, {
        state: blocked ? "blocked" : "failed",
        stage: blocked ? "Waiting for Rekordbox" : "Failed",
        summary: message,
      })
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
      artist: "SoundCloud",
      stage: "queued",
      progress: 0,
      cueStatus: cueMode === "fill" ? "proposed" : "off",
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

  if (currentJob) {
    return (
      <JobWorkspace
        job={currentJob}
        status={engineStatus}
        onInspect={(track) => onInspect({ kind: "track", track })}
        onNewImport={reset}
        onFinishLater={() => { reset(); onShowActivity() }}
        onCloseRekordbox={() => void resumeImport(true)}
        onCheckAgain={() => void resumeImport(false)}
        handoffBusy={handoffBusy}
      />
    )
  }

  return (
    <main className="workspace import-workspace">
      <section className="import-intro">
        <div className="eyebrow"><span className="live-dot" />Ready for a new source</div>
        <h1>Bring a set into Rekordbox.</h1>
        <p>Paste a public SoundCloud track, playlist, or likes URL. Existing downloads and playlist rows stay untouched.</p>
        <form className={`url-command ${resolving ? "loading" : ""}`} onSubmit={resolveSource}>
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
        {resolving ? <div className="resolving-line"><LoaderCircle className="spin" size={14} /><span>{engineStatus || "Reading SoundCloud source..."}</span></div> : null}
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
              <button className={`toggle ${analyze ? "on" : ""}`} role="switch" aria-checked={analyze} onClick={() => setAnalyze((value) => !value)}><span /></button>
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
          <div className="import-actionbar">
            <div className="action-safety"><ShieldCheck size={16} /><span>{destination === "rekordbox" ? "A database backup is created before every write." : "No Rekordbox changes will be made."}</span></div>
            <AppButton tone="primary" icon={destination === "rekordbox" ? Disc3 : Download} onClick={startImport} disabled={!plan.added.length && plan.is_first_sync}>
              {destination === "rekordbox" ? `Import ${plan.added.length || plan.total_count} tracks` : `Download ${plan.added.length || plan.total_count} tracks`}
            </AppButton>
          </div>
        </section>
      )}
    </main>
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
              <span className="row-arrow">-&gt;</span>
            </button>
          )
        })}
      </div>
      {!items.length ? <EmptyState icon={ListMusic} title="No recent sources" description="Resolved SoundCloud sources appear here after the first import." /> : null}
      <div className="workflow-note">
        <Zap size={15} />
        <div><strong>Delta-aware by default</strong><span>Repeat playlist runs resolve the full source but download only new track IDs.</span></div>
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

function JobWorkspace({ job, status, onInspect, onNewImport, onFinishLater, onCloseRekordbox, onCheckAgain, handoffBusy }: { job: ActivityJob; status: string; onInspect: (track: JobTrack) => void; onNewImport: () => void; onFinishLater: () => void; onCloseRekordbox: () => void; onCheckAgain: () => void; handoffBusy: boolean }) {
  const failed = job.tracks.filter((track) => track.stage === "failed").length
  const complete = job.state === "complete" || job.state === "partial"
  return (
    <main className="workspace job-workspace">
      <section className="job-header">
        <div className={`job-state-icon ${complete ? "complete" : job.state === "blocked" || job.state === "failed" ? "problem" : ""}`}>
          {complete ? <Check size={20} /> : job.state === "blocked" || job.state === "failed" ? <TriangleAlert size={20} /> : <LoaderCircle size={20} className="spin" />}
        </div>
        <div className="job-title">
          <span className="section-kicker">{complete ? "Import complete" : job.state === "blocked" ? "Action required" : "Import in progress"}</span>
          <h1>{job.title}</h1>
          <p>{job.summary || status || job.stage}</p>
        </div>
        <div className="job-header-actions">
          {complete ? <AppButton tone="primary" icon={Plus} onClick={onNewImport}>New import</AppButton> : null}
        </div>
      </section>
      <section className="job-progress-panel">
        <div className="job-progress-top">
          <div><strong>{job.stage}</strong><span>{job.completed} of {job.total} tracks complete</span></div>
          <strong className="progress-number">{Math.round(job.progress)}%</strong>
        </div>
        <div className="progress-track"><span style={{ width: `${Math.max(job.progress, 2)}%` }} /></div>
        <div className="job-metrics">
          <span><Clock3 size={13} />Started {timeAgo(job.startedAt)}</span>
          <span><Folder size={13} />{job.outputDir}</span>
          {job.playlistName ? <span><Disc3 size={13} />{job.playlistName}</span> : null}
          {failed ? <span className="metric-danger"><AlertCircle size={13} />{failed} failed</span> : null}
        </div>
      </section>
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

function TrackTable({ tracks, onInspect }: { tracks: JobTrack[]; onInspect: (track: JobTrack) => void }) {
  return (
    <section className="data-table-wrap">
      <div className="table-toolbar"><div><strong>Tracks</strong><span>{tracks.length} in this job</span></div></div>
      <div className="track-table" role="table">
        <div className="track-table-head" role="row">
          <span className="col-status" /><span>Track</span><span>Stage</span><span>BPM</span><span>Key</span><span>Cues</span><span />
        </div>
        {tracks.map((track, index) => (
          <button className={`track-table-row ${track.stage === "failed" ? "failed" : ""}`} role="row" key={track.id} onClick={() => onInspect(track)}>
            <span className="col-status">
              {track.stage === "ready" ? <CheckCircle2 size={16} className="success-icon" /> : track.stage === "failed" ? <AlertCircle size={16} className="danger-icon" /> : track.stage === "queued" ? <span className="queue-dot" /> : <LoaderCircle size={15} className="spin blue-icon" />}
            </span>
            <span className="track-cell"><span className="mini-art">{String(index + 1).padStart(2, "0")}</span><span><strong>{track.title}</strong><small>{track.artist}</small></span></span>
            <span className="stage-cell"><span>{track.stage === "ready" ? "Ready" : track.stage.charAt(0).toUpperCase() + track.stage.slice(1)}</span><span className="tiny-progress"><i style={{ width: `${track.progress}%` }} /></span></span>
            <span className="mono">{track.bpm ? track.bpm.toFixed(1) : "--"}</span>
            <span className="mono key-cell">{track.key || "--"}</span>
            <span>{track.cueStatus === "filled" ? <StatusBadge tone="success">Filled</StatusBadge> : track.cueStatus === "proposed" ? <StatusBadge tone="blue">Proposed</StatusBadge> : <span className="muted">Off</span>}</span>
            <span className="row-arrow">-&gt;</span>
            {track.error ? <span className="row-error-detail">{track.error}</span> : null}
          </button>
        ))}
      </div>
    </section>
  )
}

function ActivityView({ jobs, onSelectJob, onInspect, onClearCompleted }: { jobs: ActivityJob[]; onSelectJob: (job: ActivityJob) => void; onInspect: (track: JobTrack) => void; onClearCompleted: () => void }) {
  const running = jobs.filter((job) => job.state === "running" || job.state === "blocked")
  return (
    <main className="workspace standard-workspace">
      <PageIntro eyebrow="Job history" title="Activity" description="Imports keep their progress, exceptions, and Rekordbox handoff state in one place." />
      {jobs.length === 0 ? <EmptyState icon={Activity} title="No jobs yet" description="Your first SoundCloud import will appear here with track-level progress." /> : (
        <>
          {running.length ? <div className="activity-section"><div className="section-toolbar compact"><div><span className="section-kicker">Now</span><h2>In progress</h2></div></div>{running.map((job) => <JobRow key={job.id} job={job} onClick={() => onSelectJob(job)} />)}</div> : null}
          <div className="activity-section"><div className="section-toolbar compact"><div><span className="section-kicker">History</span><h2>Recent jobs</h2></div><button className="text-button" onClick={onClearCompleted}>Clear completed</button></div>
            {jobs.filter((job) => !running.includes(job)).map((job) => <JobRow key={job.id} job={job} onClick={() => { onSelectJob(job); if (job.tracks[0]) onInspect(job.tracks[0]) }} />)}
          </div>
        </>
      )}
    </main>
  )
}

function JobRow({ job, onClick }: { job: ActivityJob; onClick: () => void }) {
  const tone = job.state === "complete" ? "success" : job.state === "partial" || job.state === "blocked" ? "warning" : job.state === "failed" ? "danger" : "blue"
  return (
    <button className="job-row" onClick={onClick}>
      <span className={`job-row-icon job-${tone}`}>{job.state === "complete" ? <Check size={16} /> : job.state === "running" ? <LoaderCircle size={16} className="spin" /> : <AlertCircle size={16} />}</span>
      <span className="job-row-name"><strong>{job.title}</strong><small>{job.stage} · {job.completed}/{job.total} tracks</small></span>
      <span className="job-row-destination">{job.playlistName || job.outputDir}</span>
      <span className="job-row-progress"><i><b style={{ width: `${job.progress}%` }} /></i><small>{Math.round(job.progress)}%</small></span>
      <span className="job-row-time">{timeAgo(job.startedAt)}</span>
      <span className="row-arrow">-&gt;</span>
    </button>
  )
}

function DownloadsView({ jobs, onInspect, onAddSource }: { jobs: ActivityJob[]; onInspect: (track: JobTrack) => void; onAddSource: () => void }) {
  const tracks = jobs.flatMap((job) => job.tracks.filter((track) => track.stage === "ready"))
  const [query, setQuery] = useState("")
  const filtered = tracks.filter((track) => `${track.title} ${track.artist} ${track.key || ""}`.toLowerCase().includes(query.toLowerCase()))
  return (
    <main className="workspace standard-workspace">
      <PageIntro eyebrow="Local library" title="Downloads" description="Prepared files grouped across recent SoundCloud sources." actions={<AppButton tone="primary" icon={Plus} onClick={onAddSource}>Add source</AppButton>} />
      <div className="library-toolbar">
        <label className="search-field"><Search size={14} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search tracks, artists, keys..." /></label>
        <span className="toolbar-count">{filtered.length} tracks</span>
      </div>
      {filtered.length ? <TrackTable tracks={filtered} onInspect={onInspect} /> : <EmptyState icon={FileAudio} title="No prepared tracks" description="Completed downloads appear here after your first import." />}
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
      <PageIntro eyebrow="Collection" title="Rekordbox playlists" description="Inspect cached playlist state and run guarded, backed-up changes." actions={<AppButton tone="secondary" icon={RefreshCw} loading={loading} onClick={onReload}>Refresh</AppButton>} />
      <div className="rekordbox-healthbar">
        <div><span className="health-icon"><ShieldCheck size={17} /></span><span><strong>Safe write policy active</strong><small>Occupied hot-cue slots are never overwritten.</small></span></div>
        <StatusBadge tone="success">Guarded access</StatusBadge>
      </div>
      <div className="library-toolbar">
        <label className="search-field"><Search size={14} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search playlists..." /></label>
        <span className="toolbar-count">{visible.length} playlists</span>
      </div>
      <section className="playlist-table">
        <div className="playlist-table-head"><span>Playlist</span><span>Tracks</span><span>Access</span><span /></div>
        {visible.map((playlist) => (
          <div className="playlist-row" key={playlist.id}>
            <button className="playlist-main" onClick={() => onInspect(playlist)}><span className="playlist-icon"><ListMusic size={16} /></span><span><strong>{playlist.name}</strong><small>{playlist.path}</small></span></button>
            <span className="mono">{playlist.song_count}</span>
            <StatusBadge tone="success">Loaded</StatusBadge>
            <div className="playlist-actions"><button className="icon-button" title="Remove generated hot cues" aria-label={`Remove generated hot cues from ${playlist.name}`} onClick={() => setRemoving(playlist)}><Trash2 size={15} /></button></div>
          </div>
        ))}
      </section>
      {removing ? (
        <Modal title="Remove generated hot cues?" icon={Trash2} onClose={() => !removeBusy && setRemoving(null)}>
          <p>This removes only cues verified as generated by SoundCloud DL from tracks in <strong>{removing.name}</strong>.</p>
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
  return (
    <main className="workspace standard-workspace">
      <PageIntro eyebrow="Safety and repair" title="Rekordbox Doctor" description="Inspect every repair before it touches the library." actions={<AppButton tone="secondary" icon={RefreshCw} loading={loading} onClick={load}>Run checks</AppButton>} />
      <div className="doctor-summary">
        <div className="doctor-score"><span><ShieldCheck size={22} /></span><div><strong>{issueCount ? "Review recommended" : "Library looks healthy"}</strong><small>{loading ? "Running diagnostics..." : "Last checked just now"}</small></div></div>
        <div className="doctor-stat"><strong>{Math.max(0, checks.length - checks.filter((check) => check.tone === "warning" || check.tone === "danger").length)}</strong><span>Checks passed</span></div>
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
        <div className="modal-detail"><ShieldCheck size={15} /><span>Only verified SoundCloud DL loops are changed. A database backup is created first.</span></div>
        <div className="modal-actions"><AppButton tone="secondary" onClick={() => setRepair(null)} disabled={repairing}>Cancel</AppButton><AppButton tone="primary" icon={repair === "active" ? CircleDot : Gauge} loading={repairing} onClick={applyRepair}>Apply repair</AppButton></div>
      </Modal> : null}
    </main>
  )
}

function SettingsView({ config, onSave, theme, onTheme }: { config: AppConfig | null; onSave: (config: AppConfig) => Promise<void>; theme: Theme; onTheme: (theme: Theme) => void }) {
  const [draft, setDraft] = useState<AppConfig>(config ?? mockConfig)
  const [saving, setSaving] = useState(false)
  useEffect(() => { if (config) setDraft(config) }, [config])
  const update = <K extends keyof AppConfig>(key: K, value: AppConfig[K]) => setDraft((current) => ({ ...current, [key]: value }))
  const save = async () => {
    setSaving(true)
    try { await onSave(draft) } finally { setSaving(false) }
  }
  return (
    <main className="workspace standard-workspace settings-workspace">
      <PageIntro eyebrow="Preferences" title="Settings" description="Simple defaults here; expert concurrency remains automatic." actions={<AppButton tone="primary" icon={Check} loading={saving} onClick={save}>Save changes</AppButton>} />
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
        <div className="settings-fields"><div className="engine-info"><span className="engine-ready"><Check size={14} /></span><span><strong>SoundCloud engine ready</strong><small>320 kbps MP3 · {draft.workers} download workers · {draft.fragments} fragments</small></span><span className="mono">v0.1.0</span></div></div>
      </section>
    </main>
  )
}

function SettingsToggle({ label, detail, value, onChange }: { label: string; detail: string; value: boolean; onChange: (value: boolean) => void }) {
  return <div className="settings-inline"><span><strong>{label}</strong><small>{detail}</small></span><button className={`toggle ${value ? "on" : ""}`} role="switch" aria-checked={value} onClick={() => onChange(!value)}><span /></button></div>
}

function PageIntro({ eyebrow, title, description, actions }: { eyebrow: string; title: string; description: string; actions?: ReactNode }) {
  return <section className="page-intro"><div><span className="section-kicker">{eyebrow}</span><h1>{title}</h1><p>{description}</p></div>{actions ? <div className="page-actions">{actions}</div> : null}</section>
}

function EmptyState({ icon: Icon, title, description }: { icon: LucideIcon; title: string; description: string }) {
  return <div className="empty-state"><span><Icon size={22} /></span><strong>{title}</strong><p>{description}</p></div>
}

function Modal({ title, icon: Icon, children, onClose }: { title: string; icon: LucideIcon; children: ReactNode; onClose: () => void }) {
  useEffect(() => {
    const listener = (event: KeyboardEvent) => { if (event.key === "Escape") onClose() }
    window.addEventListener("keydown", listener)
    return () => window.removeEventListener("keydown", listener)
  }, [onClose])
  return <div className="modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose() }}><div className="modal" role="dialog" aria-modal="true" aria-labelledby="modal-title"><div className="modal-head"><span><Icon size={17} /></span><h2 id="modal-title">{title}</h2><button className="icon-button" onClick={onClose} aria-label="Close"><X size={16} /></button></div><div className="modal-body">{children}</div></div></div>
}

function LocalWaveform({ seed = "track", compact = false }: { seed?: string; compact?: boolean }) {
  const bars = useMemo(() => Array.from({ length: compact ? 46 : 94 }, (_, index) => {
    let value = 0
    for (let char = 0; char < seed.length; char += 1) value += seed.charCodeAt(char) * (index + char + 3)
    return 18 + ((value * 17 + index * 31) % 72)
  }), [seed, compact])
  return <div className={`waveform ${compact ? "compact" : ""}`}>{bars.map((height, index) => <i key={index} style={{ height: `${height}%` }} className={index < bars.length * 0.36 ? "played" : ""} />)}{!compact ? <><span className="wave-playhead" /><span className="cue-marker cue-a" style={{ left: "8%" }}>A</span><span className="cue-marker cue-b" style={{ left: "27%" }}>B</span><span className="cue-marker cue-c" style={{ left: "48%" }}>C</span><span className="cue-loop" style={{ left: "72%", width: "9%" }}><b>E</b></span></> : null}</div>
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

      const center = rect.height / 2
      const step = rect.width / Math.max(lane.heights.length, 1)
      lane.heights.forEach((rawHeight, index) => {
        const height = Math.max(1, Math.min(1, rawHeight) * (rect.height - 18))
        const color = lane.colors[index] ?? [56, 168, 232]
        context.fillStyle = `rgb(${color[0] ?? 56}, ${color[1] ?? 168}, ${color[2] ?? 232})`
        context.globalAlpha = 0.86
        context.fillRect(index * step, center - height / 2, Math.max(1, step + 0.25), height)
      })
      context.globalAlpha = 1

      for (const beat of waveform.beat_grid) {
        if (beat.beat_number !== 1) continue
        const x = beat.time_sec / waveform.duration_sec * rect.width
        context.fillStyle = "rgba(255, 255, 255, 0.24)"
        context.fillRect(Math.round(x), 2, 1, rect.height - 4)
      }

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
        <div className="playlist-inspector-art"><ListMusic size={28} /><LocalWaveform seed={playlist.name} compact /></div>
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

function LocalTrackInspector({ track, onClose }: { track: JobTrack; onClose: () => void }) {
  return (
    <aside className="inspector">
      <div className="inspector-head"><strong>Track inspector</strong><button className="icon-button" onClick={onClose} aria-label="Close inspector"><X size={15} /></button></div>
      <div className="inspector-scroll">
        <div className="track-inspector-top"><div className="large-art"><Music2 size={27} /><span>{track.key || "--"}</span></div><div><h2>{track.title}</h2><p>{track.artist}</p><span className="source-link">SoundCloud <ExternalLink size={10} /></span></div></div>
        <div className="wave-source-row"><StatusBadge tone="neutral">Local preview</StatusBadge><span>Available before Rekordbox analysis</span></div>
        <LocalWaveform seed={track.title} />
        <div className="wave-legend"><span><i className="legend-downbeat" />Downbeat</span><span><i className="legend-cue" />Hot cue</span><span><i className="legend-loop" />Loop</span></div>
        <div className="inspector-metrics"><div><strong className="mono">{track.bpm?.toFixed(1) || "--"}</strong><span>BPM</span></div><div><strong className="mono">{track.key || "--"}</strong><span>Key</span></div><div><strong>7</strong><span>Energy</span></div></div>
        <div className="inspector-section cue-preview"><div className="inspector-section-head"><h3>Hot cues</h3><StatusBadge tone={track.cueStatus === "filled" ? "success" : "neutral"}>{track.cueStatus}</StatusBadge></div>
          {[{ pad: "A", name: "Intro", color: "pink" }, { pad: "B", name: "Phrase 16", color: "blue" }, { pad: "C", name: "Phrase 32", color: "green" }, { pad: "D", name: "Intro Loop", color: "orange" }, { pad: "E", name: "Exit Loop", color: "amber" }].map((cue) => <div className="cue-row" key={cue.pad}><span className={`cue-pad cue-${cue.color}`}>{cue.pad}</span><strong>{cue.name}</strong><span>{track.cueStatus === "filled" ? "Written" : "Suggested"}</span></div>)}
        </div>
        <div className="cue-safety-note"><ShieldCheck size={15} /><span>Existing occupied slots are always preserved.</span></div>
      </div>
    </aside>
  )
}

function RekordboxTrackInspector({ track, playlist, onClose }: { track: PlaylistTrack; playlist: Playlist; onClose: () => void }) {
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
      <div className="inspector-head"><strong>Track inspector</strong><button className="icon-button" onClick={onClose} aria-label="Close inspector"><X size={15} /></button></div>
      <div className="inspector-scroll">
        <div className="track-inspector-top"><div className="large-art"><Disc3 size={27} /><span>RB</span></div><div><h2>{track.title}</h2><p>{track.artist || "Unknown artist"}</p><span className="source-link">{playlist.name}</span></div></div>
        <div className="wave-source-row">{waveform ? <><StatusBadge tone="success">Rekordbox analysis</StatusBadge><span>{waveform.preview?.tag || "No preview"} overview · {waveform.detail?.tag || "No detail"} detail</span></> : <><StatusBadge tone="neutral">Awaiting analysis</StatusBadge><span>No authoritative ANLZ waveform yet</span></>}</div>
        {loading ? <div className="waveform-loading"><LoaderCircle className="spin" size={16} /><span>Reading Rekordbox ANLZ data...</span></div> : null}
        {error ? <InlineError message={error} /> : null}
        {waveform?.preview ? <div className="authoritative-waveform"><span>Full track</span><RekordboxWaveformCanvas waveform={waveform} lane={waveform.preview} label={`${track.title} Rekordbox color overview waveform`} /></div> : null}
        {waveform?.detail ? <div className="authoritative-waveform detail-waveform"><span>Detail</span><RekordboxWaveformCanvas waveform={waveform} lane={waveform.detail} label={`${track.title} Rekordbox color detail waveform`} /></div> : null}
        {waveform ? <div className="wave-legend"><span><i className="legend-downbeat" />PQTZ downbeat</span><span><i className="legend-cue" />Database cue</span><span><i className="legend-loop" />Loop range</span></div> : null}
        <div className="inspector-metrics"><div><strong className="mono">{bpm?.toFixed(1) || "--"}</strong><span>BPM</span></div><div><strong>{waveform?.beat_grid.length ?? "--"}</strong><span>Beats</span></div><div><strong className="mono">{formatDuration(waveform?.duration_sec ?? 0)}</strong><span>Length</span></div></div>
        {waveform ? <div className="inspector-section cue-preview"><div className="inspector-section-head"><h3>Database cues</h3><StatusBadge tone="success">Exact timing</StatusBadge></div>
          {waveform.cues.map((cue) => { const pad = cuePad(cue); return <div className="cue-row" key={cue.id}><span className={`cue-pad cue-${cueTone(pad)}`}>{pad}</span><strong>{cue.name || "Unnamed cue"}</strong><span className="mono">{formatTimestamp(cue.in_sec)}{cue.out_sec !== null ? ` - ${formatTimestamp(cue.out_sec)}` : ""}</span></div> })}
          {!waveform.cues.length ? <p className="inspector-helper">No database cues are set.</p> : null}
        </div> : null}
        {waveform ? <div className="anlz-proof"><ShieldCheck size={14} /><span><strong>Authoritative Rekordbox data</strong><small>{waveform.fingerprint.slice(0, 12)} · {waveform.files.map((file) => file.kind).join(" + ")}</small></span></div> : null}
        {!track.file_exists ? <div className="cue-safety-note missing-file-note"><AlertCircle size={15} /><span>The audio file is missing, but Rekordbox's cached waveform remains inspectable.</span></div> : null}
      </div>
    </aside>
  )
}

function Inspector({ item, onClose, onInspect, onOpenRekordbox }: { item: InspectorItem; onClose: () => void; onInspect: (item: InspectorItem) => void; onOpenRekordbox: () => void }) {
  if (!item) return (
    <aside className="inspector empty-inspector">
      <div className="inspector-head"><strong>Inspector</strong><button className="icon-button" onClick={onClose} aria-label="Close inspector"><X size={15} /></button></div>
      <div className="inspector-empty"><AudioLines size={24} /><strong>Nothing selected</strong><span>Select a track or playlist to inspect it without leaving your place.</span></div>
    </aside>
  )
  if (item.kind === "playlist") return <PlaylistInspector playlist={item.playlist} onClose={onClose} onInspect={onInspect} onOpenRekordbox={onOpenRekordbox} />
  if (item.kind === "rekordbox-track") return <RekordboxTrackInspector track={item.track} playlist={item.playlist} onClose={onClose} />
  return <LocalTrackInspector track={item.track} onClose={onClose} />
}

type ToastTone = "success" | "danger" | "neutral"
interface ToastItem { id: string; message: string; tone: ToastTone }

function Toasts({ items, onDismiss }: { items: ToastItem[]; onDismiss: (id: string) => void }) {
  return <div className="toasts" aria-live="polite">{items.map((item) => <div className={`toast toast-${item.tone}`} key={item.id}>{item.tone === "success" ? <CheckCircle2 size={16} /> : item.tone === "danger" ? <AlertCircle size={16} /> : <Info size={16} />}<span>{item.message}</span><button onClick={() => onDismiss(item.id)} aria-label="Dismiss"><X size={14} /></button></div>)}</div>
}

function CommandPalette({ onClose, onNavigate }: { onClose: () => void; onNavigate: (view: ViewId) => void }) {
  const [query, setQuery] = useState("")
  const actions: { label: string; detail: string; icon: LucideIcon; view: ViewId }[] = [
    { label: "Start a new import", detail: "Paste a SoundCloud URL", icon: Plus, view: "import" },
    { label: "Open Rekordbox playlists", detail: "Inspect and sync playlists", icon: Disc3, view: "rekordbox" },
    { label: "Run Rekordbox Doctor", detail: "Diagnostics and repairs", icon: ShieldCheck, view: "doctor" },
    { label: "Open settings", detail: "Storage and defaults", icon: Settings, view: "settings" },
  ]
  const visible = actions.filter((action) => `${action.label} ${action.detail}`.toLowerCase().includes(query.toLowerCase()))
  return <div className="palette-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose() }}><div className="palette"><label><Search size={17} /><input autoFocus value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search commands..." /><kbd>ESC</kbd></label><div className="palette-section"><span>Actions</span>{visible.map((action) => { const Icon = action.icon; return <button key={action.label} onClick={() => { onNavigate(action.view); onClose() }}><span><Icon size={16} /></span><span><strong>{action.label}</strong><small>{action.detail}</small></span><span className="row-arrow">-&gt;</span></button> })}{!visible.length ? <div className="palette-empty">No matching commands</div> : null}</div></div></div>
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
  const [inspector, setInspector] = useState<InspectorItem>(null)
  const [inspectorOpen, setInspectorOpen] = useState(true)
  const [paletteOpen, setPaletteOpen] = useState(false)
  const [rekordboxRunning, setRekordboxRunning] = useState<boolean | null>(null)
  const [engineReady, setEngineReady] = useState(false)
  const [usbDevices, setUsbDevices] = useState<UsbDevice[]>([])
  const [toasts, setToasts] = useState<ToastItem[]>([])
  const [theme, setTheme] = useState<Theme>(() => (localStorage.getItem(THEME_KEY) as Theme) || "dark")
  const [initialUrl, setInitialUrl] = useState("")
  const [resumeJob, setResumeJob] = useState<ActivityJob | null>(null)
  const toastTimers = useRef<Map<string, number>>(new Map())

  const showToast = useCallback((message: string, tone: ToastTone = "neutral") => {
    const id = createId("toast")
    setToasts((current) => [...current, { id, message, tone }])
    const timer = window.setTimeout(() => setToasts((current) => current.filter((item) => item.id !== id)), 5000)
    toastTimers.current.set(id, timer)
  }, [])

  const dismissToast = (id: string) => {
    const timer = toastTimers.current.get(id)
    if (timer) window.clearTimeout(timer)
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
          setConfig(mockConfig)
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
    setView(next)
    setResumeJob(null)
    if (next === "import") setInitialUrl("")
  }

  const openLikes = () => {
    setResumeJob(null)
    setInitialUrl(config?.likes_url || "")
    setView("import")
  }

  const consumeResumeJob = useCallback(() => setResumeJob(null), [])

  const openJob = (job: ActivityJob) => {
    if (job.state === "running" || job.state === "blocked") {
      setInitialUrl("")
      setResumeJob(job)
      setView("import")
    } else if (job.tracks[0]) {
      inspect({ kind: "track", track: job.tracks[0] })
    }
  }

  const content = (() => {
    if (view === "import") return <ImportWorkspace config={config} playlists={playlists} jobs={jobs} onCreateJob={createJob} onUpdateJob={updateJob} onInspect={inspect} initialUrl={initialUrl} resumeJob={resumeJob} onResumeJobConsumed={consumeResumeJob} onShowActivity={() => setView("activity")} rekordboxRunning={rekordboxRunning} onRekordboxStatus={setRekordboxRunning} />
    if (view === "activity") return <ActivityView jobs={jobs} onSelectJob={openJob} onInspect={(track) => inspect({ kind: "track", track })} onClearCompleted={() => setJobs((current) => current.filter((job) => job.state === "running" || job.state === "blocked"))} />
    if (view === "downloads") return <DownloadsView jobs={jobs} onInspect={(track) => inspect({ kind: "track", track })} onAddSource={() => navigate("import")} />
    if (view === "rekordbox") return <RekordboxView playlists={playlists} onReload={loadPlaylists} loading={playlistsLoading} onInspect={(playlist) => inspect({ kind: "playlist", playlist })} onToast={showToast} />
    if (view === "doctor") return <DoctorView onToast={showToast} usbDevices={usbDevices} onRekordboxStatus={setRekordboxRunning} />
    return <SettingsView config={config} onSave={saveConfig} theme={theme} onTheme={setTheme} />
  })()

  return (
    <div className={`app-shell ${inspectorOpen ? "with-inspector" : ""}`}>
      <Sidebar active={view} onChange={navigate} onLikes={openLikes} jobs={jobs} config={config} />
      <div className="main-column">
        <Topbar view={view} inspectorOpen={inspectorOpen} onToggleInspector={() => setInspectorOpen((value) => !value)} onOpenPalette={() => setPaletteOpen(true)} rekordboxRunning={rekordboxRunning} />
        {content}
      </div>
      {inspectorOpen ? <Inspector item={inspector} onClose={() => setInspectorOpen(false)} onInspect={inspect} onOpenRekordbox={() => void openRekordboxNow()} /> : null}
      <Statusbar jobs={jobs} engineReady={engineReady} rekordboxRunning={rekordboxRunning} usbDevices={usbDevices} />
      {paletteOpen ? <CommandPalette onClose={() => setPaletteOpen(false)} onNavigate={navigate} /> : null}
      <Toasts items={toasts} onDismiss={dismissToast} />
    </div>
  )
}
