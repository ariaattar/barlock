# SoundCloud DL Desktop Product Plan

Status: proposed

Scope: macOS-first desktop application for downloading SoundCloud music, preparing it for DJ use, and safely syncing it into Rekordbox. The product ships as one self-contained application bundle with no separately installed runtime, worker, CLI, or media dependency.

Working product name: **SoundCloud DL Desktop**. Naming and icon design should be resolved before packaging, but neither should block product work.

## 1. Product Thesis

The desktop app should turn a SoundCloud link into a clean, reviewable Rekordbox playlist with the least possible ceremony.

The core interaction is not a dashboard and not a multi-page setup wizard. It is an operational workspace:

1. Paste a SoundCloud track, playlist, or likes URL.
2. Review what is new and where it will go.
3. Start the job.
4. Watch useful progress without babysitting it.
5. Resolve only the exceptions that require judgment.
6. Open the finished playlist in Rekordbox and export it to USB normally.

The product should feel local, fast, and trustworthy. It should never make a user wonder whether it is downloading, analyzing, blocked by Rekordbox, overwriting cues, or writing to the wrong playlist.

### North-star outcome

From paste to a usable Rekordbox playlist in one uninterrupted session, with:

- Delta-only downloads on repeat runs.
- Clear partial-failure handling for DRM or removed tracks.
- Accurate metadata, key, BPM, and analysis status.
- Existing Rekordbox cues preserved.
- Optional cue generation limited to empty hot-cue slots.
- A backup and visible audit trail for every direct database mutation.

## 2. Product Principles

### 2.1 Local-first

Audio, analysis, credentials, Rekordbox data, and job history stay on the machine. No account or cloud service is required for the core workflow.

### 2.2 Fast shell, deferred work

The window must become usable before Python, Rekordbox inspection, or SoundCloud resolution finishes. Expensive work starts after first paint and reports progressive state.

### 2.3 One obvious primary action

Every screen gets one clear next action. Secondary operations live in context menus, the inspector, or the command palette. The interface should not become a grid of equally prominent buttons.

### 2.4 Review before mutation

Downloads may begin after source confirmation. Rekordbox writes require a final review that names the target playlist, track count, cue policy, and backup behavior.

### 2.5 Preserve user work

Hot-cue writing is off by default. The only normal write mode is **Fill empty slots**, which never deletes, moves, renames, or overwrites an occupied slot. Generated cue removal is a separate explicit action and only removes cues with verified app provenance.

### 2.6 Errors are rows, not dead ends

A four-track DRM failure in a 100-track playlist should produce 96 completed rows and four actionable failures. It should not replace the whole workspace with a traceback.

### 2.7 Keyboard complete

Every primary workflow must work with arrow keys, Enter, Escape, and standard macOS shortcuts. Back stays local to the current panel or step.

### 2.8 Quiet visual system

Use a Vercel-inspired neutral system: precise typography, near-black and white surfaces, fine separators, restrained radii, and sparse semantic color. Avoid decorative gradients, oversized headings, floating section cards, and animation without meaning.

### 2.9 One bundled product

The release is one signed and notarized macOS app. Python, yt-dlp, ffmpeg, native audio libraries, analysis code, fonts, and any internal helper executable travel inside that app and update as one versioned unit. Nothing is installed globally and nothing essential is fetched on first launch.

## 3. Non-goals For The First Desktop Release

- Replacing Rekordbox as a library manager.
- Writing a Pioneer device library directly to USB.
- SoundCloud social browsing or discovery.
- Real-time DJ mixing, effects, pitch shifting, or two-deck playback.
- Automatically replacing manual hot cues.
- Automatically accepting low-confidence loops.
- Windows support before the macOS pipeline and packaging are stable.
- Requiring a hosted backend, login, or subscription system.
- Asking users to install Python, `uv`, Bun, Go, ffmpeg, yt-dlp, or a companion service.

## 4. Primary Users And Jobs

### Working DJ preparing a set

Needs to pull a public SoundCloud playlist, download only additions, inspect failures, analyze the files, and append them to the matching Rekordbox playlist.

### DJ maintaining a likes inbox

Needs to sync likes repeatedly, see only new tracks, and route selected tracks into one or more preparation playlists.

### DJ correcting automation

Needs to inspect key, BPM, waveform, beat grid, generated cue suggestions, and loop confidence before writing anything into empty Rekordbox slots.

### DJ preparing an XDJ USB

Needs a clear readiness check showing that files exist, analysis is complete, Rekordbox contains the playlist, and the next step is Rekordbox Export mode rather than copying files manually.

## 5. Information Architecture

Use a stable desktop shell with a compact sidebar, a central work surface, and an optional inspector. Do not use route-sized modal wizards.

```text
+----------------------+--------------------------------------------+------------------------+
| Sidebar              | Main workspace                             | Inspector              |
|                      |                                            |                        |
| New import           | Context toolbar                            | Selected source/track  |
| Activity             | ------------------------------------------ | metadata               |
| Downloads            | Track/source/job table                     | waveform               |
| Rekordbox             |                                            | analysis               |
|   Playlists           | Inline progress and exception rows        | cue policy/preview     |
|   Doctor              |                                            | actions                |
|                      |                                            |                        |
| Settings             |                                            |                        |
+----------------------+--------------------------------------------+------------------------+
| Status: worker ready | Rekordbox closed | USB detected | 42 GB free                         |
+----------------------------------------------------------------------------------------+
```

