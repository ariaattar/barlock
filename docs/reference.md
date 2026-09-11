# Crate reference

Desktop and OpenTUI SoundCloud downloader, audio analyzer, and Rekordbox playlist importer for DJ prep.

The macOS desktop app is the primary visual workflow. It ships as one application bundle containing the Tauri/React interface, Python engine, yt-dlp, audio analysis stack, and ffmpeg. The existing interactive terminal workflow remains available through:

The main entrypoint is:

```bash
soundcloud-dl
```

Use it to pull SoundCloud tracks, public playlists, or likes, analyze the local files, tag them, and push them into a Rekordbox playlist with useful optional hot cues and loops.

## What It Does

- Downloads SoundCloud tracks, public playlists, and likes with `yt-dlp`
- Converts downloads to high-quality MP3 with ffmpeg
- Runs parallel track downloads and parallel media fragments
- Skips already downloaded tracks on playlist reruns
- Accepts a simple folder name like `set-2` and resolves it to `~/Downloads/set-2`
- Analyzes BPM, musical key, Camelot key, loudness, energy, and cue hints
- Writes MP3 ID3 tags for title, artist, BPM, key, and source metadata
- Generates Rekordbox `.m3u8` and XML import files
- Pushes directly into an existing or new Rekordbox playlist
- Picks up the SoundCloud playlist title as the default Rekordbox playlist name
- Backs up Rekordbox database files before direct database writes
- Reads Rekordbox's own ANLZ beat grid, native three-band/RGB waveform, cues, and loops for an authoritative track inspector
- Detects mounted export USB devices and whether a Pioneer/Rekordbox export is present
- Provides a native macOS desktop app with persistent jobs, guarded Rekordbox handoff, and delta-aware imports
- Uses an OpenTUI interactive terminal app with arrow-key menus and back navigation

## Desktop App

The desktop app supports the complete prep workflow without requiring a separate backend process:

- Resolve a SoundCloud track, public playlist, or likes URL and review its delta before starting.
- Download into a named folder under `~/Downloads`, with optional BPM/key/energy analysis and ID3 tags.
- Select or create a Rekordbox playlist, with hot cues off by default and an opt-in `Fill empty slots` policy.
- Persist job progress and resume a blocked Rekordbox-close handoff from Activity.
- Inspect collection playlists and tracks using Rekordbox's own ANLZ data.
- Open a full waveform editor to play, scrub, zoom, and audition intro or exit loops before writing them.
- Correct BPM, first-downbeat offset, Camelot key, phrase cues, and 4/8-beat loop boundaries with beat-grid snapping.
- Autosave correction drafts and apply them through a backed-up, verified Rekordbox transaction.
- Run Doctor checks, guarded loop repairs, and playlist-scoped removal of generated cues.

### Rekordbox-accurate waveforms

The track inspector does not redraw an approximation and label it as Rekordbox data. For tracks already analyzed by Rekordbox it reads:

- `PQTZ` for exact beat numbers, BPM changes, and grid timestamps
- `PWV6` for the native three-band full-track overview, with `PWV4`/`PWAV` fallback
- `PWV7` at its native 150 columns per second for three-band detail, with `PWV5`/`PWV3` fallback
- Rekordbox database cue and loop timestamps for overlays

The renderer preserves the raw mid/high/low values from Rekordbox's `.2EX` analysis and uses the same low, overlap, mid, and high layer order as CDJ-3000-style displays. The response includes an ANLZ fingerprint so rewritten analysis files invalidate the displayed waveform. A track without ANLZ files is visibly labeled `Local preview` until Rekordbox analyzes it.

### Waveform correction editor

Open a downloaded or Rekordbox track and choose `Edit waveform`. The editor treats automatic analysis as a draft: use `A Through` to hear the loop transition in context or `B Loop` to repeat the exact hard loop boundary. Drag cue and loop markers, drag the first-downbeat anchor, or use the numeric BPM/grid controls for precise changes.

