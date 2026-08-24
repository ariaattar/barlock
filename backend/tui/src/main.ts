import {
  BoxRenderable,
  InputRenderable,
  InputRenderableEvents,
  SelectRenderable,
  SelectRenderableEvents,
  TextRenderable,
  createCliRenderer,
  type CliRenderer,
  type KeyEvent,
  type SelectOption,
} from "@opentui/core"
import { spawn } from "node:child_process"
import { cpus } from "node:os"
import { basename, isAbsolute, join, resolve } from "node:path"

type Config = {
  soundcloud_username: string
  output_dir: string
  workers: number
  fragments: number
  quality: string
  rekordbox_playlist: string
  analyze_after_download: boolean
  write_tags: boolean
  create_import_files: boolean
  direct_rekordbox_push: boolean
  extract_vocal_stems: boolean
  likes_url: string
  config_path: string
}

type BridgeEvent = Record<string, unknown> & { event?: string; ok?: boolean; message?: string }

type DownloadPlan = {
  title: string
  count: number
  entries: DownloadEntry[]
}

type DownloadEntry = {
  url: string
  id: string
  title: string
  label: string
}

type Feature = Record<string, unknown> & {
  path: string
  title: string
  artist: string
  bpm: number
  camelot_key: string
  musical_key: string
  energy: number
  energy_curve?: number[]
  segmentation_mode?: string
  segmentation_confidence?: number
  vocal_class?: string
  tempo_stable?: boolean
}

type Playlist = {
  id: string
  name: string
  path: string
  is_folder: boolean
  song_count: number
}

type SelectItem<T> = {
  label: string
  description?: string
  value: T
}

type FlowProgress = {
  name: string
  step: number
  total: number
  label: string
}

type DownloadSettings = {
  workers: number
  fragments: number
  quality: string
}

class Back extends Error {}

const BACKEND_DIR = resolve(import.meta.dir, "..", "..")
const HOME = process.env.HOME || ""

const theme = {
  border: "#3C6E71",
  title: "#64D2FF",
  text: "#E8E8E8",
  dim: "#8B949E",
  accent: "#7BD88F",
  warn: "#FFD166",
  error: "#FF6B6B",
  panel: "#101316",
  selected: "#2F5D62",
}

class Bridge {
  async json(command: string, args: string[] = [], input?: unknown): Promise<Record<string, unknown>> {
    const lines: string[] = []
    const { code, stderr } = await this.run(command, args, input, (event, raw) => {
      if (event) {
        lines.push(JSON.stringify(event))
      } else if (raw.trim()) {
        lines.push(raw)
      }
    })
    const last = [...lines].reverse().find((line) => line.trim().startsWith("{"))
    if (!last) {
      throw new Error(stderr.trim() || `Bridge command ${command} returned no JSON`)
    }
    const parsed = JSON.parse(last)
    if (code !== 0 || parsed.ok === false) {
      throw new Error(String(parsed.message || stderr.trim() || `Bridge command ${command} failed`))
    }
    return parsed
  }

  async events(
    command: string,
    args: string[] = [],
    input: unknown,
    onEvent: (event: BridgeEvent) => void,
  ): Promise<BridgeEvent> {
    let finalEvent: BridgeEvent | null = null
    let errorMessage = ""
    const { code, stderr } = await this.run(command, args, input, (event, raw) => {
      if (!event) {
        if (raw.trim()) {
          onEvent({ event: "status", message: raw.trim() })
        }
        return
      }
      if (event.event === "done") {
        finalEvent = event
      } else if (event.event === "error") {
        errorMessage = String(event.message || "")
      }
      onEvent(event)
    })
    const done = finalEvent as BridgeEvent | null
    if (code !== 0 && !done) {
      throw new Error(errorMessage || stderr.trim() || `Bridge command ${command} failed`)
    }
    if (!done) {
      throw new Error(errorMessage || stderr.trim() || `Bridge command ${command} did not finish`)
    }
    if (done.ok === false) {
      throw new Error(String(done.message || errorMessage || "Bridge command failed"))
    }
    return done
  }

  private run(
    command: string,
    args: string[],
    input: unknown,
    onLine: (event: BridgeEvent | null, raw: string) => void,
  ): Promise<{ code: number; stderr: string }> {
    return new Promise((resolveProcess, reject) => {
      const child = spawn(
        "uv",
        ["run", "python", "-m", "app.soundcloud_bridge", command, ...args],
        {
          cwd: BACKEND_DIR,
          stdio: ["pipe", "pipe", "pipe"],
          env: { ...process.env, PATH: launcherPath() },
        },
      )
      let stderr = ""
      let buffer = ""
      child.stdout.setEncoding("utf8")
      child.stderr.setEncoding("utf8")
      child.stdout.on("data", (chunk: string) => {
        buffer += chunk
        let newline = buffer.indexOf("\n")
        while (newline >= 0) {
          const line = buffer.slice(0, newline)
          buffer = buffer.slice(newline + 1)
          this.handleLine(line, onLine)
          newline = buffer.indexOf("\n")
        }
      })
      child.stderr.on("data", (chunk: string) => {
        stderr += chunk
      })
      child.on("error", reject)
      child.on("close", (code) => {
        if (buffer.trim()) {
          this.handleLine(buffer, onLine)
        }
        resolveProcess({ code: code ?? 0, stderr })
      })
      if (input === undefined) {
        child.stdin.end()
      } else {
        child.stdin.end(JSON.stringify(input))
      }
    })
  }

  private handleLine(line: string, onLine: (event: BridgeEvent | null, raw: string) => void): void {
    const trimmed = line.trim()
    if (!trimmed) {
      return
    }
    try {
      onLine(JSON.parse(trimmed), trimmed)
    } catch {
      onLine(null, trimmed)
    }
  }
}

class TuiApp {
  private renderer!: CliRenderer
  private bridge = new Bridge()
  private config!: Config
  private running = true

  async start(): Promise<void> {
    if (process.argv.includes("--smoke")) {
      console.log("OpenTUI smoke ok")
      return
    }

    this.config = (await this.bridge.json("config"))["config"] as Config
    this.renderer = await createCliRenderer({ exitOnCtrlC: true })
    this.renderer.start()
    await this.mainLoop()
    this.shutdown()
  }

