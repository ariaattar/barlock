export type Analysis = {
  track_id: string
  filename: string
  duration_sec: number
  sample_rate: number
  bpm: number
  beats: number[]
  downbeats: number[]
  time_signature: string
  first_downbeat_sec: number
}

export type Cue = {
  slot: number | null
  name: string
  position_sec: number
  color: [number, number, number]
  type: "hot" | "memory" | "loop"
  end_sec: number | null
}

export type TrackSummary = {
  track_id: string
  filename: string
  duration_sec: number
  bpm: number
}

const j = async <T>(r: Response): Promise<T> => {
  if (!r.ok) throw new Error(await r.text())
  return r.json() as Promise<T>
}

export async function analyzeUpload(file: File): Promise<Analysis> {
  const fd = new FormData()
  fd.append("file", file)
  return j(await fetch("/api/analyze", { method: "POST", body: fd }))
}

export async function listTracks(): Promise<TrackSummary[]> {
  return j(await fetch("/api/tracks"))
}

export async function getTrack(id: string): Promise<Analysis> {
  return j(await fetch(`/api/tracks/${id}`))
}

export async function deleteTrack(id: string): Promise<void> {
  await fetch(`/api/tracks/${id}`, { method: "DELETE" })
}

export async function getCues(id: string): Promise<{ cues: Cue[] }> {
  return j(await fetch(`/api/tracks/${id}/cues`))
}

export async function putCues(id: string, cues: Cue[]): Promise<{ cues: Cue[] }> {
  return j(
    await fetch(`/api/tracks/${id}/cues`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ cues }),
    }),
  )
}

export async function exportRekordbox(trackIds: string[]): Promise<Blob> {
  const r = await fetch("/api/export/rekordbox", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ track_ids: trackIds }),
  })
  if (!r.ok) throw new Error(await r.text())
  return r.blob()
}

export function audioUrl(id: string) {
  return `/files/${id}`
}

// ── library ──────────────────────────────────────────────────────────

export type LibraryRoot = { name: string; path: string }

export type LibraryEntry =
  | { type: "dir"; name: string; path: string }
  | { type: "file"; name: string; path: string; size: number }

export type RbPlaylistNode = {
  id: string
  name: string
  is_folder: boolean
  song_count?: number
  children?: RbPlaylistNode[]
}

export type RbSong = {
  rekordbox_id: string
  title: string
  artist: string
  bpm: number | null
  duration_sec: number | null
  path: string
  exists: boolean
}

export async function libraryFolders(): Promise<{
  roots: LibraryRoot[]
  rekordbox_available: boolean
}> {
  return j(await fetch("/api/library/folders"))
}

export async function libraryBrowse(
  path: string,
): Promise<{ path: string; exists: boolean; entries: LibraryEntry[] }> {
  return j(await fetch(`/api/library/browse?path=${encodeURIComponent(path)}`))
}

export async function rbPlaylists(): Promise<{ playlists: RbPlaylistNode[] }> {
  return j(await fetch("/api/library/rekordbox/playlists"))
}

export async function rbPlaylistSongs(id: string): Promise<{ songs: RbSong[] }> {
  return j(await fetch(`/api/library/rekordbox/playlist/${id}`))
}

export async function analyzePath(path: string): Promise<Analysis> {
  return j(
    await fetch("/api/analyze_path", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path }),
    }),
  )
}