Rekordbox tracks use the existing `PQTZ` grid and prefer the native `PWV6`/`PWV7` three-band waveform as the source of truth. Constant-tempo grids can be corrected; variable-tempo grids remain read-only so the original analysis is preserved. Applying a draft requires Rekordbox to be closed, creates backups, verifies the resulting grid, and skips occupied manual cue slots. Existing cues are changed only when they have SoundCloud DL provenance or are explicitly adopted in the editor.

Build the self-contained local app and DMG:

```bash
cd backend/desktop
bun install
bun run bundle
```

Artifacts are written to:

```text
backend/desktop/src-tauri/target/release/bundle/macos/Crate.app
backend/desktop/src-tauri/target/release/bundle/dmg/Crate_0.1.0_arm64.dmg
```

The local build is ad-hoc signed when no Apple Developer signing identity is configured. Public distribution still requires Developer ID signing and notarization.

## Cue Layout

Optional generated hot cues are designed for fast house and tech-house prep:

| Pad | Cue | Purpose |
| --- | --- | --- |
| A | Intro | Start point for loading or launching the track |
| B | Phrase 16 | Faster mix-in landmark |
| C | Phrase 32 | Safer long-blend landmark |
| D | Intro Loop | Clean intro loop when the analyzer finds one |
| E | Exit Loop | Clean outro/exit loop when the analyzer finds one |

D and E are conservative. The analyzer prefers clean 4 or 8 beat loops that start and end on beat-grid lines, favors the first clean beat after pickup or transition hits, and skips a loop when the section looks unstable, faded, or transition-heavy. Bad automatic loops are worse than missing loops.

The tool does not create memory cues. Some XDJ load settings jump to a stored memory cue, so the generated exit marker remains hot cue E instead of a red late-track memory marker.

Hot-cue writing is disabled by default. During sync or reanalysis, choose `Fill empty hot-cue slots only` to add generated cues without moving, deleting, or overwriting anything already stored in Rekordbox. The home menu also includes `Remove generated hot cues`, scoped to a selected playlist and backed up before deletion.

## Install

From the repo root:

```bash
cd backend
uv sync
cd tui && bun install && cd ..
go build -o bin/soundcloud-dl ./cmd/soundcloud-dl
ln -sfn "$(pwd)/bin/soundcloud-dl" /opt/homebrew/bin/soundcloud-dl
```

Requirements:

- macOS
- Python 3.10 to 3.12
- `uv`
- Bun, for the OpenTUI interactive app
- Go, only for rebuilding the launcher binary
- Rekordbox 6 database available at `~/Library/Pioneer/rekordbox`

The launcher starts `backend/tui` for interactive use and keeps `uv run python -m app.soundcloud_cli` for URL/subcommand shortcuts.

## Usage

Start the interactive terminal UI:

```bash
soundcloud-dl
```

Interactive menus support Up/Down, Enter to select, Escape/Left/`b` to go back, and `q` to quit from menus. Text fields use Escape/Left for back so normal folder names and URLs can include any letters.

Download and import workflows show a `Step X/Y` progress indicator in the header. Back navigation is local to the current workflow, so pressing Left halfway through prep returns to the previous step instead of dumping you at the home menu.

Download-only flow:

1. Choose `Download only`.
2. Paste a SoundCloud track, playlist, or likes URL.
3. Review the track count and suggested folder.
4. Enter a folder name or full path. `set-2` means `~/Downloads/set-2`.
5. Download. No analysis, tags, or Rekordbox changes are made.

Rekordbox sync flow:

1. Choose `Sync a SoundCloud URL`.
2. Paste a SoundCloud track, playlist, or likes URL.
3. Review deltas and the auto-picked playlist name.
4. Confirm analysis and choose whether to leave cues disabled or fill empty hot-cue slots.
5. Close Rekordbox when prompted so playlist changes can be written.
6. If empty cue slots were filled on newly imported tracks, let the CLI open Rekordbox for device-grid analysis, then return to the CLI to close Rekordbox and align D/E loops before export.

Likes sync prompts for a SoundCloud username. The entered username is saved as
the default for the next run and can be changed at any time.

