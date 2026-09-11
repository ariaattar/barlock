import type { AppConfig, BridgeMessage, BridgeResult, DoctorResult, EditorCorrections, EditorState, Playlist, PlaylistTrack, RekordboxWaveform, SourceEntry, SyncPlan } from "../types"

function mockAuditionWav(): string {
  const sampleRate = 8_000
  const frameCount = Math.round(sampleRate * 0.3)
  const bytes = new Uint8Array(44 + frameCount * 2)
  const view = new DataView(bytes.buffer)
  const writeText = (offset: number, value: string) => {
    for (let index = 0; index < value.length; index += 1) view.setUint8(offset + index, value.charCodeAt(index))
  }
  writeText(0, "RIFF")
  view.setUint32(4, 36 + frameCount * 2, true)
  writeText(8, "WAVE")
  writeText(12, "fmt ")
  view.setUint32(16, 16, true)
  view.setUint16(20, 1, true)
  view.setUint16(22, 1, true)
  view.setUint32(24, sampleRate, true)
  view.setUint32(28, sampleRate * 2, true)
  view.setUint16(32, 2, true)
  view.setUint16(34, 16, true)
  writeText(36, "data")
  view.setUint32(40, frameCount * 2, true)
  for (let frame = 0; frame < frameCount; frame += 1) {
    view.setInt16(44 + frame * 2, Math.round(Math.sin(frame / sampleRate * Math.PI * 440) * 8_000), true)
  }
  let binary = ""
  for (let offset = 0; offset < bytes.length; offset += 4_096) {
    binary += String.fromCharCode(...bytes.subarray(offset, offset + 4_096))
  }
  return `data:audio/wav;base64,${btoa(binary)}`
}

const MOCK_AUDITION_WAV = mockAuditionWav()

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
  const low = heights.map((height, index) => Math.round(18 + height * 102 + Math.abs(Math.sin(index / 13)) * 8))
  const mid = heights.map((height, index) => Math.round(12 + height * 92 + Math.abs(Math.sin(index / 7)) * 18))
  const high = heights.map((height, index) => Math.round(8 + height * 72 + Math.abs(Math.sin(index / 3.5)) * 28))
  const previewIndexes = heights.map((_, index) => index).filter((index) => index % 2 === 0)
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
      { kind: "2EX", path: "ANLZ0000.2EX", mtime_ns: 0, size: 108220 },
    ],
    beat_grid: beats,
    preview: {
      tag: "PWV6",
      style: "three_band",
      sample_rate_hz: null,
      source_points: 1200,
      heights: previewIndexes.map((index) => heights[index]),
      colors: [],
      bands: {
        low: previewIndexes.map((index) => low[index]),
        mid: previewIndexes.map((index) => mid[index]),
        high: previewIndexes.map((index) => high[index]),
      },
    },
    detail: {
      tag: "PWV7",
      style: "three_band",
      sample_rate_hz: 150,
      source_points: Math.round(duration * 150),
      heights,
      colors,
      bands: { low, mid, high },
    },
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
let mockDraftRevision = 1
let mockCorrections: EditorCorrections | null = null