  private async mainLoop(): Promise<void> {
    while (this.running) {
      try {
        const choice = await this.select("SoundCloud DL", [
          {
            label: "Download only",
            description: "Track, playlist, or likes link -> folder in Downloads",
            value: "download",
          },
          {
            label: "Sync a SoundCloud URL",
            description: "Download, analyze, and push -> Rekordbox playlist",
            value: "sync",
          },
          {
            label: this.config.soundcloud_username
              ? `Sync likes for @${this.config.soundcloud_username}`
              : "Sync SoundCloud likes",
            description: this.config.likes_url || "Enter a SoundCloud username",
            value: "likes",
          },
          {
            label: "Reanalyze a Rekordbox playlist",
            description: "Re-run cue analysis on tracks already in Rekordbox",
            value: "reanalyze",
          },
          {
            label: "Rekordbox doctor",
            description: "Collection, missing-file, and import checks",
            value: "doctor",
          },
          {
            label: "Settings",
            description: "Username, defaults, vocal stems",
            value: "settings",
          },
          { label: "Quit", value: "quit" },
        ], { allowBack: false, allowQuit: true })

        if (choice === "quit") {
          this.running = false
        } else if (choice === "download") {
          await this.downloadOnlyFlow()
        } else if (choice === "sync") {
          await this.syncFlow()
        } else if (choice === "likes") {
          await this.syncLikesFlow()
        } else if (choice === "reanalyze") {
          await this.reanalyzeFlow()
        } else if (choice === "doctor") {
          await this.doctorFlow()
        } else if (choice === "settings") {
          await this.settingsFlow()
        }
      } catch (error) {
        if (!(error instanceof Back)) {
          await this.message("Error", [errorMessage(error)], "Back")
        }
      }
    }
  }

  private async downloadOnlyFlow(): Promise<void> {
    const previousSubtitle = this.subtitle
    const stepLabels = ["SoundCloud URL", "Review download", "Folder", "Confirm", "Run"]
    const state: {
      url: string
      urls: string[]
      plan?: DownloadPlan
      outputDir?: string
    } = {
      url: "",
      urls: [],
    }
    const updateSubtitle = () => {
      const parts: string[] = []
      if (state.plan) {
        parts.push(`SoundCloud: ${downloadSourceName(state.plan)}`)
      } else if (state.url) {
        parts.push(`SoundCloud: ${state.url}`)
      }
      if (state.outputDir) {
        parts.push(`Folder: ${state.outputDir}`)
      }
      this.subtitle = parts.join("    ->    ")
    }
    let step = 0
    const flow = () => ({
      name: "Download",
      step: step + 1,
      total: stepLabels.length,
      label: stepLabels[step],
    })

    try {
      while (step < stepLabels.length && this.running) {
        try {
          const current = stepLabels[step]
          if (current === "SoundCloud URL") {
            const value = await this.input("Download SoundCloud", "Paste SoundCloud URL(s)", state.url, {
              placeholder: "https://soundcloud.com/you/sets/test",
              flow: flow(),
            })
            const urls = splitUrls(value)
            if (!urls.length) {
              return
            }
            if (value.trim() !== state.url) {
              state.url = value.trim()
              state.urls = urls
              state.plan = undefined
              state.outputDir = undefined
              updateSubtitle()
            }
            step += 1
          } else if (current === "Review download") {
            if (!state.plan) {
              state.plan = await this.status("Reading SoundCloud", ["Expanding SoundCloud URL(s)..."], async (write) => {
                const raw = (await this.bridge.json("plan", state.urls)) as unknown as DownloadPlan & { ok: boolean }
                write(`Title: ${downloadSourceName(raw)}`)
                write(`Tracks: ${raw.count}`)
                return raw
              }, flow())
              if (!state.plan.count) {
                await this.message("No Tracks", ["No downloadable tracks were found."], "Back", flow())
                return
              }
              state.outputDir = suggestedOutputFolder(this.config.output_dir, downloadSourceName(state.plan))
              updateSubtitle()
            }
            const preview = state.plan.entries.slice(0, 8).map((entry, index) => `${index + 1}. ${entry.label}`)
            if (state.plan.entries.length > preview.length) {
              preview.push(`... ${state.plan.entries.length - preview.length} more`)
            }
            await this.message(
              "Download Plan",
              [
                `Source: ${downloadSourceName(state.plan)}`,
                `Tracks: ${state.plan.count}`,
                "",
                ...preview,
              ],
              "Continue",
              flow(),
            )
            step += 1
          } else if (current === "Folder") {
            if (!state.plan) {
              step = Math.max(0, step - 1)
              continue
            }
            const def = state.outputDir ?? suggestedOutputFolder(this.config.output_dir, downloadSourceName(state.plan))
            const folder = await this.input("Download Folder", "Folder name or full path", def, {
              placeholder: "set-2",
              flow: flow(),
            })
            state.outputDir = resolveDownloadPath(folder, def)
            updateSubtitle()
            step += 1
          } else if (current === "Confirm") {
            if (!state.plan || !state.outputDir) {
              step = Math.max(0, step - 1)
              continue
            }
            const confirmed = await this.confirm(
              "Confirm Download",
              [
                `Download "${downloadSourceName(state.plan)}".`,
                `Tracks: ${state.plan.count}`,
                `Folder: ${state.outputDir}`,
                "Reruns reuse this folder's archive so already downloaded tracks are skipped.",
                "No analysis, tags, or Rekordbox changes.",
              ],
              true,
              flow(),
            )
            if (!confirmed) {
              return
            }
            step += 1
          } else if (current === "Run") {
            if (!state.plan || !state.outputDir) {
              step = Math.max(0, step - 1)
              continue
            }
            const settings = optimalDownloadSettings(this.config)
            const final = await this.status("Downloading", [], async (write) => {
              return await this.bridge.events(
                "download",
                [
                  "--output-dir",
                  state.outputDir!,
                  "--workers",
                  String(settings.workers),
                  "--fragments",
                  String(settings.fragments),
                  "--quality",
                  settings.quality,
                  ...state.urls,
                ],
                undefined,
                (event) => {
                  if (event.message) {
                    write(String(event.message))
                  } else if (event.event === "plan") {
                    write(`Found ${event.count} track(s).`)
                  }
                },
              )
            }, flow())
            const failures = (final.failures || []) as Array<{
              title?: string
              url?: string
              id?: string
              error?: string
            }>
            const lines: string[] = [
              `Source: ${final.title || downloadSourceName(state.plan)}`,
              `Folder: ${final.output_dir || state.outputDir}`,
              `Tracks available locally: ${Array.isArray(final.paths) ? final.paths.length : final.downloaded_count || 0}`,
              failures.length ? `Failed: ${failures.length}` : "",
            ].filter(Boolean) as string[]
            if (failures.length) {
              lines.push("")
              lines.push(`${failures.length} track(s) could not be downloaded:`)
              for (const failure of failures.slice(0, 8)) {
                const label = failure.title || failure.url || failure.id || "(unknown)"
                const reason = failure.error ? ` - ${truncateReason(failure.error)}` : ""
                lines.push(`   - ${label}${reason}`)
              }
              if (failures.length > 8) {
                lines.push(`   - ... ${failures.length - 8} more`)
              }
              if (final.failed_report) {
                lines.push(`Report: ${final.failed_report}`)
              }
            }
            await this.message("Download Complete", lines, "Done", flow(), false)
            return
          }
        } catch (error) {
          if (error instanceof Back) {
            if (step === 0) {
              return
            }
            step -= 1
            continue
          }
          throw error
        }
      }
    } finally {
      this.subtitle = previousSubtitle
    }
  }