Examples of accepted URLs:

```text
https://soundcloud.com/deeperpurpose/jenny-extended
https://soundcloud.com/your-username/sets/set-2
https://soundcloud.com/your-username/likes
```

Advanced shortcuts also exist:

```bash
soundcloud-dl https://soundcloud.com/deeperpurpose/jenny-extended -o jenny
soundcloud-dl sync-likes your-username
soundcloud-dl doctor
soundcloud-dl --dry-run https://soundcloud.com/deeperpurpose/jenny-extended
```

## Rekordbox Workflow

Direct Rekordbox push requires Rekordbox to be closed. If Rekordbox is open, the CLI can still generate import files instead.

Direct push:

- Creates or selects a Rekordbox playlist
- Adds missing tracks to the collection
- Adds new tracks to the playlist without duplicating existing playlist rows
- Updates metadata for tracks managed by this tool
- Leaves existing Rekordbox cues untouched by default
- Optionally fills empty hot-cue slots without replacing occupied slots
- Detects new tracks without a Rekordbox beat grid and offers an in-flow analysis/finalization pass
- Preserves cues that were not generated by this tool
- Creates a timestamped backup in `~/Library/Pioneer/rekordbox/backups`

For USB export to XDJ/CDJ hardware, export from Rekordbox in Export mode after confirming the playlist and cues look right. Do not copy MP3 files directly to the USB if you want Rekordbox playlists, waveforms, cues, and loops to show up.

## Delta Downloads

Each output folder keeps per-source download archives under:

```text
.soundcloud-archives/
```

Rerunning the same SoundCloud playlist only pulls deltas. If no new files are downloaded, the CLI still uses existing local files for analysis and Rekordbox sync.

Sync is additive: tracks that disappear from likes or playlists (including takedowns) remain in your local library, Rekordbox playlist, and download history. Missing upstream tracks are never automatically removed.

## Configuration

Settings are stored at:

```text
~/.config/soundcloud-dl/config.json
```

The interactive `Settings` menu controls:

- SoundCloud username for likes
- Default output folder
- Default Rekordbox playlist
- Analyze/tag/import defaults

The interactive flow hides expert download tuning. It automatically uses 320 kbps MP3 output, bounded parallel track downloads, and bounded fragment concurrency based on the saved config and local CPU capacity.

## Development

Backend setup:

```bash
cd backend
uv sync
cd tui && bun install && bun run check && bun run smoke && cd ..
uv run pytest -q
```

Desktop development and verification:

```bash
cd backend/desktop
bun install
bun run check
bun run test:e2e
bun run tauri dev
```

Rebuild the launcher:

```bash
cd backend
go build -o bin/soundcloud-dl ./cmd/soundcloud-dl
```

Run the CLI without rebuilding:

```bash
cd backend/tui
bun run start
```

## Project Layout

```text
backend/tui/src/main.ts               OpenTUI interactive terminal UI
backend/desktop/src/App.tsx           Native desktop product interface
backend/desktop/src-tauri/src/lib.rs  Tauri-to-Python streaming bridge
backend/desktop/engine/worker.py      Frozen desktop engine entrypoint
backend/scripts/build_desktop_*.sh    Self-contained app and DMG packaging
backend/app/soundcloud_bridge.py      JSON bridge used by the TUI
backend/app/soundcloud_cli.py         Plain Python URL/subcommand backend
backend/app/soundcloud_downloader.py  SoundCloud expansion and parallel downloads
backend/app/audio_features.py         BPM/key/energy/cue and loop analysis
backend/app/rekordbox_sync.py         Rekordbox import files and direct DB push
backend/cmd/soundcloud-dl/main.go     Small global launcher binary
backend/tests/                        Test suite
docs/architecture.md      Desktop behavior and architecture contract
```

## Notes

- Some SoundCloud tracks are DRM protected or otherwise unavailable. Those are reported in `failed-downloads.txt`.
- Only download audio you have the rights or permission to save.
- Rekordbox database writes are inherently sensitive. Keep Rekordbox closed and keep the generated backups.
