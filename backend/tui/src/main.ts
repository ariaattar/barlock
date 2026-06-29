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
        const choice = await this.select("SoundCloud → Rekordbox Sync", [
          {
            label: "Sync a SoundCloud URL",
            description: "Set, playlist, or likes link → Rekordbox playlist",
            value: "sync",
          },
          {
            label: `Sync likes for @${this.config.soundcloud_username}`,
            description: this.config.likes_url,
            value: "likes",
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
        } else if (choice === "sync") {
          await this.syncFlow()
        } else if (choice === "likes") {
          await this.syncFlow(this.config.likes_url)
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
      content: this.subtitle || `@${this.config.soundcloud_username} likes: ${this.config.likes_url}`,
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