  private async syncFlow(prefilledUrl?: string): Promise<void> {
    const previousSubtitle = this.subtitle
    this.subtitle = prefilledUrl ? `SoundCloud: ${prefilledUrl}` : ""
    const updateSubtitle = () => {
      const parts: string[] = []
      if (state.plan?.title) {
        parts.push(`SoundCloud: ${state.plan.title}`)
      } else if (state.url) {
        parts.push(`SoundCloud: ${state.url}`)
      }
      if (state.playlistName) {
        parts.push(`Rekordbox: ${state.playlistName}`)
      } else if (state.plan?.suggested_playlist) {
        parts.push(`Rekordbox: ${state.plan.suggested_playlist}`)
      }
      this.subtitle = parts.join("    →    ")
    }
    const stepLabels = prefilledUrl
      ? ["Review sync", "Playlist name", "Analyze", "Confirm", "Run"]
      : ["SoundCloud URL", "Review sync", "Playlist name", "Analyze", "Confirm", "Run"]
    type SyncPlan = {
      url: string
      title: string
      target_dir: string
      suggested_playlist: string
      rekordbox_playlist_id: string
      total_count: number
      added: DownloadEntry[]
      removed_ids: string[]
      unchanged_count: number
      is_first_sync: boolean
    }
    const state: {
      url: string
      plan?: SyncPlan
      playlistName?: string
      analyze?: boolean
    } = {
      url: prefilledUrl || "",
    }
    let step = 0
    const flow = () => ({
      name: "Sync",
      step: step + 1,
      total: stepLabels.length,
      label: stepLabels[step],
    })

    try {
    while (step < stepLabels.length && this.running) {
      try {
        const current = stepLabels[step]
        if (current === "SoundCloud URL") {
          const value = await this.input("Sync SoundCloud", "Paste a SoundCloud URL", state.url, {
            placeholder: "https://soundcloud.com/you/sets/test",
            flow: flow(),
          })
          const trimmed = value.trim()
          if (!trimmed) {
            return
          }
          if (trimmed !== state.url) {
            state.url = trimmed
            state.plan = undefined
            state.playlistName = undefined
            updateSubtitle()
          }
          step += 1
        } else if (current === "Review sync") {
          if (!state.plan) {
            state.plan = await this.status("Reading SoundCloud", ["Expanding playlist..."], async (write) => {
              const raw = (await this.bridge.json("sync-plan", [state.url])) as unknown as SyncPlan & { ok: boolean }
              const lines = [
                `Title: ${raw.title}`,
                `Folder: ${raw.target_dir}`,
                raw.is_first_sync ? `First sync — ${raw.total_count} track(s) to download.` : `${raw.added.length} new, ${raw.removed_ids.length} removed, ${raw.unchanged_count} unchanged.`,
              ]
              for (const line of lines) {
                write(line)
              }
              return raw
            }, flow())
            updateSubtitle()
          }
          const summary = state.plan.is_first_sync
            ? [`First sync of "${state.plan.title}".`, `Tracks: ${state.plan.total_count}`, `Folder: ${state.plan.target_dir}`]
            : [
                `Re-sync of "${state.plan.title}".`,
                `New: ${state.plan.added.length}   Removed: ${state.plan.removed_ids.length}   Unchanged: ${state.plan.unchanged_count}`,
                `Folder: ${state.plan.target_dir}`,
              ]
          const preview = state.plan.added.slice(0, 6).map((entry, index) => `+ ${index + 1}. ${entry.label}`)
          if (state.plan.added.length > preview.length) {
            preview.push(`+ ... ${state.plan.added.length - preview.length} more`)
          }
          if (state.plan.removed_ids.length) {
            preview.push(`- ${state.plan.removed_ids.length} removed from SoundCloud`)
          }
          await this.message("Sync Plan", [...summary, "", ...preview], "Continue", flow())
          step += 1
        } else if (current === "Playlist name") {
          if (!state.plan) {
            step = Math.max(0, step - 1)
            continue
          }
          const def = state.playlistName ?? state.plan.suggested_playlist
          const name = await this.input("Rekordbox Playlist", "Name (defaults to the SoundCloud title)", def, {
            flow: flow(),
          })
          state.playlistName = name.trim() || state.plan.suggested_playlist
          updateSubtitle()
          step += 1
        } else if (current === "Analyze") {
          state.analyze = await this.confirm(
            "Analyze",
            ["Analyze new tracks (BPM, key, cues) before pushing?"],
            this.config.analyze_after_download,
            flow(),
          )
          step += 1
        } else if (current === "Confirm") {
          if (!state.plan) {
            step = Math.max(0, step - 1)
            continue
          }
          const lines = [
            `Sync "${state.plan.title}" to Rekordbox playlist "${state.playlistName}".`,
            state.plan.is_first_sync
              ? `${state.plan.total_count} new track(s) to download.`
              : `${state.plan.added.length} new   ${state.plan.removed_ids.length} removed   ${state.plan.unchanged_count} unchanged`,
            state.analyze ? "Analyze enabled — cues will be written." : "Analyze disabled — collection only.",
          ]
          const confirmed = await this.confirm("Confirm Sync", lines, true, flow())
          if (!confirmed) {
            return
          }
          // Pre-check Rekordbox state before launching the heavier work.
          const running = await this.bridge.json("rekordbox-running")
          if (running.running) {
            const action = await this.select("Rekordbox Is Open", [
              { label: "Close Rekordbox and continue", description: "Required for direct DB writes", value: "close" },
              { label: "I closed it, check again", value: "check" },
              { label: "Cancel", value: "cancel" },
            ], { flow: flow() })
            if (action === "cancel") {
              return
            }
            if (action === "close") {
              await this.status("Closing Rekordbox", ["Asking Rekordbox to quit..."], async () => {
                return await this.bridge.json("close-rekordbox")
              }, flow())
            }
            const after = await this.bridge.json("rekordbox-running")
            if (after.running) {
              await this.message("Rekordbox Still Open", ["Close Rekordbox, then try the sync again."], "Back", flow())
              return
            }
          }
          step += 1
        } else if (current === "Run") {
          if (!state.plan) {
            step = Math.max(0, step - 1)
            continue
          }
          const final = await this.status("Syncing", [], async (write) => {
            return await this.bridge.events(
              "sync",
              [],
              {
                url: state.url,
                playlist_name: state.playlistName,
                analyze: state.analyze ?? true,
                write_tags: this.config.write_tags,
                push: true,
              },
              (event) => {
                if (event.message) {
                  write(String(event.message))
                }
              },
            )
          }, flow())
          const push = (final.push || {}) as Record<string, unknown>
          const pendingGridAlignment = Number(push.pending_grid_alignment || 0)
          const gridRepair = pendingGridAlignment > 0
            ? await this.finalizeGridAfterImport(pendingGridAlignment)
            : null
          const failedDownloads = (final.failed_downloads || []) as Array<{
            id: string
            title: string
            url: string
            error: string
          }>
          const lines: string[] = [
            `Title: ${final.title}`,
            `Folder: ${final.target_dir}`,
            `Added: ${final.added}   Removed: ${final.removed}   Unchanged: ${final.unchanged}`,
            push.playlist_name
              ? `Rekordbox playlist: ${push.playlist_name}`
              : "Rekordbox push skipped.",
            push.playlist_name
              ? `Added to playlist: ${push.added_to_playlist}   Removed: ${push.removed_from_playlist}   Cues: ${push.added_cues}   Loops: ${push.added_loops}`
              : "",
            push.backup_dir ? `Backup: ${push.backup_dir}` : "",
            gridRepair
              ? `Grid-aligned loops: ${gridRepair.repaired || 0}`
              : pendingGridAlignment
                ? `${pendingGridAlignment} track(s) still need grid alignment in Rekordbox Doctor.`
                : "",
          ]
          if (failedDownloads.length) {
            lines.push("")
            lines.push(`⚠  ${failedDownloads.length} track(s) could not be downloaded:`)
            for (const failure of failedDownloads.slice(0, 8)) {
              const label = failure.title || failure.url || failure.id || "(unknown)"
              const reason = failure.error ? ` — ${truncateReason(failure.error)}` : ""
              lines.push(`   • ${label}${reason}`)
            }
            if (failedDownloads.length > 8) {
              lines.push(`   • ... ${failedDownloads.length - 8} more (see failed-downloads.txt)`)
            }
            lines.push(`   These will be retried next sync.`)
          }
          await this.message("Sync Complete", lines.filter(Boolean) as string[], "Done", flow(), false)
          return
        }
      } catch (error) {
        if (error instanceof Back) {
          if (step === 0) {
            return
          }
          step -= 1
          continue
        }
        throw error
      }
    }
    } finally {
      this.subtitle = previousSubtitle
    }
  }

