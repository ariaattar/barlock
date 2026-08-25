export type ViewId = "import" | "activity" | "downloads" | "rekordbox" | "doctor" | "settings"

export type CueMode = "off" | "fill"

export interface AppConfig {
  soundcloud_username: string
  output_dir: string
  workers: number
  fragments: number
  quality: string
  rekordbox_playlist: string
  analyze_after_download: boolean
  write_tags: boolean
  direct_rekordbox_push: boolean
  likes_url: string
  config_path: string
}

export interface SourceEntry {
  id: string
  url: string
  title: string
  label: string
}

export interface SyncPlan {
  ok: boolean
  url: string
  title: string
  target_dir: string
  suggested_playlist: string
  rekordbox_playlist_id: string
  total_count: number
  added: SourceEntry[]
  removed_ids: string[]
  unchanged_count: number
  is_first_sync: boolean
  entries: SourceEntry[]
}

export interface Playlist {
  id: string
  name: string
  path: string
  is_folder: boolean
  song_count: number
}

export interface PlaylistTrack {
  content_id: string
  title: string
  artist: string
  folder_path: string
  file_exists: boolean
  soundcloud_dl_managed: boolean
}

export interface RekordboxWaveformLane {
  tag: "PWAV" | "PWV3" | "PWV4" | "PWV5" | string
  heights: number[]
  colors: number[][]
}

export interface RekordboxBeat {
  beat_number: number
  bpm: number
  time_sec: number
}

export interface RekordboxCuePoint {
  id: string
  kind: number
  name: string
  in_sec: number
  out_sec: number | null
  color: number
  color_table_index: number
  active_loop: boolean
  loop_beats: number
}

export interface RekordboxWaveform {
  source: "rekordbox_anlz"
  content_id: string
  title: string
  duration_sec: number
  fingerprint: string
  files: Array<{ kind: string; path: string; mtime_ns: number; size: number }>
  beat_grid: RekordboxBeat[]
  preview: RekordboxWaveformLane | null
  detail: RekordboxWaveformLane | null
  cues: RekordboxCuePoint[]
}

export interface TrackFeature {
  path: string
  title: string
  artist: string
  duration_sec: number
  bpm: number
  musical_key: string
  camelot_key: string
  key_confidence: number
  energy: number
  energy_curve?: number[]
  segmentation_mode?: string
}

export interface BridgeMessage {
  event?: "status" | "plan" | "done" | "error"
  message?: string
  ok?: boolean
  [key: string]: unknown
}

export interface BridgeResult {
  code: number
  messages: BridgeMessage[]
  stderr: string
}

export interface JobTrack {
  id: string
  title: string
  artist: string
  stage: "queued" | "downloading" | "converting" | "analyzing" | "syncing" | "ready" | "failed" | "skipped"
  progress: number
  bpm?: number
  key?: string
  cueStatus: "off" | "proposed" | "filled" | "skipped"
  error?: string
}

export interface ActivityJob {
  id: string
  title: string
  sourceUrl: string
  state: "running" | "blocked" | "complete" | "partial" | "failed" | "canceled"
  stage: string
  progress: number
  completed: number
  total: number
  startedAt: string
  tracks: JobTrack[]
  destination?: "rekordbox" | "download"
  analyze?: boolean
  cueMode?: CueMode
  playlistName?: string
  outputDir: string
  summary?: string
}

export interface DoctorResult {
  rekordbox_running?: boolean
  rekordbox_database?: string
  backup_dir?: string
  ffmpeg?: string
  output_dir?: string
  [key: string]: unknown
}

export interface UsbDevice {
  name: string
  mount_path: string
  filesystem: string
  total_bytes: number
  free_bytes: number
  writable: boolean
  rekordbox_export: boolean
}
