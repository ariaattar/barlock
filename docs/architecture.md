# Crate architecture

The maintained interfaces are a Tauri desktop app and the interactive `soundcloud-dl` terminal application. Both use the Python engine in `backend/app/`. There is no web service to deploy.

The desktop renderer calls a narrow Tauri bridge. Each command runs a short-lived engine worker and streams structured JSON progress back through a Tauri channel. A development build uses the local Python environment; a release bundles a frozen worker and ffmpeg. Browser tests exercise the same interface against deterministic mock responses.

`App.tsx` coordinates imports, navigation, local history, and contextual inspectors. Shared chrome, controls, keyboard navigation, and dialog focus behavior live in `src/components/`. `TrackEditor.tsx` owns waveform playback and correction drafts. Styles are separated into theme/base tokens, the workspace, the editor, and Crate identity.

Imports expand SoundCloud sources before downloading. Metadata expansion has bounded network retries and parallel metadata reads with progress. Download history is reconciled with files on disk so a deleted folder can be imported again. Title and artist tags flow from source metadata through analysis into Rekordbox.

Desktop job history is stored locally; hiding completed Activity entries leaves Downloads intact. Library rows are deduplicated by source track identity. Import state remains mounted while users browse other screens, so navigation does not discard the source review or interrupt progress.

Correction drafts and cue provenance live in a local SQLite database. Saves are serialized with optimistic revisions. File locks keep active apply transactions separate from crash recovery across workers. Apply operations create backups and journals before writes, verify results, and restore interrupted work. Exact Rekordbox beat timestamps remain untouched until the user explicitly changes tempo or downbeat timing.

The app is called **Crate**, the repository is **crate**, and the desktop package is `crate-desktop`. Compatibility identifiers deliberately remain stable: `com.ariaattar.soundcloud-dl`, `soundcloud-dl` CLI/config, the worker protocol, local storage keys, and `~/Library/Application Support/SoundCloud DL`. Legacy cue provenance must retain its recognizable signatures. A branding change must not orphan a user's library, settings, or manual cues.

Build from `backend/desktop` with `bun run bundle`. Product name and version are read from `tauri.conf.json`; the installer filename follows the host architecture. `assets/app-icon.svg` is the icon source; regenerate platform icons with `bun run tauri icon assets/app-icon.svg`.