  private async reanalyzeFlow(): Promise<void> {
    const previousSubtitle = this.subtitle
    type TrackRow = {
      content_id: string
      title: string
      artist: string
      folder_path: string
      file_exists: boolean
      soundcloud_dl_managed: boolean
    }
    const stepLabels = ["Playlist", "Scope", "Confirm", "Run"]
    let step = 0
    let playlist: Playlist | undefined
    let tracks: TrackRow[] | undefined
    let scope: "all" | "subset" | undefined
    let selectedIds: string[] | undefined
    const flow = () => ({
      name: "Reanalyze",
      step: step + 1,
      total: stepLabels.length,
      label: stepLabels[step],
    })

    try {
      while (step < stepLabels.length && this.running) {
        try {
          const current = stepLabels[step]
          if (current === "Playlist") {
            playlist = await this.pickRekordboxPlaylist(flow())
            this.subtitle = `Rekordbox: ${playlist.path}`
            tracks = undefined
            scope = undefined
            selectedIds = undefined
            step += 1
          } else if (current === "Scope") {
            if (!playlist) {
              step = 0
              continue
            }
            if (!tracks) {
              tracks = await this.status("Reading playlist", ["Loading tracks from Rekordbox..."], async (write) => {
                const raw = await this.bridge.json("list-playlist-tracks", ["--playlist-id", playlist!.id])
                const list = (raw.tracks || []) as TrackRow[]
                write(`Found ${list.length} track(s).`)
                return list
              }, flow())
            }
            if (!tracks.length) {
              await this.message("Empty Playlist", ["This playlist has no tracks."], "Back", flow())
              return
            }
            const missingCount = tracks.filter((t) => !t.file_exists).length
            const scopeChoice = await this.select(
              "Reanalyze Scope",
              [
                {
                  label: `All ${tracks.length} track(s)`,
                  description: missingCount ? `${missingCount} have missing files and will be skipped` : "",
                  value: "all" as const,
                },
                {
                  label: "Pick individual tracks",
                  description: "Multi-select picker",
                  value: "subset" as const,
                },
              ],
              { flow: flow() },
            )
            scope = scopeChoice
            if (scope === "subset") {
              selectedIds = await this.pickTracks(tracks, flow())
              if (!selectedIds.length) {
                return
              }
            } else {
              selectedIds = tracks.filter((t) => t.file_exists).map((t) => t.content_id)
            }
            step += 1
          } else if (current === "Confirm") {
            if (!playlist || !tracks || !selectedIds) {
              step = Math.max(0, step - 1)
              continue
            }
            const runnable = tracks.filter((t) => selectedIds!.includes(t.content_id) && t.file_exists)
            const missing = tracks.filter((t) => selectedIds!.includes(t.content_id) && !t.file_exists)
            const lines = [
              `Reanalyze ${runnable.length} track(s) in "${playlist.name}".`,
              missing.length ? `${missing.length} track(s) have missing files and will be skipped.` : "",
              "Cache will be bypassed so the latest analyzer runs.",
              "Auto-generated cues will be rewritten in Rekordbox.",
            ].filter(Boolean) as string[]
            const confirmed = await this.confirm("Confirm Reanalyze", lines, true, flow())
            if (!confirmed) {
              return
            }
            const running = await this.bridge.json("rekordbox-running")
            if (running.running) {
              const action = await this.select("Rekordbox Is Open", [
                { label: "Close Rekordbox and continue", description: "Required for direct DB writes", value: "close" },
                { label: "I closed it, check again", value: "check" },
                { label: "Cancel", value: "cancel" },
              ], { flow: flow() })
              if (action === "cancel") {
                return
              }
              if (action === "close") {
                await this.status("Closing Rekordbox", ["Asking Rekordbox to quit..."], async () => {
                  return await this.bridge.json("close-rekordbox")
                }, flow())
              }
              const after = await this.bridge.json("rekordbox-running")
              if (after.running) {
                await this.message("Rekordbox Still Open", ["Close Rekordbox, then try again."], "Back", flow())
                return
              }
            }
            step += 1
          } else if (current === "Run") {
            if (!playlist || !selectedIds) {
              step = Math.max(0, step - 1)
              continue
            }
            const final = await this.status("Reanalyzing", [], async (write) => {
              return await this.bridge.events(
                "reanalyze-playlist",
                [],
                {
                  playlist_id: playlist!.id,
                  content_ids: selectedIds,
                  force_no_cache: true,
                },
                (event) => {
                  if (event.message) {
                    write(String(event.message))
                  }
                },
              )
            }, flow())
            const missing = (final.missing || []) as TrackRow[]
            const lines: string[] = [
              `Playlist: ${final.playlist_name}`,
              `Analyzed: ${final.analyzed}`,
              `Cues added: ${final.added_cues}   Loops added: ${final.added_loops}`,
              `Backup: ${final.backup_dir}`,
            ]
            if (missing.length) {
              lines.push("")
              lines.push(`⚠  ${missing.length} track(s) skipped (file missing):`)
              for (const t of missing.slice(0, 8)) {
                const label = t.title || t.folder_path || t.content_id
                lines.push(`   • ${label}`)
              }
              if (missing.length > 8) {
                lines.push(`   • ... ${missing.length - 8} more`)
              }
            }
            await this.message("Reanalyze Complete", lines, "Done", flow(), false)
            return
          }
        } catch (error) {
          if (error instanceof Back) {
            if (step === 0) {
              return
            }
            step -= 1
            continue
          }
          throw error
        }
      }
    } finally {
      this.subtitle = previousSubtitle
    }
  }

