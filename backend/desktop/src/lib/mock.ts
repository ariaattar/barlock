import type { AppConfig, BridgeMessage, BridgeResult, DoctorResult, Playlist, PlaylistTrack, RekordboxWaveform, SourceEntry, SyncPlan } from "../types"

const entries: SourceEntry[] = [
  { id: "2336664791", title: "Dirty Dancin'", label: "Dirty Dancin'", url: "https://soundcloud.com/catchandreleaserecords/tony-dark-eyes-dirty-dancin" },
  { id: "2307368309", title: "All The Time (VIZON Remix)", label: "All The Time (VIZON Remix)", url: "https://soundcloud.com/vizon/all-the-time" },
  { id: "2265774521", title: "Girls (Missed Call Edit)", label: "Girls (Missed Call Edit)", url: "https://soundcloud.com/missedcall/girls" },
  { id: "2210568296", title: "On Off (Rework)", label: "On Off (Rework)", url: "https://soundcloud.com/dansyn/on-off" },
  { id: "2276064686", title: "Boogie Wonderland", label: "Boogie Wonderland", url: "https://soundcloud.com/tribe_musicc/boogiewonderland" },
  { id: "2115152889", title: "No Broke Boys (DANSYN Remix)", label: "No Broke Boys (DANSYN Remix)", url: "https://soundcloud.com/dansyn/no-broke-boys" },
]

export const mockConfig: AppConfig = {
  soundcloud_username: "ariaattar",
  output_dir: "/Users/ariaattar/Downloads/SoundCloud",
  workers: 8,
  fragments: 8,
  quality: "320",
  rekordbox_playlist: "SoundCloud Likes",
  analyze_after_download: true,
  write_tags: true,
  direct_rekordbox_push: true,
  likes_url: "https://soundcloud.com/ariaattar/likes",
  config_path: "~/.config/soundcloud-dl/config.json",
}

export const mockPlaylists: Playlist[] = [
  { id: "set-20", name: "Set 20", path: "Playlists / Set 20", is_folder: false, song_count: 18 },
  { id: "set-7", name: "set7", path: "Playlists / set7", is_folder: false, song_count: 15 },
  { id: "likes", name: "SoundCloud Likes", path: "Playlists / SoundCloud Likes", is_folder: false, song_count: 124 },
  { id: "prep", name: "Prep", path: "Playlists / Prep", is_folder: true, song_count: 0 },
]

export const mockDoctor: DoctorResult = {
  rekordbox_running: false,
  rekordbox_database: "~/Library/Pioneer/rekordbox/master.db",
  backup_dir: "~/Library/Pioneer/rekordbox/backups/2026-08-25-074102",
  ffmpeg: "Bundled 7.1",
  output_dir: "/Users/ariaattar/Downloads/SoundCloud",
  active_generated_loops: 0,
  off_grid_generated_loops: 2,
}

const mockPlaylistTracks: PlaylistTrack[] = entries.map((entry, index) => ({
  content_id: entry.id,
  title: entry.title,
  artist: ["Tony Dark Eyes", "VIZON", "Missed Call", "DANSYN", "TRIBE", "DANSYN"][index] ?? "SoundCloud",
  folder_path: `/Users/ariaattar/Downloads/Late Summer Set/${entry.title} [${entry.id}].mp3`,
  file_exists: index !== 4,
  soundcloud_dl_managed: true,
}))

function mockWaveform(contentId: string): RekordboxWaveform {
  const duration = 218
  const pointCount = 720
  const heights = Array.from({ length: pointCount }, (_, index) => {
    const phrase = 0.42 + Math.sin(index / 29) * 0.2
    const transient = Math.abs(Math.sin(index / 4.7)) * 0.42
    const envelope = Math.min(1, index / 55, (pointCount - index) / 42)
    return Math.max(0.03, Math.min(1, (phrase + transient) * envelope))
  })
  const colors = heights.map((height, index) => [
    Math.round(26 + height * 40),
    Math.round(102 + height * 112),
    Math.round(178 + Math.abs(Math.sin(index / 18)) * 72),
  ])
  const beats = Array.from({ length: 472 }, (_, index) => ({
    beat_number: index % 4 + 1,
    bpm: 130,
    time_sec: Number((0.073 + index * (60 / 130)).toFixed(3)),
  }))
  return {
    source: "rekordbox_anlz",
    content_id: contentId,
    title: mockPlaylistTracks.find((track) => track.content_id === contentId)?.title ?? "Dirty Dancin'",
    duration_sec: duration,
    fingerprint: "697a8ec187cad940d9cbe62ac733b28ab1cad3b4cbb9e6e0a92d6f35cd27f02d",
    files: [
      { kind: "DAT", path: "ANLZ0000.DAT", mtime_ns: 0, size: 6160 },
      { kind: "EXT", path: "ANLZ0000.EXT", mtime_ns: 0, size: 106875 },
    ],
    beat_grid: beats,
    preview: { tag: "PWV4", heights: heights.filter((_, index) => index % 2 === 0), colors: colors.filter((_, index) => index % 2 === 0) },
    detail: { tag: "PWV5", heights, colors },
    cues: [
      { id: "cue-a", kind: 1, name: "Intro", in_sec: 0.073, out_sec: null, color: 0, color_table_index: 0, active_loop: false, loop_beats: 0 },
      { id: "cue-b", kind: 2, name: "Phrase 16", in_sec: 29.612, out_sec: null, color: 0, color_table_index: 0, active_loop: false, loop_beats: 0 },
      { id: "cue-c", kind: 3, name: "Phrase 32", in_sec: 59.15, out_sec: null, color: 0, color_table_index: 0, active_loop: false, loop_beats: 0 },
      { id: "cue-e", kind: 6, name: "Exit Loop", in_sec: 191.15, out_sec: 194.842, color: 0, color_table_index: 0, active_loop: false, loop_beats: 8 },
    ],
  }
}

