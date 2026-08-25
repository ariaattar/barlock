# AGENTS.md

Guidance for coding agents working in this repository.

## Scope

This repo is focused on the SoundCloud/Rekordbox prep tool. The unrelated web frontend was removed. The maintained interfaces are the native Tauri desktop app and the OpenTUI terminal app under `backend/`.

Core files:

- `backend/tui/src/main.ts` - OpenTUI interactive terminal interface
- `backend/desktop/src/App.tsx` - React interface hosted by Tauri
- `backend/desktop/src-tauri/src/lib.rs` - native bridge to the packaged engine
- `backend/desktop/engine/worker.py` - frozen desktop engine entrypoint
- `backend/scripts/build_desktop_app.sh` - self-contained `.app` and DMG build
- `backend/app/soundcloud_bridge.py` - JSON bridge for the TUI
- `backend/app/soundcloud_cli.py` - plain Python URL/subcommand backend
- `backend/app/soundcloud_downloader.py` - SoundCloud URL expansion and downloads
- `backend/app/audio_features.py` - BPM, key, energy, cue, and loop analysis
- `backend/app/rekordbox_sync.py` - Rekordbox import and direct database sync
- `backend/cmd/soundcloud-dl/main.go` - global launcher binary
- `backend/tests/` - pytest suite

## Development Commands

Use these from `backend/` unless noted otherwise:

```bash
uv sync
uv run pytest -q
cd tui && bun install && bun run check && bun run smoke
go build -o bin/soundcloud-dl ./cmd/soundcloud-dl
cd desktop && bun install && bun run check && bun run test:e2e
```

Build the bundled desktop product from `backend/desktop/`:

```bash
bun run bundle
```

To refresh the global local binary on this machine:

```bash
ln -sfn "$(pwd)/bin/soundcloud-dl" /opt/homebrew/bin/soundcloud-dl
```

## Safety Rules

- Do not commit downloaded audio, Rekordbox database backups, virtualenvs, build artifacts, or local analysis caches.
- Do not run direct Rekordbox database writes while Rekordbox is open.
- Direct DB writes should continue to create a backup before mutating `master.db`.
- Preserve user/manual Rekordbox cues. Do not overwrite existing cues during normal sync or reanalysis.
- Hot-cue writing is opt-in. The normal sync and reanalysis default is off; fill mode may only write empty slots and must never delete, move, or overwrite an existing cue.
- Cue removal must be an explicit playlist-scoped action, create a backup first, and remove only recognizable SoundCloud DL-generated cues on managed tracks.
- Never present a locally decoded waveform as Rekordbox-accurate. Authoritative waveform views must come from the track's own ANLZ data and retain exact `PQTZ` timestamps.
- Keep local waveform previews visibly labeled until Rekordbox has created ANLZ files.
- Do not commit generated desktop bundles, PyInstaller output, Playwright artifacts, or Tauri build output.
- Keep the normal user workflow interactive through `soundcloud-dl`.
- Prefer improving the OpenTUI flow over adding extra required commands.
- Workflow Back behavior should stay local to the current wizard step. Do not let nested prompts bubble directly back to the home menu unless the user backs out from the first step.

## Analysis And Cue Behavior

When optional hot-cue generation is enabled, the expected cue layout is:

- A: `Intro`
- B: `Phrase 16`
- C: `Phrase 32`
- D: `Intro Loop`
- E: `Exit Loop`

Do not generate memory cues. On some XDJ configurations, a late-track memory cue becomes the load position. Use E (`Exit Loop`) as the generated exit marker instead.

D and E loops should be conservative. Prefer clean 4 or 8 beat loops that start and end on beat-grid lines, favoring the first clean beat after pickup or transition hits. Skip loops that look unstable, faded, off-grid, or transition-heavy.

If loop selection changes, bump `ANALYSIS_VERSION` in `backend/app/audio_features.py` so cached cue hints are regenerated.

## Testing Expectations

For analyzer or Rekordbox sync changes:

```bash
uv run pytest tests/test_audio_features.py tests/test_rekordbox_sync.py -q
```

Before pushing:

```bash
uv run pytest -q
cd desktop && bun run check && bun run test:e2e
```

If the launcher changes:

```bash
go build -o bin/soundcloud-dl ./cmd/soundcloud-dl
./bin/soundcloud-dl --help
cd tui && bun run check && bun run smoke
```

## Git Notes

- Check `git status -sb` before staging.
- Stage only intended files.
- Do not revert unrelated user changes.
- This repo may contain ignored local data under `data/`, `backend/bin/`, `backend/dist/`, and download folders.
