# AGENTS.md

Guidance for coding agents working in this repository.

## Scope

This repo is now focused on the SoundCloud/Rekordbox prep tool. The web frontend has been removed. Most active work should happen under `backend/`.

Core files:

- `backend/tui/src/main.ts` - OpenTUI interactive terminal interface
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
```

To refresh the global local binary on this machine:

```bash
ln -sfn /Users/ariaattar/Documents/Documents/Code/mixer/backend/bin/soundcloud-dl /opt/homebrew/bin/soundcloud-dl
```

## Safety Rules

- Do not commit downloaded audio, Rekordbox database backups, virtualenvs, build artifacts, or local analysis caches.
- Do not run direct Rekordbox database writes while Rekordbox is open.
- Direct DB writes should continue to create a backup before mutating `master.db`.
- Preserve user/manual Rekordbox cues. Only generated SoundCloud DL cues should be overwritten automatically.
- Keep the normal user workflow interactive through `soundcloud-dl`.
- Prefer improving the OpenTUI flow over adding extra required commands.
- Workflow Back behavior should stay local to the current wizard step. Do not let nested prompts bubble directly back to the home menu unless the user backs out from the first step.

## Analysis And Cue Behavior

The expected cue layout is:

- A: `Intro`
- B: `Phrase 16`
- C: `Phrase 32`
- D: `Intro Loop`
- E: `Exit Loop`
- Memory: `Outro`

D and E loops should be conservative. Prefer clean 4 or 8 beat loops snapped to beat-grid lines. Skip loops that look unstable, faded, off-grid, or transition-heavy.

If loop selection changes, bump `ANALYSIS_VERSION` in `backend/app/audio_features.py` so cached cue hints are regenerated.

## Testing Expectations

For analyzer or Rekordbox sync changes:

```bash
uv run pytest tests/test_audio_features.py tests/test_rekordbox_sync.py -q
```

Before pushing:

```bash
uv run pytest -q
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