  private async pickRekordboxPlaylist(flow?: FlowProgress): Promise<Playlist> {
    const playlists = await this.status(
      "Loading Rekordbox Playlists",
      ["Reading playlists from Rekordbox..."],
      async (write) => {
        const raw = await this.bridge.json("list-playlists")
        const list = ((raw.playlists || []) as Playlist[]).filter((pl) => !pl.is_folder)
        write(`Found ${list.length} playlist(s).`)
        return list
      },
      flow,
    )
    if (!playlists.length) {
      throw new Error("No Rekordbox playlists found.")
    }

    const sortedPlaylists = [...playlists].sort((a, b) => b.song_count - a.song_count)
    let filtered = sortedPlaylists
    while (true) {
      const filterLabel = filtered === sortedPlaylists
        ? `🔍  Search (showing all ${sortedPlaylists.length})`
        : `🔍  Search (filtered to ${filtered.length} of ${sortedPlaylists.length})`
      const items: SelectItem<Playlist | "__search__" | "__clear__">[] = [
        { label: filterLabel, value: "__search__" },
      ]
      if (filtered !== sortedPlaylists) {
        items.push({ label: "✕  Clear search", value: "__clear__" })
      }
      for (const pl of filtered.slice(0, 200)) {
        items.push({
          label: pl.path,
          description: `${pl.song_count} track(s)`,
          value: pl,
        })
      }
      if (filtered.length > 200) {
        items.push({
          label: `... ${filtered.length - 200} more — use search to narrow`,
          value: "__search__",
        })
      }
      const choice = await this.select("Select Playlist", items, { flow })
      if (choice === "__search__") {
        const term = (await this.input("Filter Playlists", "Substring (blank for all)", "", { flow })).trim().toLowerCase()
        filtered = term
          ? sortedPlaylists.filter((pl) => pl.path.toLowerCase().includes(term))
          : sortedPlaylists
        if (!filtered.length) {
          await this.message("No Matches", [`No playlists matched "${term}".`], "Back", flow)
          filtered = sortedPlaylists
        }
        continue
      }
      if (choice === "__clear__") {
        filtered = sortedPlaylists
        continue
      }
      return choice as Playlist
    }
  }

  private async finalizeGridAfterImport(
    pendingTracks: number,
  ): Promise<Record<string, unknown> | null> {
    try {
      const action = await this.select("Finish Loop Alignment", [
        {
          label: "Open Rekordbox and finish now",
          description: `${pendingTracks} new track(s) need Rekordbox's beat grid`,
          value: "finish" as const,
        },
        {
          label: "Finish later in Rekordbox Doctor",
          description: "Loops remain provisional until then",
          value: "later" as const,
        },
      ])
      if (action !== "finish") {
        return null
      }

      const opened = await this.status(
        "Opening Rekordbox",
        ["Starting Rekordbox so it can analyze the imported tracks..."],
        async () => await this.bridge.json("open-rekordbox"),
      )
      if (!opened.opened) {
        await this.message(
          "Could Not Open Rekordbox",
          ["Open Rekordbox manually, let it analyze the tracks, then use Rekordbox Doctor."],
          "Back",
        )
        return null
      }

      await this.message(
        "Analyze Imported Tracks",
        [
          `Wait for Rekordbox to finish analyzing ${pendingTracks} imported track(s).`,
          "If analysis does not start automatically, select the imported playlist, select all tracks, and choose Analyze Track.",
          "Return here only after Rekordbox's analysis jobs are finished.",
        ],
        "Analysis Finished",
      )

      const close = await this.select("Finalize Loop Grid", [
        {
          label: "Close Rekordbox and align loops",
          description: "Creates a backup before updating generated loops",
          value: "close" as const,
        },
        {
          label: "Finish later in Rekordbox Doctor",
          value: "later" as const,
        },
      ])
      if (close !== "close") {
        return null
      }

      await this.status("Closing Rekordbox", ["Waiting for Rekordbox to close safely..."], async () => {
        return await this.bridge.json("close-rekordbox")
      })
      const running = await this.bridge.json("rekordbox-running")
      if (running.running) {
        await this.message(
          "Rekordbox Still Open",
          ["Close Rekordbox, then run Rekordbox Doctor to align the loops."],
          "Back",
        )
        return null
      }

      const repaired = await this.status("Aligning Imported Loops", [], async () => {
        return await this.bridge.json("repair-generated-off-grid-loops")
      })
      if (!Number(repaired.repaired || 0)) {
        await this.message(
          "No Loops Aligned",
          [
            "Rekordbox did not create a beat grid for the imported tracks yet.",
            "Finish analysis in Rekordbox, then run Rekordbox Doctor.",
          ],
          "Back",
        )
        return null
      }
      return repaired
    } catch (error) {
      if (error instanceof Back) {
        return null
      }
      throw error
    }
  }

