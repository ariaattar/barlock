# Crate

Your next set starts here. Crate is a native macOS workspace for collecting SoundCloud finds, preparing tracks, and bringing them into Rekordbox.

Paste a public track, playlist, or likes URL. Review the tracks and destination, import, then browse your collection or fine-tune a track in the waveform editor. Repeated imports pick up new tracks without downloading the whole source again.

## Desktop development

Requires macOS, [Bun](https://bun.sh), [uv](https://docs.astral.sh/uv/), Rust, and Xcode Command Line Tools.

```sh
cd backend
uv sync
cd desktop
bun install
bun run tauri dev
```

Build the self-contained app and installer:

```sh
cd backend/desktop
bun run bundle
```

Outputs are in `backend/desktop/src-tauri/target/release/bundle/`: `macos/Crate.app` and `dmg/Crate_0.1.0_arm64.dmg` on Apple Silicon. The bundle includes its Python engine and ffmpeg; end users do not need a development environment.

## Checks

```sh
cd backend
uv run pytest -q
cd desktop
bun run check
bun run test:e2e
```

The desktop tests use simulated engine data. Native app verification uses the bundled engine and the local library.

## Project map

| Path | Purpose |
| --- | --- |
| `backend/desktop/src/` | React desktop workflows, shared controls, and waveform editor |
| `backend/desktop/src/styles/` | Theme tokens, workspace, editor, and visual identity |
| `backend/desktop/src-tauri/` | Native bridge, window configuration, app resources |
| `backend/desktop/engine/` | Packaged engine entrypoint |
| `backend/app/` | Downloads, analysis, correction drafts, and Rekordbox sync |
| `backend/tui/` | Interactive OpenTUI terminal interface |
| `backend/cmd/soundcloud-dl/` | Compatible global CLI launcher |
| `backend/scripts/` | App and engine packaging |
| `backend/tests/` | Python regression tests |

## Library behavior

- Downloads are ordinary audio files in the folder selected during import.
- Rekordbox writes require Rekordbox to be closed and create a backup first.
- Hot cues are off by default. Optional generation fills empty A–E slots and preserves existing cues. Crate does not generate memory cues.
- Authoritative waveforms and beat timestamps come from Rekordbox ANLZ files. Locally decoded audio stays explicitly labeled as a preview.
- The existing `soundcloud-dl` terminal command, config directory, application identifier, and saved state keys remain compatible with previous installations.

See the [usage and CLI reference](docs/reference.md), [architecture](docs/architecture.md), and [design audit](docs/design-audit.md). Earlier product proposals live under [docs/history](docs/history/).
