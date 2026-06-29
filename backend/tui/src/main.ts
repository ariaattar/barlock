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
            label: "Download SoundCloud URL or playlist",
            description: "Tracks, sets, public playlists, and likes links",
            value: "download",
          },
          {
            label: `Sync likes for @${this.config.soundcloud_username}`,
            description: this.config.likes_url,
            value: "likes",
          },
          {
            label: "Analyze/tag local download folder",
            description: "BPM, key, energy, cue hints, and ID3 tags",
            value: "analyze",
          },
          {
            label: "Push analyzed tracks to Rekordbox playlist",
            description: "Analyze a folder, then write directly into Rekordbox",
            value: "push",
          },
          {
            label: "Rekordbox doctor",
            description: "Collection, missing-file, and import checks",
            value: "doctor",
          },
          {
            label: "Settings",
            description: "Defaults for downloads, quality, likes, and Rekordbox",
            value: "settings",
          },
          { label: "Quit", value: "quit" },
        ], { allowBack: false, allowQuit: true })

        if (choice === "quit") {
          this.running = false
        } else if (choice === "download") {
          await this.downloadFlow()
        } else if (choice === "likes") {
          await this.downloadFlow([this.config.likes_url], "SoundCloud Likes")
        } else if (choice === "analyze") {
          await this.analyzeFolderFlow(false)
        } else if (choice === "push") {
          await this.analyzeFolderFlow(true)
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

  private async downloadFlow(prefilledUrls?: string[], fallbackName = "SoundCloud Download"): Promise<void> {
    const stepLabels = prefilledUrls
      ? ["Review tracks", "Output folder", "Download", "Analyze", "Tags", "Import files", "Rekordbox"]
      : ["SoundCloud URL", "Review tracks", "Output folder", "Download", "Analyze", "Tags", "Import files", "Rekordbox"]
    const state: {
      urls: string[]
      plan?: DownloadPlan
      sourceName: string
      outputDir?: string
      result?: BridgeEvent
      paths: string[]
      entries: DownloadEntry[]
      features?: Feature[]
      wroteTags?: boolean
      wroteImportFiles?: boolean
    } = {
      urls: prefilledUrls ? [...prefilledUrls] : [],
      sourceName: fallbackName,
      paths: [],
      entries: [],
    }
    const settings = optimalDownloadSettings(this.config)
    let step = 0
    const flow = () => ({
      name: "Download & Import",
      step: step + 1,
      total: stepLabels.length,
      label: stepLabels[step],
    })
    const resetAfterSource = () => {
      state.plan = undefined
      state.outputDir = undefined
      state.result = undefined
      state.paths = []
      state.entries = []
      state.features = undefined
      state.wroteTags = undefined
      state.wroteImportFiles = undefined
    }
    const resetAfterOutput = () => {
      state.result = undefined
      state.paths = []
      state.entries = []
      state.features = undefined
      state.wroteTags = undefined
      state.wroteImportFiles = undefined
    }

    while (step < stepLabels.length && this.running) {
      try {
        const current = stepLabels[step]
        if (current === "SoundCloud URL") {
          const previous = state.urls.join(" ")
          const value = await this.input("SoundCloud URL(s)", "Paste SoundCloud URL(s)", previous, {
            placeholder: "https://soundcloud.com/artist/track",
            flow: flow(),
          })
          const urls = splitUrls(value)
          if (!urls.length) {
            return
          }
          if (urls.join("\n") !== state.urls.join("\n")) {
            state.urls = urls
            resetAfterSource()
          }
          step += 1
        } else if (current === "Review tracks") {
          if (!state.plan) {
            state.plan = await this.status("Expanding SoundCloud", ["Reading playlist metadata..."], async (write) => {
              const plan = (await this.bridge.json("plan", state.urls)) as unknown as DownloadPlan & { ok: boolean }
              write(`Found ${plan.count} track(s).`)
              return plan
            }, flow())
            state.sourceName = state.plan.title || fallbackName
          }
          await this.confirmPlan(state.plan, state.sourceName, flow())
          step += 1
        } else if (current === "Output folder") {
          const outputInput = await this.input(
            "Output Folder",
            "Folder",
            state.outputDir || suggestedOutputFolder(this.config.output_dir, state.sourceName),
            { placeholder: "set-2", flow: flow() },
          )
          const outputDir = resolveDownloadPath(outputInput, this.config.output_dir)
          if (outputDir !== state.outputDir) {
            state.outputDir = outputDir
            resetAfterOutput()
          }
          step += 1
        } else if (current === "Download") {
          if (!state.plan || !state.outputDir) {
            step = Math.max(0, step - 1)
            continue
          }
          if (!state.result) {
            const confirmed = await this.confirm(
              "Download",
              [
                `Download ${state.plan.count} track(s) to`,
                state.outputDir,
                "Download quality and parallelism are optimized automatically.",
              ],
              true,
              flow(),
            )
            if (!confirmed) {
              return
            }
            state.result = await this.status("Downloading", [], async (write) => {
              return await this.bridge.events(
                "download",
                [
                  ...state.urls,
                  "--output-dir",
                  state.outputDir || this.config.output_dir,
                  "--workers",
                  String(settings.workers),
                  "--fragments",
                  String(settings.fragments),
                  "--quality",
                  settings.quality,
                ],
                undefined,
                (event) => {
                  if (event.message) {
                    write(String(event.message))
                  }
                  if (event.event === "done" && Number(event.failed_count || 0) > 0) {
                    write(`Failed ${event.failed_count} track(s). Report: ${event.failed_report}`)
                  }
                },
              )
            }, flow())
            state.paths = stringArray(state.result.paths)
            state.entries = (state.result.entries || []) as DownloadEntry[]
          }
          if (!state.paths.length) {
            await this.message("Download Complete", ["No local MP3 paths were found after download."], "Back", flow())
            return
          }
          await this.message("Download Complete", [
            `${state.paths.length} local file(s) ready.`,
            state.result && Number(state.result.failed_count || 0) > 0
              ? `Failed tracks report: ${state.result.failed_report}`
              : "All available tracks are ready for analysis.",
          ], "Continue", flow())
          step += 1
        } else if (current === "Analyze") {
          if (state.features === undefined) {
            const shouldAnalyze = await this.confirm(
              "Analyze",
              ["Analyze BPM/key/energy and cue hints now?"],
              this.config.analyze_after_download,
              flow(),
            )
            if (!shouldAnalyze) {
              return
            }
            state.features = await this.analyzePaths(state.paths, state.entries, state.outputDir || this.config.output_dir, flow())
          } else {
            await this.analysisSummary(state.features, flow())
          }
          if (!state.features.length) {
            return
          }
          step += 1
        } else if (current === "Tags") {
          if (!state.features?.length) {
            return
          }
          if (state.wroteTags === undefined) {
            const shouldWriteTags = await this.confirm(
              "Tags",
              ["Write BPM/key/title ID3 tags to MP3 files?"],
              this.config.write_tags,
              flow(),
            )
            if (shouldWriteTags) {
              await this.runFeatureCommand("write-tags", "Writing Tags", state.features, {}, flow())
            }
            state.wroteTags = shouldWriteTags
          } else {
            await this.message("Tags", [state.wroteTags ? "Tags are written." : "Tag writing was skipped."], "Continue", flow())
          }
          step += 1
        } else if (current === "Import files") {
          if (!state.features?.length) {
            return
          }
          if (state.wroteImportFiles === undefined) {
            const shouldWriteImport = await this.confirm(
              "Import Files",
              ["Generate Rekordbox M3U/XML import files?"],
              this.config.create_import_files,
              flow(),
            )
            if (shouldWriteImport) {
              await this.runFeatureCommand(
                "write-import-files",
                "Writing Import Files",
                state.features,
                { output_dir: state.outputDir, source_name: state.sourceName },
                flow(),
              )
            }
            state.wroteImportFiles = shouldWriteImport
          } else {
            await this.message(
              "Import Files",
              [state.wroteImportFiles ? "Import files are written." : "Import file generation was skipped."],
              "Continue",
              flow(),
            )
          }
          step += 1
        } else if (current === "Rekordbox") {
          if (!state.features?.length) {
            return
          }
          if (await this.confirm("Rekordbox", ["Push directly to a Rekordbox playlist?"], this.config.direct_rekordbox_push, flow())) {
            await this.pushFeatures(state.features, state.sourceName, state.outputDir || this.config.output_dir)
          }
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
  }

  private async analyzeFolderFlow(pushAfter: boolean): Promise<void> {
    const stepLabels = pushAfter
      ? ["Folder", "Analyze", "Rekordbox"]
      : ["Folder", "Analyze", "Tags", "Import files", "Rekordbox"]
    let step = 0
    let folder = ""
    let features: Feature[] | undefined
    let wroteTags: boolean | undefined
    let wroteImportFiles: boolean | undefined
    const flow = () => ({
      name: pushAfter ? "Folder Push" : "Folder Prep",
      step: step + 1,
      total: stepLabels.length,
      label: stepLabels[step],
    })

    while (step < stepLabels.length && this.running) {
      try {
        const current = stepLabels[step]
        if (current === "Folder") {
          const folderInput = await this.input("Analyze Folder", "Folder", folder || this.config.output_dir, {
            placeholder: "set-2",
            flow: flow(),
          })
          const nextFolder = resolveDownloadPath(folderInput, this.config.output_dir)
          if (nextFolder !== folder) {
            folder = nextFolder
            features = undefined
            wroteTags = undefined
            wroteImportFiles = undefined
          }
          step += 1
        } else if (current === "Analyze") {
          if (!features) {
            features = await this.analyzeFolder(folder, flow())
          } else {
            await this.analysisSummary(features, flow())
          }
          if (!features.length) {
            return
          }
          step += 1
        } else if (current === "Tags") {
          if (!features?.length) {
            return
          }
          if (wroteTags === undefined) {
            const shouldWriteTags = await this.confirm(
              "Tags",
              ["Write BPM/key/title ID3 tags to MP3 files?"],
              this.config.write_tags,
              flow(),
            )
            if (shouldWriteTags) {
              await this.runFeatureCommand("write-tags", "Writing Tags", features, {}, flow())
            }
            wroteTags = shouldWriteTags
          } else {
            await this.message("Tags", [wroteTags ? "Tags are written." : "Tag writing was skipped."], "Continue", flow())
          }
          step += 1
        } else if (current === "Import files") {
          if (!features?.length) {
            return
          }
          if (wroteImportFiles === undefined) {
            const shouldWriteImport = await this.confirm(
              "Import Files",
              ["Generate Rekordbox M3U/XML import files?"],
              this.config.create_import_files,
              flow(),
            )
            if (shouldWriteImport) {
              await this.runFeatureCommand("write-import-files", "Writing Import Files", features, {
                output_dir: folder,
                source_name: basename(folder),
              }, flow())
            }
            wroteImportFiles = shouldWriteImport
          } else {
            await this.message(
              "Import Files",
              [wroteImportFiles ? "Import files are written." : "Import file generation was skipped."],
              "Continue",
              flow(),
            )
          }
          step += 1
        } else if (current === "Rekordbox") {
          if (!features?.length) {
            return
          }
          if (pushAfter || await this.confirm("Rekordbox", ["Push these tracks to a Rekordbox playlist?"], false, flow())) {
            await this.pushFeatures(features, basename(folder), folder)
          }
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
  }

  private async analyzeFolder(folder: string, flow?: FlowProgress): Promise<Feature[]> {
    const final = await this.status("Analyzing", [], async (write) => {
      return await this.bridge.events("analyze", ["--folder", folder, "--output-dir", folder], undefined, (event) => {
        if (event.message) {
          write(String(event.message))
        }
      })
    }, flow)
    const features = (final.features || []) as Feature[]
    await this.analysisSummary(features, flow)
    return features
  }

  private async analyzePaths(
    paths: string[],
    entries: DownloadEntry[],
    outputDir: string,
    flow?: FlowProgress,
  ): Promise<Feature[]> {
    const final = await this.status("Analyzing", [], async (write) => {
      return await this.bridge.events(
        "analyze",
        [...paths, "--output-dir", outputDir, "--entries-json", JSON.stringify(entries)],
        undefined,
        (event) => {
          if (event.message) {
            write(String(event.message))
          }
        },
      )
    }, flow)
    const features = (final.features || []) as Feature[]
    await this.analysisSummary(features, flow)
    return features
  }

  private async runFeatureCommand(
    command: "write-tags" | "write-import-files",
    title: string,
    features: Feature[],
    extra: Record<string, unknown> = {},
    flow?: FlowProgress,
  ): Promise<void> {
    const final = await this.status(title, [], async (write) => {
      return await this.bridge.events(command, [], { features, ...extra }, (event) => {
        if (event.message) {
          write(String(event.message))
        }
      })
    }, flow)
    const lines = command === "write-import-files"
      ? [`M3U: ${final.m3u}`, `XML: ${final.xml}`]
      : [`Updated ${final.count} track(s).`]
    await this.message(title, lines, "Continue", flow)
  }

  private async pushFeatures(features: Feature[], defaultPlaylistName: string, outputDir: string): Promise<void> {
    const stepLabels = ["Rekordbox state", "Destination", "Playlist", "Confirm push", "Write database"]
    let step = 0
    let destination: "existing" | "create" | undefined
    let playlistName = defaultPlaylistName || this.config.rekordbox_playlist
    let playlistId = ""
    let createPlaylist = false
    let rekordboxReady = false
    const flow = () => ({
      name: "Rekordbox Push",
      step: step + 1,
      total: stepLabels.length,
      label: stepLabels[step],
    })

    while (step < stepLabels.length && this.running) {
      try {
        const current = stepLabels[step]
        if (current === "Rekordbox state") {
          const running = await this.bridge.json("rekordbox-running")
          if (running.running) {
            const action = await this.select("Rekordbox Is Open", [
              { label: "Close Rekordbox and continue", description: "Recommended for direct database writes", value: "close" },
              { label: "I closed it, check again", value: "check" },
              { label: "Generate import files instead", value: "files" },
            ], { flow: flow() })
            if (action === "files") {
              await this.runFeatureCommand("write-import-files", "Writing Import Files", features, {
                output_dir: outputDir,
                source_name: defaultPlaylistName,
              }, flow())
              return
            }
            if (action === "close") {
              await this.status("Closing Rekordbox", ["Asking Rekordbox to quit..."], async () => {
                return await this.bridge.json("close-rekordbox")
              }, flow())
            }
            const after = await this.bridge.json("rekordbox-running")
            if (after.running) {
              await this.message("Rekordbox Still Open", ["Close Rekordbox, then try the push again."], "Back", flow())
              return
            }
          }
          rekordboxReady = true
          step += 1
        } else if (current === "Destination") {
          if (!rekordboxReady) {
            step = 0
            continue
          }
          destination = await this.select("Rekordbox Playlist", [
            { label: "Use existing playlist", value: "existing" },
            { label: "Create new playlist", value: "create" },
          ], { flow: flow() })
          createPlaylist = destination === "create"
          playlistId = ""
          step += 1
        } else if (current === "Playlist") {
          if (destination === "existing") {
            const playlist = await this.pickPlaylist(playlistName, flow())
            playlistName = playlist.name
            playlistId = playlist.id
            createPlaylist = false
          } else {
            playlistName = await this.input("New Rekordbox Playlist", "Name", playlistName || this.config.rekordbox_playlist, {
              flow: flow(),
            })
            createPlaylist = true
            playlistId = ""
          }
          step += 1
        } else if (current === "Confirm push") {
          const confirmed = await this.confirm(
            "Push To Rekordbox",
            [`Push ${features.length} track(s) to`, playlistName],
            true,
            flow(),
          )
          if (!confirmed) {
            return
          }
          step += 1
        } else if (current === "Write database") {
          const final = await this.status("Pushing To Rekordbox", [], async (write) => {
            write("Writing collection, playlist rows, metadata, cues, and loops...")
            return await this.bridge.events(
              "push",
              [],
              { features, playlist_name: playlistName, playlist_id: playlistId, create_playlist: createPlaylist },
              (event) => {
                if (event.message) {
                  write(String(event.message))
                }
              },
            )
          }, flow())
          await this.message("Rekordbox Push Complete", [
            `Playlist: ${final.playlist_name}`,
            `Added to playlist: ${final.added_to_playlist}`,
            `Already in playlist: ${final.already_in_playlist}`,
            `Cues added: ${final.added_cues}`,
            `Loops added: ${final.added_loops}`,
            `Backup: ${final.backup_dir}`,
          ], "Done", flow(), false)
          return
        }
      } catch (error) {
        if (error instanceof Back) {
          if (step === 0) {
            throw error
          }
          step -= 1
          continue
        }
        throw error
      }
    }
  }

  private async pickPlaylist(defaultName: string, flow?: FlowProgress): Promise<Playlist> {
    const raw = await this.bridge.json("list-playlists")
    const playlists = ((raw.playlists || []) as Playlist[]).filter((playlist) => !playlist.is_folder)
    const search = (await this.input("Search Playlists", "Search", defaultName, { flow })).trim().toLowerCase()
    const matches = (search ? playlists.filter((playlist) => playlist.path.toLowerCase().includes(search)) : playlists).slice(0, 40)
    if (!matches.length) {
      throw new Error("No matching Rekordbox playlists found.")
    }
    return await this.select("Select Playlist", matches.map((playlist) => ({
      label: playlist.path,
      description: `${playlist.song_count} track(s)`,
      value: playlist,
    })), { flow })
  }

  private async doctorFlow(): Promise<void> {
    const raw = await this.bridge.json("doctor", ["--output-dir", this.config.output_dir])
    const data = raw.doctor as Record<string, unknown>
    await this.message("Rekordbox Doctor", [
      `Rekordbox running: ${data.rekordbox_running ? "yes" : "no"}`,
      `Collection tracks: ${data.collection_tracks}`,
      `Local tracks: ${data.local_tracks}`,
      `Missing files: ${(data.missing_files as unknown[]).length}`,
      `Downloaded not imported: ${(data.unimported_output_files as unknown[]).length}`,
    ], "Back")
  }

  private async settingsFlow(): Promise<void> {
    const soundcloudUsername = await this.input("Settings", "SoundCloud username", this.config.soundcloud_username)
    const outputDir = resolveDownloadPath(await this.input("Settings", "Default output folder", this.config.output_dir), this.config.output_dir)
    const rekordboxPlaylist = await this.input("Settings", "Default Rekordbox playlist", this.config.rekordbox_playlist)
    const analyzeAfterDownload = await this.confirm("Settings", ["Analyze after download by default?"], this.config.analyze_after_download)
    const writeTags = await this.confirm("Settings", ["Write tags by default?"], this.config.write_tags)
    const createImportFiles = await this.confirm("Settings", ["Generate import files by default?"], this.config.create_import_files)
    const directRekordboxPush = await this.confirm("Settings", ["Offer direct Rekordbox push by default?"], this.config.direct_rekordbox_push)

    const raw = await this.bridge.json("save-config", [], {
      soundcloud_username: soundcloudUsername.trim() || this.config.soundcloud_username,
      output_dir: outputDir,
      rekordbox_playlist: rekordboxPlaylist.trim() || this.config.rekordbox_playlist,
      analyze_after_download: analyzeAfterDownload,
      write_tags: writeTags,
      create_import_files: createImportFiles,
      direct_rekordbox_push: directRekordboxPush,
    })
    this.config = raw.config as Config
    await this.message("Settings Saved", [this.config.config_path], "Back")
  }

  private async confirmPlan(plan: DownloadPlan, sourceName: string, flow?: FlowProgress): Promise<void> {
    const preview = plan.entries.slice(0, 8).map((entry, index) => `${index + 1}. ${entry.label}`)
    if (plan.entries.length > preview.length) {
      preview.push(`... ${plan.entries.length - preview.length} more`)
    }
    await this.message(`Found ${plan.count} Track(s)`, [`Source: ${sourceName}`, ...preview], "Continue", flow)
  }

  private async analysisSummary(features: Feature[], flow?: FlowProgress): Promise<void> {
    const preview = features.slice(0, 8).map((item) => {
      const artist = item.artist ? `${item.artist} - ` : ""
      const key = item.camelot_key || item.musical_key || "key?"
      return `${artist}${item.title} | ${Number(item.bpm || 0).toFixed(1)} BPM | ${key} | E${item.energy}`
    })
    if (features.length > preview.length) {
      preview.push(`... ${features.length - preview.length} more`)
    }
    await this.message("Analysis Complete", preview.length ? preview : ["No tracks analyzed."], "Continue", flow)
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
      content: `Output: ${this.config.output_dir}    Rekordbox: ${this.config.rekordbox_playlist}`,
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

function splitUrls(value: string): string[] {
  return value.split(/\s+/).map((item) => item.trim()).filter(Boolean)
}

function suggestedOutputFolder(current: string, sourceName: string): string {
  if (!sourceName || sourceName === "SoundCloud Download" || sourceName === "SoundCloud Likes") {
    return current
  }
  return join(HOME, "Downloads", sourceName.replace(/[^\w .-]+/g, "-").trim() || basename(current))
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
