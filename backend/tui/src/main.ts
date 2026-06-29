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
    const urls = prefilledUrls ?? splitUrls(
      await this.input("SoundCloud URL(s)", "Paste SoundCloud URL(s)", "", {
        placeholder: "https://soundcloud.com/artist/track",
      }),
    )
    if (!urls.length) {
      return
    }

    const plan = await this.status("Expanding SoundCloud", ["Reading playlist metadata..."], async (write) => {
      const plan = (await this.bridge.json("plan", urls)) as unknown as DownloadPlan & { ok: boolean }
      write(`Found ${plan.count} track(s).`)
      return plan
    })
    const sourceName = plan.title || fallbackName
    await this.confirmPlan(plan, sourceName)

    const outputInput = await this.input(
      "Output Folder",
      "Folder",
      suggestedOutputFolder(this.config.output_dir, sourceName),
      { placeholder: "set-2" },
    )
    const outputDir = resolveDownloadPath(outputInput, this.config.output_dir)
    const workers = await this.numberInput("Parallel Track Downloads", this.config.workers)
    const fragments = await this.numberInput("Parallel Fragments Per Track", this.config.fragments)
    const quality = await this.input("MP3 Bitrate", "Bitrate", this.config.quality, { placeholder: "320" })

    const confirmed = await this.confirm(
      "Download",
      [`Download ${plan.count} track(s) to`, outputDir],
      true,
    )
    if (!confirmed) {
      return
    }

    const final = await this.status("Downloading", [], async (write) => {
      return await this.bridge.events(
        "download",
        [
          ...urls,
          "--output-dir",
          outputDir,
          "--workers",
          String(workers),
          "--fragments",
          String(fragments),
          "--quality",
          quality,
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
    })

    const paths = stringArray(final.paths)
    if (!paths.length) {
      await this.message("Download Complete", ["No local MP3 paths were found after download."], "Back")
      return
    }

    const features = await this.afterDownloadPrompts(paths, final.entries as DownloadEntry[], outputDir, sourceName)
    if (features.length && await this.confirm("Rekordbox", ["Push directly to a Rekordbox playlist?"], this.config.direct_rekordbox_push)) {
      await this.pushFeatures(features, sourceName, outputDir)
    }
  }

  private async afterDownloadPrompts(
    paths: string[],
    entries: DownloadEntry[],
    outputDir: string,
    sourceName: string,
  ): Promise<Feature[]> {
    let features: Feature[] = []
    if (await this.confirm("Analyze", ["Analyze BPM/key/energy and cue hints now?"], this.config.analyze_after_download)) {
      features = await this.analyzePaths(paths, entries, outputDir)
    }
    if (!features.length) {
      return []
    }
    if (await this.confirm("Tags", ["Write BPM/key/title ID3 tags to MP3 files?"], this.config.write_tags)) {
      await this.runFeatureCommand("write-tags", "Writing Tags", features)
    }
    if (await this.confirm("Import Files", ["Generate Rekordbox M3U/XML import files?"], this.config.create_import_files)) {
      await this.runFeatureCommand("write-import-files", "Writing Import Files", features, { output_dir: outputDir, source_name: sourceName })
    }
    return features
  }

  private async analyzeFolderFlow(pushAfter: boolean): Promise<void> {
    const folderInput = await this.input("Analyze Folder", "Folder", this.config.output_dir, { placeholder: "set-2" })
    const folder = resolveDownloadPath(folderInput, this.config.output_dir)
    const features = await this.analyzeFolder(folder)
    if (!features.length) {
      return
    }
    if (!pushAfter && await this.confirm("Tags", ["Write BPM/key/title ID3 tags to MP3 files?"], this.config.write_tags)) {
      await this.runFeatureCommand("write-tags", "Writing Tags", features)
    }
    if (!pushAfter && await this.confirm("Import Files", ["Generate Rekordbox M3U/XML import files?"], this.config.create_import_files)) {
      await this.runFeatureCommand("write-import-files", "Writing Import Files", features, {
        output_dir: folder,
        source_name: basename(folder),
      })
    }
    if (pushAfter || await this.confirm("Rekordbox", ["Push these tracks to a Rekordbox playlist?"], false)) {
      await this.pushFeatures(features, basename(folder), folder)
    }
  }

  private async analyzeFolder(folder: string): Promise<Feature[]> {
    const final = await this.status("Analyzing", [], async (write) => {
      return await this.bridge.events("analyze", ["--folder", folder, "--output-dir", folder], undefined, (event) => {
        if (event.message) {
          write(String(event.message))
        }
      })
    })
    const features = (final.features || []) as Feature[]
    await this.analysisSummary(features)
    return features
  }

  private async analyzePaths(paths: string[], entries: DownloadEntry[], outputDir: string): Promise<Feature[]> {
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
    })
    const features = (final.features || []) as Feature[]
    await this.analysisSummary(features)
    return features
  }

  private async runFeatureCommand(
    command: "write-tags" | "write-import-files",
    title: string,
    features: Feature[],
    extra: Record<string, unknown> = {},
  ): Promise<void> {
    const final = await this.status(title, [], async (write) => {
      return await this.bridge.events(command, [], { features, ...extra }, (event) => {
        if (event.message) {
          write(String(event.message))
        }
      })
    })
    const lines = command === "write-import-files"
      ? [`M3U: ${final.m3u}`, `XML: ${final.xml}`]
      : [`Updated ${final.count} track(s).`]
    await this.message(title, lines, "Back")
  }

  private async pushFeatures(features: Feature[], defaultPlaylistName: string, outputDir: string): Promise<void> {
    const running = await this.bridge.json("rekordbox-running")
    if (running.running) {
      const action = await this.select("Rekordbox Is Open", [
        { label: "Close Rekordbox and continue", description: "Recommended for direct database writes", value: "close" },
        { label: "I closed it, check again", value: "check" },
        { label: "Generate import files instead", value: "files" },
        { label: "Back", value: "back" },
      ])
      if (action === "back") {
        return
      }
      if (action === "files") {
        await this.runFeatureCommand("write-import-files", "Writing Import Files", features, {
          output_dir: outputDir,
          source_name: defaultPlaylistName,
        })
        return
      }
      if (action === "close") {
        await this.status("Closing Rekordbox", ["Asking Rekordbox to quit..."], async () => {
          return await this.bridge.json("close-rekordbox")
        })
      }
      const after = await this.bridge.json("rekordbox-running")
      if (after.running) {
        await this.message("Rekordbox Still Open", ["Close Rekordbox, then try the push again."], "Back")
        return
      }
    }

    const destination = await this.select("Rekordbox Playlist", [
      { label: "Use existing playlist", value: "existing" },
      { label: "Create new playlist", value: "create" },
      { label: "Back", value: "back" },
    ])
    if (destination === "back") {
      return
    }

    let playlistName = defaultPlaylistName || this.config.rekordbox_playlist
    let playlistId = ""
    let createPlaylist = destination === "create"

    if (destination === "existing") {
      const playlist = await this.pickPlaylist(playlistName)
      playlistName = playlist.name
      playlistId = playlist.id
      createPlaylist = false
    } else {
      playlistName = await this.input("New Rekordbox Playlist", "Name", playlistName || this.config.rekordbox_playlist)
    }

    if (!await this.confirm("Push To Rekordbox", [`Push ${features.length} track(s) to`, playlistName], true)) {
      return
    }

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
    })
    await this.message("Rekordbox Push Complete", [
      `Playlist: ${final.playlist_name}`,
      `Added to playlist: ${final.added_to_playlist}`,
      `Already in playlist: ${final.already_in_playlist}`,
      `Cues added: ${final.added_cues}`,
      `Loops added: ${final.added_loops}`,
      `Backup: ${final.backup_dir}`,
    ], "Back")
  }

  private async pickPlaylist(defaultName: string): Promise<Playlist> {
    const raw = await this.bridge.json("list-playlists")
    const playlists = ((raw.playlists || []) as Playlist[]).filter((playlist) => !playlist.is_folder)
    const search = (await this.input("Search Playlists", "Search", defaultName)).trim().toLowerCase()
    const matches = (search ? playlists.filter((playlist) => playlist.path.toLowerCase().includes(search)) : playlists).slice(0, 40)
    if (!matches.length) {
      throw new Error("No matching Rekordbox playlists found.")
    }
    return await this.select("Select Playlist", matches.map((playlist) => ({
      label: playlist.path,
      description: `${playlist.song_count} track(s)`,
      value: playlist,
    })))
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
    const workers = await this.numberInput("Parallel Track Downloads", this.config.workers)
    const fragments = await this.numberInput("Parallel Fragments Per Track", this.config.fragments)
    const quality = await this.input("Settings", "MP3 bitrate", this.config.quality)
    const rekordboxPlaylist = await this.input("Settings", "Default Rekordbox playlist", this.config.rekordbox_playlist)
    const analyzeAfterDownload = await this.confirm("Settings", ["Analyze after download by default?"], this.config.analyze_after_download)
    const writeTags = await this.confirm("Settings", ["Write tags by default?"], this.config.write_tags)
    const createImportFiles = await this.confirm("Settings", ["Generate import files by default?"], this.config.create_import_files)
    const directRekordboxPush = await this.confirm("Settings", ["Offer direct Rekordbox push by default?"], this.config.direct_rekordbox_push)

    const raw = await this.bridge.json("save-config", [], {
      soundcloud_username: soundcloudUsername.trim() || this.config.soundcloud_username,
      output_dir: outputDir,
      workers,
      fragments,
      quality,
      rekordbox_playlist: rekordboxPlaylist.trim() || this.config.rekordbox_playlist,
      analyze_after_download: analyzeAfterDownload,
      write_tags: writeTags,
      create_import_files: createImportFiles,
      direct_rekordbox_push: directRekordboxPush,
    })
    this.config = raw.config as Config
    await this.message("Settings Saved", [this.config.config_path], "Back")
  }

  private async confirmPlan(plan: DownloadPlan, sourceName: string): Promise<void> {
    const preview = plan.entries.slice(0, 8).map((entry, index) => `${index + 1}. ${entry.label}`)
    if (plan.entries.length > preview.length) {
      preview.push(`... ${plan.entries.length - preview.length} more`)
    }
    await this.message(`Found ${plan.count} Track(s)`, [`Source: ${sourceName}`, ...preview], "Continue")
  }

  private async analysisSummary(features: Feature[]): Promise<void> {
    const preview = features.slice(0, 8).map((item) => {
      const artist = item.artist ? `${item.artist} - ` : ""
      const key = item.camelot_key || item.musical_key || "key?"
      return `${artist}${item.title} | ${Number(item.bpm || 0).toFixed(1)} BPM | ${key} | E${item.energy}`
    })
    if (features.length > preview.length) {
      preview.push(`... ${features.length - preview.length} more`)
    }
    await this.message("Analysis Complete", preview.length ? preview : ["No tracks analyzed."], "Continue")
  }

  private async numberInput(title: string, defaultValue: number): Promise<number> {
    while (true) {
      const value = await this.input(title, "Value", String(defaultValue))
      const parsed = Number.parseInt(value, 10)
      if (Number.isFinite(parsed) && parsed > 0) {
        return parsed
      }
      await this.message(title, ["Enter a positive number."], "Back")
    }
  }

  private async confirm(title: string, lines: string[], defaultValue: boolean): Promise<boolean> {
    const prefix = lines.length ? lines.join("\n") + "\n\n" : ""
    return await this.select(title, [
      { label: "Yes", description: defaultValue ? "Default" : "", value: true },
      { label: "No", description: !defaultValue ? "Default" : "", value: false },
    ], { intro: prefix, defaultIndex: defaultValue ? 0 : 1 })
  }

  private async message(title: string, lines: string[], actionLabel: string): Promise<void> {
    await this.select(title, [{ label: actionLabel, value: "ok" }], {
      intro: lines.join("\n"),
      allowBack: true,
    })
  }

  private async status<T>(title: string, initial: string[], run: (write: (line: string) => void) => Promise<T>): Promise<T> {
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
    }, "Working...")
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
    opts: { placeholder?: string } = {},
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
      }, "Enter to continue | Esc/Left to go back")
      input.focus()
    })
  }

  private select<T>(
    title: string,
    items: SelectItem<T>[],
    opts: { intro?: string; allowBack?: boolean; allowQuit?: boolean; defaultIndex?: number } = {},
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
      }, "Up/Down select | Enter choose | Esc/B/Left back | q quit")
      menu.focus()
    })
  }

  private renderShell(title: string, build: (panel: BoxRenderable) => void, footer: string): void {
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
      height: 5,
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