### Sidebar

| Destination | Purpose |
| --- | --- |
| New import | Paste a URL and start the primary workflow. |
| Activity | Active, paused, failed, and recently completed jobs. |
| Downloads | Local tracks grouped by SoundCloud source and folder. |
| Rekordbox Playlists | Cached playlist view, sync readiness, and playlist-scoped operations. |
| Rekordbox Doctor | Backups, active-loop repair, grid alignment, and diagnostics. |
| Settings | Defaults, storage, analysis, appearance, and diagnostics. |

### Global title bar

- Back and forward controls with standard macOS placement and behavior.
- Current view title and compact breadcrumb when needed.
- Command palette trigger.
- Rekordbox status button: `Open`, `Closed`, or `Needs attention`.
- Activity button with active job count and aggregate progress.

### Inspector behavior

- Opens for a selected source, track, failure, playlist, or backup.
- Resizable between 320 and 520 px.
- Remembers width.
- Collapses with `Cmd+Option+I`.
- Never blocks table navigation.
- Uses sections and separators, not nested cards.

## 6. Core Workflow: New Import

The import is one persistent job with stages, not a sequence of disconnected screens.

### Stage 1: Source

The first screen is the actual tool. The dominant control is a URL field with paste detection.

```text
Import from SoundCloud

[ Paste a track, playlist, or likes URL                              ] [Resolve]

Recent sources
Set 20                 15 tracks       Synced 2h ago       Up to date
ariaattar Likes       284 tracks       Synced yesterday   12 new
```

Behavior:

- `Cmd+V` with a SoundCloud URL focuses and fills the source field from anywhere in the app.
- Resolve begins automatically after a valid pasted URL, with a 250 ms debounce.
- The source title, owner, artwork, source type, track count, and availability summary fill in progressively.
- Multiple pasted URLs become one batch with separate destination rows.
- A playlist title becomes the default folder and Rekordbox playlist name.
- A single track defaults to `~/Downloads`, with an optional subfolder.

### Stage 2: Plan

Show the resolved delta before downloading:

| Group | Example state |
| --- | --- |
| New | 12 tracks will download and analyze. |
| Existing | 31 tracks already exist locally and will not download again. |
| Unavailable | 3 DRM-protected or removed tracks will be skipped and reported. |
| Rekordbox | 6 local tracks are already in the target playlist. |

Primary settings remain visible and simple:

- Download folder.
- Rekordbox destination: create, choose existing, or no Rekordbox sync.
- Analyze and tag: on by default.
- Hot cues: `Off` by default or `Fill empty slots`.

Expert download concurrency, fragment count, bitrate, and ffmpeg path remain in Settings. They do not appear in the normal import flow.

### Stage 3: Run

The workspace switches into a live table while keeping the job controls in place.

| Column | Content |
| --- | --- |
| Track | Artwork thumbnail, title, artist. |
| Stage | Queued, resolving, downloading, converting, analyzing, tagging, ready, syncing. |
| Progress | Stable-width progress bar and percentage. |
| BPM | Result when available. |
| Key | Alphanumeric key when available, Camelot in secondary text. |
| Cues | Off, proposed, filled, skipped, or needs review. |
| Status | Success, warning, retryable failure, or blocked. |

Job header:

- Overall stage and `completed / total` count.
- Aggregate progress weighted by actual stages, not a fake timer.
- Elapsed time and a smoothed ETA after enough data exists.
- Pause new work, cancel queued work, or continue in background.
- Closing the window does not silently kill a job. Offer `Keep running`, `Pause and quit`, or `Cancel job`.

### Stage 4: Rekordbox handoff

If Rekordbox is open when a write is needed, the job moves to a visible blocked state instead of skipping the step.

Prompt:

```text
Rekordbox must close before this playlist can be updated.

The download and analysis are complete. No Rekordbox changes have been made yet.

[Save and close Rekordbox]  [Check again]  [Finish later]
```

`Save and close Rekordbox` sends a normal quit request. It never force-kills Rekordbox. If unsaved work blocks quitting, the user resolves that in Rekordbox and returns.

Before the write, show a compact transaction review:

- Playlist name and whether it will be created.
- Tracks added and tracks already present.
- Metadata updates.
- Cue policy and number of eligible empty slots.
- Backup destination.

### Stage 5: Complete

Completion stays useful rather than celebratory:

```text
Set 20 is ready in Rekordbox

12 downloaded   12 analyzed   10 added   2 already present   3 unavailable

[Open playlist in Rekordbox]  [View failures]  [Start another import]
```

If grid-dependent loop alignment remains, completion is `Needs finalization`, with one explicit next action. It is not shown as complete.

## 7. Likes Workflow

Likes is a saved source, not a separate product mode.

- First use asks for a SoundCloud username and resolves the public likes source.
- The source appears under Recent Sources and can be pinned to the sidebar.
- `Sync now` computes deltas before starting work.
- A filter allows `New since last sync`, `Not in Rekordbox`, and `All available`.
- Tracks can be routed to a selected Rekordbox playlist before running.
- The job uses the same import table, failure behavior, and cue policy as every other source.

Future consideration: official SoundCloud authentication for private likes should remain outside the first release until API access and token storage are designed properly.

## 8. Downloads And Library

The Downloads view is a local operational library, not a second Rekordbox.

### Default presentation

- Sources in the left sub-navigation.
- Virtualized track table in the center.
- Track inspector on selection.
- Search across title, artist, source URL, folder, BPM, and key.
- Filters for analyzed, failed, missing file, not in Rekordbox, and cue review.