  private async pickTracks(
    tracks: Array<{ content_id: string; title: string; artist: string; file_exists: boolean }>,
    flow?: FlowProgress,
  ): Promise<string[]> {
    const selected = new Set<string>()
    while (true) {
      const items: SelectItem<string>[] = [
        { label: `✔ Done — ${selected.size} selected`, value: "__done__" },
        { label: "Select all", value: "__all__" },
        { label: "Clear selection", value: "__clear__" },
      ]
      for (const t of tracks) {
        const mark = selected.has(t.content_id) ? "[x]" : "[ ]"
        const warn = t.file_exists ? "" : "  (missing)"
        const artist = t.artist ? `${t.artist} - ` : ""
        items.push({
          label: `${mark} ${artist}${t.title}${warn}`,
          value: t.content_id,
        })
      }
      const choice = await this.select(
        "Pick Tracks (Enter toggles, Done to continue)",
        items,
        { flow },
      )
      if (choice === "__done__") {
        return Array.from(selected)
      }
      if (choice === "__all__") {
        for (const t of tracks) {
          if (t.file_exists) {
            selected.add(t.content_id)
          }
        }
        continue
      }
      if (choice === "__clear__") {
        selected.clear()
        continue
      }
      if (selected.has(choice)) {
        selected.delete(choice)
      } else {
        selected.add(choice)
      }
    }
  }

  private async doctorFlow(): Promise<void> {
    const raw = await this.bridge.json("doctor", ["--output-dir", this.config.output_dir])
    const data = raw.doctor as Record<string, unknown>
    const activeLoops = (data.generated_active_loops || []) as Array<{
      title?: string
      artist?: string
      cue_name?: string
      in_msec?: number
    }>
    const offGridLoops = (data.generated_off_grid_loops || []) as Array<{
      title?: string
      artist?: string
      cue_name?: string
      old_in_msec?: number
      new_in_msec?: number
      loop_beats?: number
    }>
    const lines = [
      `Rekordbox running: ${data.rekordbox_running ? "yes" : "no"}`,
      `Collection tracks: ${data.collection_tracks}`,
      `Local tracks: ${data.local_tracks}`,
      `Missing files: ${(data.missing_files as unknown[]).length}`,
      `Downloaded not imported: ${(data.unimported_output_files as unknown[]).length}`,
      `Generated active loops: ${activeLoops.length}`,
      `Generated off-grid loops: ${offGridLoops.length}`,
    ]
    if (!activeLoops.length && !offGridLoops.length) {
      await this.message("Rekordbox Doctor", lines, "Back")
      return
    }

    if (activeLoops.length) {
      lines.push("")
      lines.push("Active loops can make an XDJ load a track at the loop position.")
      for (const issue of activeLoops.slice(0, 4)) {
        const artist = issue.artist ? `${issue.artist} - ` : ""
        lines.push(`- ${artist}${issue.title || "Untitled"}: ${issue.cue_name || "Loop"}`)
      }
      if (activeLoops.length > 4) {
        lines.push(`- ... ${activeLoops.length - 4} more active loops`)
      }
    }
    if (offGridLoops.length) {
      lines.push("")
      lines.push("Off-grid generated loops can be aligned without reanalyzing audio.")
      for (const issue of offGridLoops.slice(0, 4)) {
        const artist = issue.artist ? `${issue.artist} - ` : ""
        lines.push(`- ${artist}${issue.title || "Untitled"}: ${issue.cue_name || "Loop"}`)
      }
      if (offGridLoops.length > 4) {
        lines.push(`- ... ${offGridLoops.length - 4} more off-grid loops`)
      }
    }

    const actions: SelectItem<"active" | "grid" | "back">[] = []
    if (offGridLoops.length) {
      actions.push({
        label: `Align ${offGridLoops.length} generated loop(s) to the Rekordbox grid`,
        description: "Fast repair; backs up Rekordbox and preserves manual loops",
        value: "grid",
      })
    }
    if (activeLoops.length) {
      actions.push({
        label: `Disable ${activeLoops.length} generated active loop(s)`,
        description: "Backs up Rekordbox, preserves manual loops",
        value: "active",
      })
    }
    actions.push({ label: "Back", value: "back" })
    const action = await this.select("Rekordbox Doctor", actions, { intro: lines.join("\n") })
    if (action === "back") {
      return
    }

    if (data.rekordbox_running) {
      const close = await this.select("Rekordbox Is Open", [
        { label: "Close Rekordbox and continue", description: "Required for direct DB writes", value: "close" },
        { label: "Cancel", value: "cancel" },
      ])
      if (close !== "close") {
        return
      }
      await this.status("Closing Rekordbox", ["Asking Rekordbox to quit..."], async () => {
        return await this.bridge.json("close-rekordbox")
      })
    }
    const running = await this.bridge.json("rekordbox-running")
    if (running.running) {
      await this.message("Rekordbox Still Open", ["Close Rekordbox, then run Doctor again."], "Back")
      return
    }
    const aligningGrid = action === "grid"
    const repaired = await this.status(
      aligningGrid ? "Aligning Generated Loops" : "Repairing Active Loops",
      [],
      async () => {
        return await this.bridge.json(
          aligningGrid ? "repair-generated-off-grid-loops" : "repair-generated-active-loops",
        )
      },
    )
    await this.message(
      aligningGrid ? "Generated Loops Aligned" : "Active Loops Disabled",
      [
        `${aligningGrid ? "Aligned" : "Disabled"}: ${repaired.repaired || 0}`,
        repaired.backup_dir ? `Backup: ${repaired.backup_dir}` : "",
        "Re-export the affected playlist to the USB in Rekordbox before testing on the XDJ.",
      ].filter(Boolean) as string[],
      "Done",
    )
  }

  private async settingsFlow(): Promise<void> {
    const soundcloudUsername = await this.input("Settings", "SoundCloud username", this.config.soundcloud_username)
    const outputDir = resolveDownloadPath(await this.input("Settings", "Default output folder", this.config.output_dir), this.config.output_dir)
    const rekordboxPlaylist = await this.input("Settings", "Default Rekordbox playlist", this.config.rekordbox_playlist)
    const analyzeAfterDownload = await this.confirm("Settings", ["Analyze after download by default?"], this.config.analyze_after_download)
    const writeTags = await this.confirm("Settings", ["Write tags by default?"], this.config.write_tags)
    const createImportFiles = await this.confirm("Settings", ["Generate import files by default?"], this.config.create_import_files)
    const directRekordboxPush = await this.confirm("Settings", ["Offer direct Rekordbox push by default?"], this.config.direct_rekordbox_push)
    const extractVocalStems = await this.confirm(
      "Settings",
      ["Extract instrumental stems during analyze? (slow, requires demucs)"],
      this.config.extract_vocal_stems,
    )

    const raw = await this.bridge.json("save-config", [], {
      soundcloud_username: soundcloudUsername.trim() || this.config.soundcloud_username,
      output_dir: outputDir,
      rekordbox_playlist: rekordboxPlaylist.trim() || this.config.rekordbox_playlist,
      analyze_after_download: analyzeAfterDownload,
      write_tags: writeTags,
      create_import_files: createImportFiles,
      direct_rekordbox_push: directRekordboxPush,
      extract_vocal_stems: extractVocalStems,
    })
    this.config = raw.config as Config
    await this.message("Settings Saved", [this.config.config_path], "Back")
  }