export function mockPlan(url: string): SyncPlan {
  const likes = url.includes("/likes")
  return {
    ok: true,
    url,
    title: likes ? "ariaattar Likes" : "Late Summer Set",
    target_dir: likes ? "/Users/ariaattar/Downloads/ariaattar Likes" : "/Users/ariaattar/Downloads/Late Summer Set",
    suggested_playlist: likes ? "SoundCloud Likes" : "Late Summer Set",
    rekordbox_playlist_id: likes ? "likes" : "",
    total_count: likes ? 284 : 42,
    added: entries,
    removed_ids: ["old-1"],
    unchanged_count: likes ? 278 : 36,
    is_first_sync: false,
    entries: [...entries, ...Array.from({ length: 12 }, (_, index) => ({
      id: `existing-${index}`,
      title: `Existing track ${index + 1}`,
      label: `Existing track ${index + 1}`,
      url: `https://soundcloud.com/demo/existing-${index + 1}`,
    }))],
  }
}

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms))
let mockRekordboxRunning = false

export async function runMockBridge(
  command: string,
  args: string[],
  payload: Record<string, unknown> | undefined,
  onMessage?: (message: BridgeMessage) => void,
): Promise<BridgeResult> {
  const messages: BridgeMessage[] = []
  const emit = async (message: BridgeMessage, delay = 80) => {
    await sleep(delay)
    messages.push(message)
    onMessage?.(message)
  }

  if (command === "config") {
    await emit({ ok: true, config: mockConfig })
  } else if (command === "save-config") {
    await emit({ ok: true, config: { ...mockConfig, ...payload } })
  } else if (command === "sync-plan") {
    await emit({ event: "status", message: "Expanding SoundCloud playlist..." })
    await emit({ ...mockPlan(args[0] ?? "https://soundcloud.com/ariaattar/sets/set") })
  } else if (command === "list-playlists") {
    await emit({ ok: true, playlists: mockPlaylists })
  } else if (command === "list-playlist-tracks") {
    await emit({ ok: true, tracks: mockPlaylistTracks })
  } else if (command === "rekordbox-waveform") {
    const contentId = args[args.indexOf("--content-id") + 1] ?? mockPlaylistTracks[0].content_id
    await emit({ ok: true, waveform: mockWaveform(contentId) }, 180)
  } else if (command === "rekordbox-running") {
    await emit({ ok: true, running: mockRekordboxRunning })
  } else if (command === "close-rekordbox") {
    mockRekordboxRunning = false
    await emit({ ok: true, closed: true }, 240)
  } else if (command === "open-rekordbox") {
    mockRekordboxRunning = true
    await emit({ ok: true, opened: true }, 240)
  } else if (command === "usb-status") {
    await emit({ ok: true, devices: [] })
  } else if (command === "doctor") {
    await emit({ ok: true, doctor: mockDoctor }, 180)
  } else if (command === "remove-generated-cues") {
    await emit({ event: "done", ok: true, playlist_name: "Set 20", removed_cues: 7, affected_tracks: 3, backup_dir: mockDoctor.backup_dir }, 400)
  } else if (command === "sync" || command === "download") {
    const plan = mockPlan(String(payload?.url ?? args[0] ?? ""))
    await emit({ event: "plan", title: plan.title, total: plan.total_count, added: plan.added.length, unchanged: plan.unchanged_count })
    for (let index = 0; index < plan.added.length; index += 1) {
      const entry = plan.added[index]
      await emit({ event: "status", message: `[${index + 1}/${plan.added.length}] downloading ${entry.url}` }, 150)
      await emit({ event: "status", message: `[${index + 1}/${plan.added.length}] finished ${entry.url}` }, 100)
    }
    await emit({ event: "status", message: `Analyzing ${plan.added.length} track(s)...` }, 180)
    for (let index = 0; index < plan.added.length; index += 1) {
      await emit({ event: "status", message: `[${index + 1}/${plan.added.length}] analyzing ${plan.added[index].title}` }, 130)
    }
    await emit({
      event: "done",
      ok: true,
      title: plan.title,
      target_dir: plan.target_dir,
      added: plan.added.length,
      removed: plan.removed_ids.length,
      unchanged: plan.unchanged_count,
      analyzed: plan.added.length,
      failed_downloads: [],
      push: command === "sync" ? {
        playlist_name: String(payload?.playlist_name ?? plan.suggested_playlist),
        added_to_playlist: plan.added.length,
        already_in_playlist: plan.unchanged_count,
        added_cues: payload?.cue_mode === "fill" ? 9 : 0,
        backup_dir: mockDoctor.backup_dir,
      } : {},
    }, 220)
  } else {
    await emit({ event: "done", ok: true })
  }

  return { code: 0, messages, stderr: "" }
}