function mockEditor(contentId: string): EditorState {
  const waveform = mockWaveform(contentId)
  const bpm = 130
  const grid = waveform.beat_grid
  const markers: EditorCorrections["markers"] = [
    { role: "intro", pad: "A", name: "Intro", kind: "hot", hotcue_slot: 0, seconds: 0.073, end_seconds: null, loop_beats: null, snap_mode: "downbeat", ownership: "suggested", source_cue_id: "", conflict: true },
    { role: "phrase16", pad: "B", name: "Phrase 16", kind: "hot", hotcue_slot: 1, seconds: 29.612, end_seconds: null, loop_beats: null, snap_mode: "downbeat", ownership: "suggested", source_cue_id: "", conflict: true },
    { role: "phrase32", pad: "C", name: "Phrase 32", kind: "hot", hotcue_slot: 2, seconds: 59.15, end_seconds: null, loop_beats: null, snap_mode: "downbeat", ownership: "suggested", source_cue_id: "", conflict: true },
    { role: "intro_loop", pad: "D", name: "Intro Loop", kind: "loop", hotcue_slot: 3, seconds: 14.842, end_seconds: 18.535, loop_beats: 8, snap_mode: "downbeat", ownership: "suggested", source_cue_id: "", conflict: false },
    { role: "exit_loop", pad: "E", name: "Exit Loop", kind: "loop", hotcue_slot: 4, seconds: 191.15, end_seconds: 194.842, loop_beats: 8, snap_mode: "downbeat", ownership: "suggested", source_cue_id: "", conflict: true },
  ]
  mockCorrections ??= { bpm, first_downbeat_sec: .073, camelot_key: "8A", musical_key: "Am", write_grid: false, markers, grid }
  return {
    schema_version: 1,
    track: { content_id: contentId, path: mockPlaylistTracks[0].folder_path, title: mockPlaylistTracks[0].title, artist: mockPlaylistTracks[0].artist, duration_sec: waveform.duration_sec, sample_rate: 44100, managed: true, audio_fingerprint: "mock-audio" },
    waveform,
    existing_cues: waveform.cues.map((cue) => ({ ...cue, role: cue.name === "Intro" ? "intro" : cue.name === "Phrase 16" ? "phrase16" : cue.name === "Phrase 32" ? "phrase32" : cue.name === "Exit Loop" ? "exit_loop" : "", ownership: "legacy_candidate", locked: true })),
    draft: { id: "mock-draft", revision: mockDraftRevision, status: "draft", updated_at: new Date().toISOString(), corrections: mockCorrections },
    grid_editable: true,
    can_apply_rekordbox: true,
    warnings: [],
  }
}

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
  } else if (command === "editor-load") {
    await emit({ ok: true, editor: mockEditor(String(payload?.content_id ?? mockPlaylistTracks[0].content_id)) }, 180)
  } else if (command === "editor-save-draft") {
    mockDraftRevision += 1
    mockCorrections = payload?.corrections as unknown as EditorCorrections
    await emit({ ok: true, draft: { id: "mock-draft", revision: mockDraftRevision, status: "draft", updated_at: new Date().toISOString(), corrections: mockCorrections } }, 60)
  } else if (command === "editor-prepare-audition") {
    await emit({ ok: true, audition: { path: MOCK_AUDITION_WAV, context_start_sec: 189.304, loop_start_sec: 0.05, loop_end_sec: 0.15, duration_sec: 0.3 } }, 100)
  } else if (command === "editor-preview-apply") {
    await emit({ ok: true, preview: { draft_id: "mock-draft", revision: mockDraftRevision, rekordbox: true, rekordbox_running: mockRekordboxRunning, conflicts: [], changes: ["BPM 130.00 and grid offset 0.073s", "Key 8A (Am)", "6 eligible cue and loop markers", "Rekordbox PQTZ beat grid"] } }, 80)
  } else if (command === "editor-apply") {
    await emit({ event: "done", ok: true, transaction_id: "mock-transaction", state: "verified", backup_dir: mockDoctor.backup_dir, written_cues: 6 }, 220)
  } else if (command === "editor-adopt-cues") {
    await emit({ ok: true, adopted: Array.isArray(payload?.cue_ids) ? payload.cue_ids.length : 0 }, 80)
  } else if (command === "editor-reset-auto") {
    mockDraftRevision += 1
    mockCorrections = null
    const next = mockEditor(String(payload?.content_id ?? mockPlaylistTracks[0].content_id)).draft.corrections
    mockCorrections = next
    await emit({ ok: true, draft: { id: "mock-draft", revision: mockDraftRevision, status: "draft", updated_at: new Date().toISOString(), corrections: next } }, 80)
  } else if (command === "editor-rebase") {
    mockDraftRevision += 1
    await emit({ ok: true, editor: mockEditor(String(payload?.content_id ?? mockPlaylistTracks[0].content_id)) }, 100)
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
  } else if (command === "import-replacement") {
    const trackId = String(payload?.track_id ?? "replacement")
    const title = String(payload?.title ?? "Replacement")
    await emit({ event: "status", message: `Analyzing replacement ${title} [${trackId}].mp3` }, 120)
    await emit({ event: "done", ok: true, path: `/Users/ariaattar/Music/SoundCloud/${title} [${trackId}].mp3`, features: { path: `/Users/ariaattar/Music/SoundCloud/${title} [${trackId}].mp3`, source_id: trackId, title, artist: "", duration_sec: 240, bpm: 128, musical_key: "Am", camelot_key: "8A", key_confidence: 0.8, energy: 7 } }, 180)
  } else if (command === "sync" || command === "download") {
    const plan = mockPlan(String(payload?.url ?? args[0] ?? ""))
    await emit({ event: "plan", title: plan.title, total: plan.total_count, added: plan.added.length, unchanged: plan.unchanged_count })
    for (let index = 0; index < plan.added.length; index += 1) {
      const entry = plan.added[index]
      await emit({ event: "status", message: `[${index + 1}/${plan.added.length}] downloading ${entry.url}` }, 150)
      if (index === 1) {
        await emit({ event: "status", message: `[${index + 1}/${plan.added.length}] trying KlickAud fallback for ${entry.url}` }, 80)
        await emit({ event: "status", message: `[${index + 1}/${plan.added.length}] KlickAud: Preparing audio for ${entry.url}` }, 80)
        await emit({ event: "status", message: `[${index + 1}/${plan.added.length}] KlickAud fallback succeeded for ${entry.url}` }, 100)
      } else {
        await emit({ event: "status", message: `[${index + 1}/${plan.added.length}] finished ${entry.url}` }, 100)
      }
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
      removed: 0,
      retained: plan.removed_ids.length,
      unchanged: plan.unchanged_count,
      analyzed: plan.added.length,
      failed_downloads: [],
      download_outcomes: plan.added.map((entry, index) => ({
        id: entry.id,
        title: entry.title,
        url: entry.url,
        outcome: index === 1 ? "fallback_succeeded" : "primary_succeeded",
        download_method: index === 1 ? "klickaud" : "yt-dlp",
        fallback_attempted: index === 1,
        primary_error: index === 1 ? "Primary stream unavailable" : "",
        error: "",
        paths: [`/Users/ariaattar/Downloads/Late Summer Set/${entry.title} [${entry.id}].mp3`],
      })),
      protected_ids: [],
      push: command === "sync" ? {
        playlist_name: String(payload?.playlist_name ?? plan.suggested_playlist),
        added_to_playlist: plan.added.length,
        already_in_playlist: plan.unchanged_count,
        added_cues: payload?.cue_mode === "fill" ? 9 : 0,
        backup_dir: mockDoctor.backup_dir,
      } : {},
      paths: plan.added.map((entry) => `/Users/ariaattar/Downloads/Late Summer Set/${entry.title} [${entry.id}].mp3`),
    }, 220)
  } else {
    await emit({ event: "done", ok: true })
  }

  return { code: 0, messages, stderr: "" }
}