  private async syncLikesFlow(): Promise<void> {
    const enteredUsername = await this.input(
      "Sync SoundCloud Likes",
      "SoundCloud username",
      this.config.soundcloud_username,
      { placeholder: "username" },
    )
    const username = enteredUsername.trim().replace(/^@/, "").replace(/^\/+|\/+$/g, "")
    if (!username) {
      await this.message("Username Required", ["Enter a SoundCloud username to sync likes."], "Back")
      return
    }

    if (username !== this.config.soundcloud_username) {
      const raw = await this.bridge.json("save-config", [], { soundcloud_username: username })
      this.config = raw.config as Config
    }
    await this.syncFlow(`https://soundcloud.com/${username}/likes`)
  }

  private async confirm(title: string, lines: string[], defaultValue: boolean, flow?: FlowProgress): Promise<boolean> {
    const prefix = lines.length ? lines.join("\n") + "\n\n" : ""
    return await this.select(title, [
      { label: "Yes", description: defaultValue ? "Default" : "", value: true },
      { label: "No", description: !defaultValue ? "Default" : "", value: false },
    ], { intro: prefix, defaultIndex: defaultValue ? 0 : 1, flow })
  }

  private async message(
    title: string,
    lines: string[],
    actionLabel: string,
    flow?: FlowProgress,
    allowBack = true,
  ): Promise<void> {
    await this.select(title, [{ label: actionLabel, value: "ok" }], {
      intro: lines.join("\n"),
      allowBack,
      flow,
    })
  }

  private async status<T>(
    title: string,
    initial: string[],
    run: (write: (line: string) => void) => Promise<T>,
    flow?: FlowProgress,
  ): Promise<T> {
    const lines = [...initial]
    let text: TextRenderable | null = null
    const render = () => {
      if (text) {
        text.content = tail(lines, Math.max(8, this.renderer.height - 10)).join("\n") || "Working..."
        this.renderer.requestRender()
      }
    }
    this.renderShell(title, (panel) => {
      text = new TextRenderable(this.renderer, {
        content: tail(lines, Math.max(8, this.renderer.height - 10)).join("\n") || "Working...",
        fg: theme.text,
        width: "100%",
        height: "auto",
      })
      panel.add(text)
    }, "Working...", flow)
    const write = (line: string) => {
      lines.push(line)
      render()
    }
    try {
      const result = await run(write)
      lines.push("Done.")
      render()
      await delay(250)
      return result
    } catch (error) {
      lines.push(errorMessage(error))
      render()
      await delay(500)
      throw error
    }
  }

  private input(
    title: string,
    label: string,
    defaultValue: string,
    opts: { placeholder?: string; flow?: FlowProgress } = {},
  ): Promise<string> {
    return new Promise((resolveInput, reject) => {
      let input!: InputRenderable
      const cleanup = this.keyScope((key) => {
        if (isInputBackKey(key)) {
          cleanup()
          reject(new Back())
        }
      })
      this.renderShell(title, (panel) => {
        panel.add(new TextRenderable(this.renderer, {
          content: label,
          fg: theme.dim,
          width: "100%",
          height: 1,
        }))
        input = new InputRenderable(this.renderer, {
          id: "input",
          value: defaultValue,
          placeholder: opts.placeholder || "",
          width: "100%",
          backgroundColor: "#151A1E",
          focusedBackgroundColor: "#202830",
          textColor: theme.text,
          cursorColor: theme.accent,
        })
        input.on(InputRenderableEvents.ENTER, (value: string) => {
          cleanup()
          if (isBackValue(value)) {
            reject(new Back())
          } else {
            resolveInput(value)
          }
        })
        panel.add(input)
      }, "Enter to continue | Esc/Left to go back", opts.flow)
      input.focus()
    })
  }

  private select<T>(
    title: string,
    items: SelectItem<T>[],
    opts: {
      intro?: string
      allowBack?: boolean
      allowQuit?: boolean
      defaultIndex?: number
      flow?: FlowProgress
    } = {},
  ): Promise<T> {
    return new Promise((resolveSelect, reject) => {
      let menu!: SelectRenderable
      const allowBack = opts.allowBack ?? true
      const cleanup = this.keyScope((key) => {
        if (allowBack && isBackKey(key)) {
          cleanup()
          reject(new Back())
        } else if (opts.allowQuit && isQuitKey(key)) {
          cleanup()
          resolveSelect("quit" as T)
        } else if (isQuitKey(key)) {
          cleanup()
          this.running = false
          reject(new Back())
        }
      })
      this.renderShell(title, (panel) => {
        if (opts.intro) {
          panel.add(new TextRenderable(this.renderer, {
            content: opts.intro,
            fg: theme.text,
            width: "100%",
            height: Math.min(8, Math.max(1, opts.intro.split("\n").length)),
          }))
        }
        const options: SelectOption[] = items.map((item) => ({
          name: item.label,
          description: item.description || "",
          value: item.value,
        }))
        menu = new SelectRenderable(this.renderer, {
          id: "select",
          width: "100%",
          height: Math.min(Math.max(items.length * 2, 4), Math.max(6, this.renderer.height - 10)),
          options,
          selectedIndex: opts.defaultIndex ?? 0,
          wrapSelection: true,
          showDescription: true,
          showScrollIndicator: true,
          backgroundColor: "#101316",
          focusedBackgroundColor: "#101316",
          textColor: theme.text,
          selectedBackgroundColor: theme.selected,
          selectedTextColor: "#FFFFFF",
          descriptionColor: theme.dim,
          selectedDescriptionColor: "#D4F4DD",
        })
        menu.on(SelectRenderableEvents.ITEM_SELECTED, (_index: number, option: SelectOption) => {
          cleanup()
          resolveSelect(option.value as T)
        })
        panel.add(menu)
      }, "Up/Down select | Enter choose | Esc/B/Left back | q quit", opts.flow)
      menu.focus()
    })
  }

  private subtitle: string = ""