### Bulk actions

- Analyze and tag.
- Add to Rekordbox playlist.
- Reveal in Finder.
- Retry download.
- Remove local cache entry.
- Delete audio only after explicit confirmation.

Bulk action bars appear only while rows are selected. They should not permanently consume vertical space.

## 9. Track Inspector

The inspector is where the desktop app becomes materially better than the CLI.

### Header

- Artwork, title, artist, duration, source link, and local path.
- Playback controls with spacebar support.
- Reveal in Finder and open SoundCloud actions use icons with tooltips.

### Waveform

- Full-width waveform with stable height and precomputed peak data.
- Rekordbox beat-grid lines, downbeats, phrase boundaries, hot cues, and loops.
- Zoom and horizontal scroll without shifting surrounding layout.
- Cue labels sit in separate lanes to prevent overlap.
- Colors match Rekordbox pad semantics but also use labels and shapes for accessibility.
- Loop regions show exact beat count, confidence, and grid alignment.

### Rekordbox-accurate waveform contract

For tracks already analyzed by Rekordbox, the inspector must render Rekordbox's own analysis data rather than a separately generated visual approximation.

- Read the track's `AnalysisDataPath` through `Rekordbox6Database.get_anlz_paths()` or `read_anlz_files()`.
- Use the `PQTZ` beat-grid tag as the authoritative list of beat numbers, BPM values, and millisecond timestamps.
- Prefer the `PWV5` color-detail waveform for the zoomed waveform.
- Prefer `PWV4` for the full-track color preview.
- Fall back to `PWV3` detail and `PWAV` preview when color tags are unavailable.
- Render Rekordbox cues and loops against those exact ANLZ timestamps so waveform markers, grid lines, and hardware exports share one time coordinate system.
- Include `source: "rekordbox_anlz"`, ANLZ file modification times, and a stable analysis fingerprint in the waveform response so stale cached views are invalidated.
- Never silently label a locally computed waveform as Rekordbox-accurate.

Before Rekordbox has analyzed a newly imported track, show a clearly labeled `Local preview` generated from decoded audio. Once Rekordbox creates the ANLZ files, refresh the inspector automatically and replace it with a `Rekordbox analysis` view. Cue and loop writes that require exact device-grid alignment remain blocked until the authoritative `PQTZ` grid is available.