  private renderShell(
    title: string,
    build: (panel: BoxRenderable) => void,
    footer: string,
    flow?: FlowProgress,
  ): void {
    this.clear()
    const root = new BoxRenderable(this.renderer, {
      id: "screen",
      width: "100%",
      height: "100%",
      flexDirection: "column",
      padding: 1,
      gap: 1,
      backgroundColor: "#07090B",
    })
    const header = new BoxRenderable(this.renderer, {
      width: "100%",
      height: flow ? 6 : 5,
      border: true,
      borderStyle: "rounded",
      borderColor: theme.border,
      paddingX: 2,
      paddingY: 1,
      flexDirection: "column",
      backgroundColor: theme.panel,
    })
    header.add(new TextRenderable(this.renderer, {
      content: title,
      fg: theme.title,
      width: "100%",
      height: 1,
    }))
    header.add(new TextRenderable(this.renderer, {
      content: this.subtitle || (this.config.likes_url
        ? `@${this.config.soundcloud_username} likes: ${this.config.likes_url}`
        : "SoundCloud likes username not set"),
      fg: theme.dim,
      width: "100%",
      height: 1,
    }))
    if (flow) {
      header.add(new TextRenderable(this.renderer, {
        content: formatProgress(flow),
        fg: theme.accent,
        width: "100%",
        height: 1,
      }))
    }
    root.add(header)

    const panel = new BoxRenderable(this.renderer, {
      width: "100%",
      flexGrow: 1,
      border: true,
      borderStyle: "single",
      borderColor: "#263238",
      padding: 1,
      gap: 1,
      flexDirection: "column",
      backgroundColor: theme.panel,
    })
    build(panel)
    root.add(panel)

    root.add(new TextRenderable(this.renderer, {
      content: footer,
      fg: theme.dim,
      width: "100%",
      height: 1,
    }))
    this.renderer.root.add(root)
    this.renderer.requestRender()
  }

  private keyScope(handler: (key: KeyEvent) => void): () => void {
    this.renderer.keyInput.on("keypress", handler)
    return () => this.renderer.keyInput.off("keypress", handler)
  }

  private clear(): void {
    for (const child of [...this.renderer.root.getChildren()]) {
      this.renderer.root.remove(child.id)
      child.destroyRecursively()
    }
  }

  private shutdown(): void {
    if (this.renderer && !this.renderer.isDestroyed) {
      this.clear()
      this.renderer.destroy()
    }
  }
}

const SPARK_CHARS = "▁▂▃▄▅▆▇█"

function renderSparkline(values: number[] | undefined, width: number): string {
  if (!values || values.length === 0) {
    return " ".repeat(width)
  }
  const bucketSize = values.length / width
  const out: string[] = []
  for (let i = 0; i < width; i++) {
    const lo = Math.floor(i * bucketSize)
    const hi = Math.max(lo + 1, Math.floor((i + 1) * bucketSize))
    let sum = 0
    let count = 0
    for (let j = lo; j < hi && j < values.length; j++) {
      sum += values[j]
      count += 1
    }
    const mean = count > 0 ? sum / count : 0
    const idx = Math.max(0, Math.min(SPARK_CHARS.length - 1, Math.round(mean * (SPARK_CHARS.length - 1))))
    out.push(SPARK_CHARS[idx])
  }
  return out.join("")
}

function truncateReason(reason: string): string {
  const single = reason.replace(/\s+/g, " ").trim()
  return single.length > 90 ? single.slice(0, 87) + "..." : single
}

function vocalBadgeFor(vocalClass: string | undefined): string {
  switch (vocalClass) {
    case "vocal":
      return "V"
    case "dub":
      return "D"
    case "instrumental":
      return "I"
    default:
      return "?"
  }
}

function splitUrls(value: string): string[] {
  return value.split(/\s+/).map((item) => item.trim()).filter(Boolean)
}

function suggestedOutputFolder(current: string, sourceName: string): string {
  if (!sourceName || sourceName === "SoundCloud Download" || sourceName === "SoundCloud Likes") {
    return current
  }
  return join(HOME, "Downloads", sourceName.replace(/[^\w .-]+/g, "-").trim() || basename(current))
}

function downloadSourceName(plan: DownloadPlan): string {
  if (plan.title) {
    return plan.title
  }
  if (plan.entries.length === 1) {
    return plan.entries[0]?.title || "SoundCloud Download"
  }
  return "SoundCloud Downloads"
}

function resolveDownloadPath(value: string, defaultPath: string): string {
  const trimmed = value.trim()
  if (!trimmed) {
    return defaultPath
  }
  const expanded = trimmed.startsWith("~/") ? join(HOME, trimmed.slice(2)) : trimmed
  if (isAbsolute(expanded)) {
    return expanded
  }
  if (!expanded.includes("/")) {
    return join(HOME, "Downloads", expanded)
  }
  return join(HOME, "Downloads", expanded)
}

function stringArray(value: unknown): string[] {
  return Array.isArray(value) ? value.map((item) => String(item)) : []
}

function tail<T>(values: T[], count: number): T[] {
  return values.slice(Math.max(0, values.length - count))
}

function delay(ms: number): Promise<void> {
  return new Promise((resolveDelay) => setTimeout(resolveDelay, ms))
}

function optimalDownloadSettings(config: Config): DownloadSettings {
  const cpuCount = Math.max(2, cpus().length || 8)
  const defaultWorkers = Math.min(8, Math.max(4, Math.floor(cpuCount / 2)))
  return {
    workers: clampInt(config.workers, defaultWorkers, 2, 12),
    fragments: clampInt(config.fragments, 8, 4, 12),
    quality: /^\d+$/.test(String(config.quality || "")) ? String(config.quality) : "320",
  }
}

function clampInt(value: unknown, fallback: number, min: number, max: number): number {
  const parsed = Number.parseInt(String(value ?? ""), 10)
  if (!Number.isFinite(parsed)) {
    return fallback
  }
  return Math.max(min, Math.min(max, parsed))
}

function formatProgress(flow: FlowProgress): string {
  const total = Math.max(1, flow.total)
  const step = Math.max(1, Math.min(flow.step, total))
  const width = 18
  const filled = Math.max(1, Math.round((step / total) * width))
  const bar = "#".repeat(filled) + "-".repeat(Math.max(0, width - filled))
  return `${flow.name}  [${bar}]  Step ${step}/${total}: ${flow.label}`
}

function isBackValue(value: string): boolean {
  return ["b", "back", "<"].includes(value.trim().toLowerCase())
}

function isBackKey(key: KeyEvent): boolean {
  return key.name === "escape" || key.name === "left" || key.sequence === "b" || key.sequence === "B"
}

function isInputBackKey(key: KeyEvent): boolean {
  return key.name === "escape" || key.name === "left"
}

function isQuitKey(key: KeyEvent): boolean {
  return key.sequence === "q" || key.sequence === "Q"
}

function errorMessage(error: unknown): string {
  if (error instanceof Error) {
    return error.message
  }
  return String(error)
}

function launcherPath(): string {
  const extras = ["/opt/homebrew/bin", "/usr/local/bin", join(HOME, ".bun", "bin")]
  const current = process.env.PATH || ""
  return [...extras, current].filter(Boolean).join(":")
}

await new TuiApp().start()