Rekordbox ANLZ files contain the beat grid, waveform previews/details, cues, and seek information used by Rekordbox and exported devices. The implementation is based on the documented [pyrekordbox analysis-file API](https://pyrekordbox.readthedocs.io/en/stable/tutorial/anlz.html) and [ANLZ tag formats](https://pyrekordbox.readthedocs.io/en/stable/formats/anlz.html).

### Analysis

- BPM and alternate tempo.
- Alphanumeric key as primary, Camelot key as secondary.
- Energy and loudness.
- Beat-grid confidence and phrase mode.
- Clear warning when a result is heuristic or low confidence.

### Cue preview

The app separates **suggestions** from **Rekordbox cues**.

| Layer | Meaning |
| --- | --- |
| Existing | Current Rekordbox hot cues; always read-only in the normal flow. |
| Suggested | Analysis output that has not been written. |
| Eligible | Suggested cue targets an empty slot. |
| Conflict | Suggested slot is occupied and will be skipped. |
| Written | Cue was added by this app and carries provenance. |

Actions:

- `Fill empty slots` writes only eligible suggestions.
- Per-cue toggles allow excluding a suggestion before writing.
- Dragging a suggestion snaps to the beat grid and never moves an existing Rekordbox cue.
- Low-confidence loops start disabled and require explicit selection.
- `Remove generated cues` is separate, destructive, and unavailable if no app-generated cues exist.

### Cue policy contract

The desktop app must enforce these rules in both UI and backend:

1. Default mode is `Off`.
2. `Fill empty slots` checks occupancy at transaction time, not only at preview time.
3. An occupied slot is skipped regardless of who created it.
4. The app never infers ownership from cue name alone. Persist and verify provenance where Rekordbox fields permit it.
5. Generated cue removal only affects selected managed tracks and creates a backup first.
6. Playlist-scoped removal explains that cues belong to tracks and therefore change everywhere that track appears.

## 10. Rekordbox Workspace

### Playlist view

- Searchable playlist tree with folders.
- Track count, local file health, analysis status, and last app sync.
- Cached state remains visible while Rekordbox is open.
- Refresh uses a short-lived database session and never holds the Rekordbox database open in the background.

Playlist actions:

- Sync source delta.
- Add downloaded tracks.
- Reanalyze selected tracks.
- Fill empty hot-cue slots.
- Remove generated hot cues.
- Reveal missing files.
- Open in Rekordbox.

### Doctor

Doctor becomes a focused diagnostic table rather than a menu of scripts.

| Check | Result | Repair |
| --- | --- | --- |
| Rekordbox process | Open/closed | Request normal close. |
| Database backup | Last backup and size | Create backup. |
| Active generated loops | Count and affected tracks | Disable generated active loops. |
| Off-grid generated loops | Count and offset | Align to current Rekordbox grid. |
| Missing local files | Count | Locate or remove stale app mapping. |
| Incomplete imports | Jobs and blocked stage | Resume. |
| USB | Device and free space | Show export readiness. |

Each repair first shows the exact affected tracks and backup path. Repairs are independently runnable and idempotent.

### Backup history

- Timestamp, Rekordbox version if detectable, database size, operation, and affected track count.
- `Reveal in Finder` is always available.
- Restore is an advanced flow and only runs while Rekordbox is closed.
- Restore itself creates a pre-restore backup.

## 11. USB And XDJ Readiness

USB detection belongs in the status layer, but direct Pioneer database writing does not belong in the first release.

When a removable drive appears:

- Show drive name, filesystem, free space, and whether a Pioneer library is detected.
- Show which prepared Rekordbox playlists are ready for export.
- Offer `Open Rekordbox Export mode` and a concise checklist.
- Never imply that copying MP3 files in Finder preserves cues, beat grids, waveforms, or playlists.
- Detect unsupported filesystem or insufficient space before the user begins export.

A later direct-device export feature requires a separately researched, fixture-tested implementation and must not reuse direct `master.db` assumptions.

## 12. Visual Direction

The visual target is Vercel-inspired, not a clone: dense, crisp, restrained, and optimized for repeated use. Vercel describes Geist around simplicity, minimalism, speed, precision, and clarity; bundle Geist locally so the desktop app does not depend on a font network request. See the official [Geist font](https://vercel.com/font) and [Geist design system](https://vercel.com/geist/introduction).

### Palette

Avoid a one-note dark theme. The foundation is neutral; functional colors are reserved for audio and state.

| Token | Dark | Light | Use |
| --- | --- | --- | --- |
| `canvas` | `#0A0A0A` | `#FAFAFA` | Window background. |
| `surface-1` | `#111111` | `#FFFFFF` | Main panels and controls. |
| `surface-2` | `#171717` | `#F4F4F5` | Hover, selected rows, secondary controls. |
| `separator` | `rgba(255,255,255,.10)` | `rgba(0,0,0,.10)` | Fine structure. |
| `text-primary` | `#EDEDED` | `#171717` | Primary text. |
| `text-secondary` | `#A1A1AA` | `#606068` | Metadata. |
| `focus` | `#52A8FF` | `#006FEE` | Focus ring and selection. |
| `success` | `#3ECF8E` | `#137A50` | Completed and healthy. |
| `warning` | `#F5A524` | `#9A5B00` | Partial and attention. |
| `danger` | `#F31260` | `#B4234D` | Failures and destructive actions. |
| `waveform` | `#D4D4D8` | `#52525B` | Base waveform. |
| `waveform-played` | `#52A8FF` | `#006FEE` | Playback position. |

The five hot-cue colors remain recognizable, but they do not become page accents.

### Typography

- Geist Sans for interface text.
- Geist Mono for BPM, key, duration, percentages, paths, and log excerpts.
- Type scale: 12, 13, 14, 16, 20, 24, 32 px.
- Most desktop body text is 13 or 14 px; 12 px is reserved for secondary metadata.
- Use tabular figures for every changing number.
- Letter spacing stays at `0`.
- Headings use balanced wrapping and body copy uses pretty wrapping.

### Density and geometry

- Base spacing unit: 4 px.
- Main row height: 44 px compact, 52 px comfortable.
- Icon button hit area: at least 40 by 40 px.
- Standard control radius: 6 px.
- Popover/dialog radius: 8 px maximum.
- Avoid rounded pill controls except status filters with established segmented-control behavior.
- Use full-height panels and separators, not floating cards within cards.

### Icons

- Use Lucide consistently.
- Icon-only buttons require tooltips and accessibility labels.
- Use familiar symbols for play, pause, reveal, retry, search, filter, settings, undo, and close.
- Never use emoji as interface icons.

### Motion

- State transitions: 120 to 180 ms.
- Panel and popover entry: up to 220 ms.
- Animate only transform and opacity.
- Progress is continuous but respects reduced motion.
- Press feedback uses `scale(0.96)` where it does not affect table layout.
- Job rows do not animate their height as stages change.
- All animations are interruptible and no animation blocks input.

## 13. Interaction Model

### Keyboard shortcuts

| Shortcut | Action |
| --- | --- |
| `Cmd+N` | New import. |
| `Cmd+V` | Paste and resolve a SoundCloud URL when no text field is active. |
| `Cmd+K` | Open command palette. |
| `Cmd+F` | Search or filter the active table. |
| `Cmd+,` | Settings. |
| `Cmd+Option+I` | Toggle inspector. |
| `Space` | Play/pause selected track when focus is not in a text control. |
| `Enter` | Open selection or activate primary row action. |
| `Escape` | Close the topmost transient surface or go back one local step. |
| `Up/Down` | Move selection without changing scroll geometry. |
| `Shift+Up/Down` | Extend table selection. |

### Command palette

The palette searches actions and entities:

- Start import.
- Sync likes.
- Open playlist.
- Resume job.
- Run Doctor.
- Open settings section.
- Reveal a recent download.

Destructive operations do not execute directly from search results. They open their review surface.

### Feedback

- Input response within 100 ms.
- Local selection is immediate.
- Operations over 300 ms show an inline pending state.
- Toasts report compact success and recoverable failures without stealing focus.
- Persistent or multi-track failures live in the Activity table, not only in toasts.
- Destructive completion offers `View backup`; use `Undo` only where a real transactional undo exists.

## 14. Job And State Model

Every import is persisted as a resumable job.

### Job states

```text
draft
  -> resolving
  -> planned
  -> downloading
  -> analyzing
  -> awaiting_rekordbox_close
  -> writing_rekordbox
  -> awaiting_device_analysis
  -> finalizing_grid
  -> complete

Any running state may also become:
  paused | partial | failed | canceled
```

### Track states

```text
queued -> resolving -> downloading -> converting -> analyzing -> tagging -> ready
ready -> syncing -> synced
any state -> skipped | retryable_failure | permanent_failure
```

### Progress

Use weighted units derived from measurable work:

- Resolve: source entries processed.
- Download: bytes when available, completed tracks otherwise.
- Convert: ffmpeg progress time divided by duration.
- Analyze: analysis phases and completed tracks.
- Rekordbox: planned transaction steps.
- Finalize: tracks with device grids checked and aligned.

Never move progress backward. If the plan grows after resolution, hold the visible percentage and update the denominator without a negative jump.

### Persistence

Use a dedicated app SQLite database under:

```text
~/Library/Application Support/SoundCloud DL/app.db
```

It stores jobs, source snapshots, track mappings, app-generated cue provenance, failure records, and backup metadata. It never replaces or mirrors the full Rekordbox database.

Audio and analysis caches remain file-based next to their source folder where practical, with stable IDs in the app database.

## 15. Technical Architecture

### Decision

Use:

- Tauri 2 desktop host.
- React and TypeScript UI built with Vite.
- Rust for window lifecycle, native dialogs, process control, filesystem watching, USB detection, and sidecar supervision.
- Existing Python modules for SoundCloud, audio analysis, tags, and Rekordbox behavior, compiled and embedded inside the application bundle.
- An internal Python worker with a versioned JSON protocol. It is an implementation detail, not a separately distributed or installed product.
- The existing Go `soundcloud-dl` launcher and OpenTUI remain available from source for development and recovery, but are not required by the desktop release.

Tauri explicitly supports bundling external binaries, including Python applications packaged as sidecars. See [Embedding External Binaries](https://v2.tauri.app/develop/sidecar/). Its command and channel model is also a good fit for typed requests and streaming progress; commands should remain async so heavy work never blocks the UI thread. See [Calling Rust from the Frontend](https://v2.tauri.app/develop/calling-rust/) and [Calling the Frontend from Rust](https://v2.tauri.app/develop/calling-frontend/).

### Why Tauri instead of Electron

- Smaller native shell and lower idle overhead.
- Native process and filesystem boundary in Rust.
- Explicit capability permissions.
- Straightforward sidecar packaging for the existing Python core.
- React still provides the UI ecosystem needed for tables, waveform inspection, and accessible primitives.

The Python analysis payload will still be large because of NumPy, librosa, audio decoders, and ffmpeg. Tauri does not eliminate that payload; it prevents the UI runtime from adding another large embedded browser distribution. Bundle size is secondary to shipping one dependable app that starts quickly and contains everything it needs.

### Process topology

```text
React UI
   | typed Tauri commands/channels
   v
Rust desktop host
   | supervises, validates, forwards
   v
Python worker sidecar
   |-- yt-dlp / ffmpeg subprocesses
   |-- audio analysis process pool
   |-- app state SQLite
   `-- short-lived pyrekordbox sessions

Source-only Go/OpenTUI CLI -> same Python application services
```

The process diagram describes runtime isolation, not separate products. On macOS, a `.app` is technically a signed directory containing an executable and its resources. The distribution requirement is one app bundle and one installer artifact; internal helper executables must live inside that signed bundle, share its version, and never require user management.

### Keep business logic out of the UI

React may select options and render previews. It must not decide:

- Whether a Rekordbox cue is safe to overwrite.
- Whether a database write can begin.
- Which source tracks are deltas.
- How loop timestamps snap to a grid.
- Whether a backup is valid.

Those invariants stay in Python and are tested once for both desktop and CLI.

### Python service layer

Refactor the current command handlers behind application services without rewriting working algorithms:

```text
backend/app/services/
  sources.py       resolve URLs, playlists, and likes
  jobs.py          create, resume, cancel, and report jobs
  downloads.py     download and convert orchestration
  analysis.py      track analysis, cache, tags, cue suggestions
  rekordbox.py     plans, guarded writes, backup, playlist operations
  doctor.py        diagnostics and repairs
```

`soundcloud_bridge.py`, the OpenTUI, and the desktop worker become adapters around these services. This prevents a third independent implementation of the workflow.

### Worker protocol

Use newline-delimited JSON-RPC-style messages over stdin/stdout:

```json
{"id":"req_123","method":"jobs.start","params":{"source_id":"src_1","cue_mode":"fill"}}
{"id":"req_123","result":{"job_id":"job_42"}}
{"event":"job.progress","params":{"job_id":"job_42","track_id":"trk_7","stage":"analyzing","progress":0.64}}
```

Protocol rules:

- Stdout is protocol-only. Logs and third-party output go to stderr or rotating log files.
- Every request and event includes a protocol version at connection negotiation.
- Commands are idempotent where practical and mutation commands accept an idempotency key.
- Cancellation is cooperative and scoped to a job or track.
- Large waveform peak arrays are cached on disk and fetched by ID, not emitted in global events.
- Python owns schema definitions with Pydantic; generate JSON Schema and TypeScript types during development.

### Sidecar lifecycle

- First paint does not wait for the sidecar.
- Start the worker immediately after the window becomes visible.
- Show a small status-line transition from `Starting engine` to `Ready`.
- Restart the worker after an unexpected exit and reconnect to persisted jobs.
- Limit crash restart loops and expose logs after repeated failure.
- On normal app quit, stop accepting new jobs, persist state, then terminate child processes cleanly.

### Single-bundle packaging

Package the complete engine inside the signed `.app` so users install only one product. There is no external worker download, package-manager step, global symlink, background daemon, or first-run dependency installer.

Recommended path:

1. Compile the Python worker and its interpreter into an architecture-specific embedded engine using PyInstaller one-folder mode.
2. Place the engine, ffmpeg, yt-dlp code, fonts, licenses, and native libraries under the Tauri app's signed resource directories.
3. Resolve every internal path through the macOS app bundle. Never resolve tools from the repository, `PATH`, Homebrew, a virtualenv, or the current working directory.
4. Give the UI executable and every nested Mach-O file the correct hardened-runtime signature before signing and notarizing the outer `.app`.
5. Produce one signed `.dmg` containing one drag-installable `.app`; updates replace that complete signed app bundle.
6. Build Apple Silicon first, then a universal bundle only after Intel dependencies and size are validated.
7. Validate NumPy, librosa, soundfile, pyrekordbox, yt-dlp, ffmpeg, and all native library imports from the final installed app in CI.
8. Run an offline first-launch test on a clean macOS account with Homebrew and developer tools absent.

PyInstaller one-folder mode is intentionally internal to the `.app`. It avoids slow self-extraction on every cold launch and makes native libraries code-signable in place. From the user's perspective there is still one application, one icon, one version, and one update. A literal single Mach-O executable would require replacing or statically embedding Python, yt-dlp, ffmpeg, librosa, and their native dependencies; that adds major maintenance risk without improving the installation experience.

Single-bundle invariants:

- The app works with networking disabled after installation, except for operations that inherently contact SoundCloud or check for updates.
- Engine and UI versions cannot drift.
- The app never starts a persistent login item or daemon.
- Temporary subprocess files live in the app cache and are removed after clean shutdown.
- User data lives in Application Support and Downloads, never inside the signed bundle.
- The app performs a fast bundle-integrity and engine-version check before accepting a job.

### Updates

Use signed whole-app updates only after notarized release builds are stable. Never update the Python engine, yt-dlp, or ffmpeg independently of the desktop app. Tauri's updater requires signed update artifacts, which is the correct default for an app that can mutate a music library. See the official [Tauri Updater](https://v2.tauri.app/plugin/updater/).

## 16. Frontend Architecture

### Recommended libraries

- React and TypeScript.
- Vite.
- TanStack Query for command-backed read models and invalidation.
- TanStack Table plus TanStack Virtual for long track and playlist lists.
- Radix primitives for dialogs, popovers, tooltips, menus, and accessible focus behavior.
- Lucide for icons.
- wavesurfer.js for playback and waveform interaction, fed precomputed peaks from Python.
- Zustand only for short-lived UI state such as selection, panel width, and active filters.
- Plain CSS variables plus utility classes for tokens; avoid a large visual component theme.

### State boundaries

| State | Owner |
| --- | --- |
| Job truth and progress | Python worker, persisted in app SQLite. |
| Rekordbox mutation state | Python worker. |
| Cached queries | TanStack Query. |
| Window, panel, selection, filters | React UI store. |
| Playback position and zoom | Waveform component. |
| Settings | Worker persisted config, cached in UI. |

### Rendering performance

- Virtualize tables above 50 rows.
- Keep waveform canvas isolated from table rendering.
- Subscribe job rows to track-specific event slices.
- Batch progress events to the UI at 10 Hz maximum while retaining full worker precision.
- Use stable row heights and tabular numbers.
- Decode and cache waveform peaks once per analysis version.
- Lazy-load the waveform inspector and Doctor detail views.
- Do not rerender the full table for every ffmpeg progress tick.

## 17. Performance Budgets

Target on an Apple Silicon Mac with warm app assets:

| Metric | Target |
| --- | --- |
| Window visible | Under 400 ms. |
| Shell interactive | Under 700 ms. |
| Navigation feedback | Under 100 ms. |
| Source paste acknowledgement | Under 100 ms. |
| Cached source plan visible | Under 500 ms. |
| Table scroll | Sustained 60 fps for 5,000 rows. |
| Worker ready | Under 2.5 s warm, under 5 s cold. |
| Progress event latency | Under 200 ms. |
| Idle CPU | Under 1% after settling. |

These are engineering budgets, not promises. Instrument them in development builds and fail obvious regressions before release.

## 18. Rekordbox Safety Architecture

Every write follows the same state machine:

```text
build immutable plan
  -> verify Rekordbox closed
  -> verify database identity and supported schema
  -> create and validate backup
  -> re-check Rekordbox closed
  -> open short transaction
  -> re-check occupied cue slots
  -> apply idempotent changes
  -> commit
  -> close database
  -> verify expected rows
  -> append audit record
```

Hard requirements:

- Never keep a writable Rekordbox connection alive while the user browses the app.
- Never begin a write from a stale UI preview.
- Never continue a playlist write after any cue-occupancy precondition changes.
- Roll back on any exception and keep the backup path visible.
- Refuse unsupported Rekordbox schema versions with a concrete message.
- Surface database exceptions as a repairable operation, not raw tracebacks.
- Record app version, analysis version, operation, playlist, content IDs, and backup path in the audit log.

## 19. Error Model

Use typed errors with severity and recovery metadata:

```json
{
  "code": "REKORDBOX_OPEN",
  "title": "Rekordbox is still open",
  "detail": "The files are ready, but the playlist has not been updated.",
  "recovery": ["request_close", "check_again", "finish_later"],
  "retryable": true
}
```

Error classes:

| Class | UI behavior |
| --- | --- |
| Track permanent | Row remains failed; job continues. Example: DRM protected. |
| Track retryable | Row offers retry; job continues. Example: network timeout. |
| Job blocked | Job waits with a primary resolution action. Example: Rekordbox open. |
| Job fatal | Job stops safely, preserves completed work, and offers logs/retry. |
| Safety refusal | No mutation starts; explain the exact failed precondition. |

Tracebacks belong in expandable diagnostics and logs, never as the primary error message.

## 20. Accessibility

- Full keyboard navigation and visible focus rings.
- VoiceOver names, values, and state for every control.
- Contrast meets WCAG AA for normal text.
- Color is never the only indicator for status or cue type.
- Reduced-motion mode disables nonessential transforms and continuous waveform animation when paused.
- Table rows expose status text, not only icons.
- Progress changes use polite live regions and do not announce every percentage tick.
- Dialog focus is trapped and restored to the triggering control.
- Text remains usable at macOS accessibility scaling without clipping controls.

## 21. Proposed Repository Layout

```text
mixer/
  backend/
    app/
      services/                    shared application services
      desktop_worker.py            sidecar protocol adapter
      soundcloud_bridge.py         OpenTUI adapter
      soundcloud_cli.py            CLI adapter
    desktop/
      package.json
      vite.config.ts
      src/
        app/
        components/
        features/
          import/
          activity/
          downloads/
          rekordbox/
          doctor/
          track-inspector/
        lib/
        styles/
      src-tauri/
        Cargo.toml
        capabilities/
        src/
          commands.rs
          sidecar.rs
          usb.rs
          process.rs
          lib.rs
    tui/                            existing OpenTUI interface
    cmd/soundcloud-dl/              existing Go launcher
    tests/
  docs/
    desktop-app-product-plan.md
```

Keep desktop code under `backend/desktop/` while the repository remains focused on one product. Rename the top-level directory only if the project later grows beyond the current backend-centric history.

## 22. Implementation Plan

### Phase 0: Stabilize shared contracts

Goal: prepare the backend for two interfaces without changing user behavior.

Deliverables:

- Extract application services from bridge command handlers.
- Define persisted `Job`, `JobTrack`, `SourceSnapshot`, `CueProvenance`, and `BackupRecord` models.
- Add typed progress events and cancellation tokens.
- Add stable typed errors.
- Add protocol schema generation.
- Keep every existing OpenTUI test passing.

Exit criteria:

- OpenTUI and plain CLI use the same services the desktop worker will use.
- A headless integration test can create, pause, resume, and complete an import job.
- Cue fill mode proves it skips all occupied slots at commit time.

### Phase 1: Desktop shell and read-only data

Goal: ship a fast shell that can inspect existing local state safely.

Deliverables:

- Tauri app, React shell, tokens, typography, sidebar, title bar, status bar.
- Sidecar start, health, logs, restart, and version negotiation.
- Activity, Downloads, Rekordbox playlist, and Settings read views.
- Command palette and keyboard navigation.
- Mock and real bridge modes for frontend development.

Exit criteria:

- Window and interaction performance meet initial budgets.
- 5,000-row tables scroll smoothly.
- Killing the worker shows a recoverable state and reconnects without restarting the UI.
- No writable Rekordbox operation exists yet.

### Phase 2: Download-only and likes

Goal: replace the common CLI download workflow end to end.

Deliverables:

- URL resolution and source plan.
- Folder picker and simple folder-name behavior under `~/Downloads`.
- Delta detection and archive reporting.
- Live per-track download/convert progress.
- Pause, cancel, retry, and app-restart resume.
- Likes saved source and sync action.

Exit criteria:

- Track, playlist, and likes fixtures complete successfully.
- DRM failures remain row-level and produce a useful report.
- Repeat playlist runs download only deltas.
- Quitting and reopening restores in-progress job state correctly.

### Phase 3: Analysis and track inspector

Goal: make analysis understandable and reviewable.

Deliverables:

- Analysis progress and cached results.
- Metadata, alphanumeric key, Camelot key, BPM, loudness, and energy views.
- Playback and waveform inspector.
- Rekordbox ANLZ waveform reader using `PWV4/PWV5` with `PWV3/PWAV` fallbacks.
- Authoritative `PQTZ` beat-grid, phrase, cue, and loop overlays.
- Clearly labeled local preview for tracks awaiting Rekordbox analysis.
- Suggested-versus-existing cue model.
- Low-confidence warnings and per-suggestion exclusion.

Exit criteria:

- An analyzed Rekordbox track renders waveform values and beat timestamps read from its own ANLZ files.
- The waveform response fingerprint changes when Rekordbox rewrites any source ANLZ file.
- A track without ANLZ data is visibly labeled `Local preview` and is never presented as authoritative.
- Waveform overlays use the same timestamps exported to Rekordbox.
- Loop start and end render on exact beat-grid lines from analysis data.
- Large waveforms do not stall navigation or job progress rendering.

### Phase 4: Guarded Rekordbox writes

Goal: make desktop import safer and clearer than the TUI.

Deliverables:

- Playlist create/select flow with SoundCloud title default.
- Immutable transaction preview.
- Rekordbox-open detection and normal close request.
- Backup, write, verify, and audit flow.
- Cue modes: Off and Fill empty slots.
- Generated-cue removal with affected-track review.
- Device-analysis and grid-finalization blocked states.

Exit criteria:

- Existing manual cues survive all normal sync and reanalysis cases.
- Occupied hot-cue slots are never changed.
- Every mutation has a valid backup and audit record.
- Force-closing the worker during a simulated transaction leaves the fixture database recoverable.
- New and existing playlists remain delta-safe and idempotent.

### Phase 5: Doctor, USB readiness, and release polish

Goal: make the app dependable without terminal intervention.

Deliverables:

- Doctor checks and previewable repairs.
- Backup history and guarded restore.
- USB detection and export-readiness status.
- Empty, loading, partial, offline, and failure-state polish.
- Accessibility pass.
- Signed, hardened, notarized macOS build.
- Signed updater pipeline after stable manual releases.
- One self-contained `.app` with all internal engines and binaries signed inside it.

Exit criteria:

- A new Mac can install one app and run without Python, Bun, Go, `uv`, ffmpeg, yt-dlp, Homebrew, or developer tools installed.
- Gatekeeper accepts the signed app.
- Removing the source checkout and disconnecting external runtime paths does not affect the installed app.
- Manual XDJ export workflow succeeds from a clean test user account.
- No normal user workflow requires opening Terminal.

## 23. Testing Strategy

### Python

- Preserve and expand pytest coverage for downloads, analysis, cue invariants, Rekordbox backups, deltas, and repairs.
- Use fixture Rekordbox databases for supported schema versions.
- Add fixture ANLZ files covering `PQTZ`, `PWAV`, `PWV3`, `PWV4`, and `PWV5`; assert decoded arrays, durations, colors, beat numbers, and timestamps.
- Assert ANLZ waveform responses are downsampled deterministically without moving beat-grid timestamps.
- Property tests for cue occupancy and idempotency.
- Fault injection before backup, during mutation, before commit, and after commit.

### Rust host

- Unit tests for process detection, path validation, USB classification, and sidecar message parsing.
- Integration tests with a fake worker that emits progress, malformed messages, delays, and crashes.
- Capability tests ensure the webview cannot spawn arbitrary commands or read arbitrary paths.

### React

- Component tests for tables, progress, errors, dialogs, and local Back behavior.
- Playwright against the Vite UI with a mocked Tauri bridge for complete workflows.
- Visual snapshots at compact and comfortable density, light and dark mode.
- Keyboard-only and reduced-motion test passes.

### Packaged app

- Smoke test the signed `.app`, bundled worker, ffmpeg, and audio libraries on a clean macOS user.
- Test cold start, worker restart, sleep/wake, network loss, low disk space, and removable-drive changes.
- Manually verify a representative playlist in Rekordbox and on XDJ hardware before each stable release.

## 24. Product Metrics And Diagnostics

Keep metrics local by default. A diagnostics screen should show:

- Median source-resolution duration.
- Download throughput.
- Analysis duration per track minute.
- Job completion and partial-failure counts.
- Rekordbox transaction duration.
- Worker crash count.
- App and protocol versions.

Optional telemetry, if ever added, must be opt-in and exclude URLs, track names, artists, file paths, Rekordbox content, and audio fingerprints.

## 25. Release Strategy

1. Internal developer build using the real library and fixture databases.
2. Read-only alpha with downloads and analysis but no Rekordbox writes.
3. Guarded-write beta with prominent backup history and audit views.
4. Stable macOS Apple Silicon release as one signed `.dmg` containing one self-contained `.app`.
5. Intel macOS build only after measuring demand and bundle support.
6. Windows evaluation only after Rekordbox paths, process behavior, audio binaries, signing, and hardware export are independently validated.

The OpenTUI CLI remains available throughout. It is both a fallback and a way to validate that business logic is not coupled to the desktop interface.

## 26. Decisions To Validate In A Prototype

These are the highest-risk questions for a two-week technical prototype:

| Question | Prototype proof |
| --- | --- |
| Can the packaged Python sidecar start quickly enough? | Build the real dependency bundle and measure cold/warm start. |
| Can sidecar progress remain clean under yt-dlp and ffmpeg output? | Enforce protocol-only stdout and stress concurrent jobs. |
| Can the waveform stay smooth during analysis events? | Render a long track while emitting 10 Hz progress updates. |
| Can a job resume after app and worker termination? | Kill both processes at every stage and resume from persisted checkpoints. |
| Can cue occupancy be revalidated safely at commit? | Change fixture cues after preview and assert the write skips conflicts. |
| Can a signed app bundle all native audio libraries? | Notarize and run on a clean macOS user account. |

## 27. Definition Of A Successful First Release

The desktop app is ready when a user can:

1. Install one signed macOS app with no companion installer, runtime, daemon, or command-line dependency.
2. Paste a public SoundCloud track, playlist, or likes URL.
3. See an accurate delta plan.
4. Download and analyze with clear per-track progress.
5. Recover from unavailable tracks without losing successful work.
6. Select or create the correctly named Rekordbox playlist.
7. Leave hot cues off or fill only empty slots.
8. Close Rekordbox through a safe prompt when a write is required.
9. Verify the backup and completed playlist.
10. Open Rekordbox and export the playlist to USB for XDJ use.

No terminal, hidden command, database knowledge, or manual cache cleanup should be required for that path.
