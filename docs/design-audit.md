# Crate design audit

Branch: `codex/crate-design`. Audited the installed Tauri app using computer use, then rebuilt and revisited the native interface. The direction is a restrained music workspace: Geist typography, warm neutral surfaces, a small periwinkle identity accent, and clear, contextual actions. Decorative record sleeves belong to the import landing screen; track waveforms must represent actual audio or Rekordbox analysis.

## Identity and repository

| Before | After |
| --- | --- |
| App named SoundCloud DL; GitHub repository named `barlock`; local checkout named `mixer` | Product **Crate**, GitHub **ariaattar/crate**, branch **codex/crate-design**. The existing checkout path stays stable for local tooling. |
| Generic waveform app icon | Original vector crate mark in `assets/app-icon.svg`, regenerated macOS/platform icons and source PNG. |
| Separate hardcoded app/DMG names and versions | Packaging reads product name and version from `tauri.conf.json`; DMG architecture follows the build host. |
| Desktop package `soundcloud-dl-desktop` | `crate-desktop`, updated Rust package/library, Cargo/Bun locks, HTML title, window title, metadata, and native error label. |
| Terminal, Python, and Go project names differed from the product | `crate-tui`, `crate-engine`, and Go module `github.com/ariaattar/crate/backend`; terminal home title is Crate. The `soundcloud-dl` command remains compatible. |
| Long root README, contradictory memory-cue claim, obsolete two-deck spec | Short current README, detailed usage reference, current architecture, historical proposals explicitly archived under `docs/history/`. |
| Branding tied to persisted identifiers | Visible branding is renamed; identifiers, config paths, cue signatures, app database path, and storage keys remain stable to preserve user state. |
| Shared UI and navigation mixed into `App.tsx` | `AppChrome`, `Primitives`, `CommandPalette`, and `useDialogFocus` live under `src/components/`. |
| One large stylesheet | An import manifest with separate tokens/base, workspace, editor, and identity files. Existing rules are edited in place. |
| Unused declarations accumulated | Removed unused imports/props/interfaces and enabled TypeScript unused-local/parameter checks. |

## Layout and visual hierarchy

| Before | After |
| --- | --- |
| An empty inspector consumed a third of the window on launch | No inspector until a selection exists; navigation dismisses it. It becomes an overlay at compact widths to preserve the working area. |
| Disabled Back button, redundant title description, fake account initials and overflow affordance | Removed those controls; local-library identity is descriptive and the header is focused on navigation. |
| Rekordbox status repeated in header and footer | A single global status in the footer; actionable import blocks remain contextual. |
| Dense 7–11px labels, dim tertiary text | Workspace/editor text has a 12px floor, track titles are 14px, and secondary contrast improves in both themes. Tiny text remains only in decorative artwork/brand microcopy. |
| Small 30–36px primary controls | Main actions, search fields, navigation, and icon buttons use 40–42px targets; icon hit extensions no longer overlap. |
| Narrow title columns, dense rows, repeated blue completion bars | Roomier 64px track rows, flexible title columns, readable artist lines, title tooltips, and progress only for active stages. |
| All fallback successes tinted entire rows | Neutral completed rows with small status indicators; failure detail retains attention. |
| Plain import header | A distinct Crate source-entry composition with restrained sleeve artwork and a clear SoundCloud link label. |
| Large introductory content persisted after resolving | Compact source-review header and an action footer in its own layout row, outside the scrolling content. |
| Source preview displayed titles with fabricated mini waveforms | Real title/artist metadata with a simple audio-file icon. |
| Completed jobs kept a large active progress treatment | Quiet completion summary, ready state, and useful destination information. |
| Playlist list repeated path/name and “Loaded” badges in every row | Useful hierarchy/path labels and a clear directional affordance; generated-cue removal is visually secondary and remains accessible. |
| Inspector playlist cover reused a simulated track waveform | Clearly decorative collection artwork. |
| Missing focus/selection treatment and inconsistent sort control on macOS | Keyboard row focus, active navigation semantics, pressed toggle states, selected command rows, and a styled sort select. |
| Inconsistent motion handling | Existing short property-specific transitions retained; global reduced-motion support disables decorative movement. |

## Workflow and trustworthy states

| Before | After |
| --- | --- |
| Switching views destroyed the import review and local progress context | Import workspace remains mounted while hidden; resolved source and options survive navigation. |
| Opening a completed Activity entry inspected an arbitrary first track | Opens the completed job summary without an unnecessary SoundCloud resolve. |
| Clearing completed Activity removed jobs and therefore Downloads, including failures | Hides completed entries separately; prepared tracks and unfinished/failed work remain available. |
| Repeated imports duplicated library rows | Library deduplicates source track identities and includes ready replacement files. |
| No library sorting | Recent, title, artist, and BPM sorting alongside search. |
| Search misses looked like an empty library | Distinct “No matching tracks/playlists” states with useful next steps. |
| Local inspector showed a hash-generated waveform and fixed energy `7` | Honest preview invitation and actual file format; no invented audio measurements. |
| Local inspector implied every A–E slot was written or suggested | Shows the real generation policy and directs users to actual Rekordbox cue data. |
| Track inspection had no local route back to its playlist | “Back to playlist” keeps navigation within the inspector. |
| Doctor briefly showed “healthy” and passed checks before receiving data | Explicit initial loading/failure states; passed count includes only successful checks. |
| Saving settings was always enabled and failures were unhandled | Dirty-state save button, disabled until config is available, and inline save errors. |
| Settings exposed fragment/worker implementation details | Concise media behavior and product identity. |
| Native initialization failure silently installed mock configuration | Mock defaults are limited to the browser preview path. |
| Toast timer entries accumulated | Timer entries are removed when dismissed or expired. |

## Keyboard and correction reliability

| Before | After |
| --- | --- |
| “Search or run a command” was a mouse-only four-action menu | Honest “Quick navigation” across all six destinations, filtering, arrow keys, Enter, Escape, empty results, and keyboard hints. |
| Dialog focus could escape and did not return to its opener | Shared focus trap and restoration for dialogs and navigation palette. |
| Switches lacked accessible names | Named switches for import analysis and every settings toggle. |
| Editor saved repeatedly whenever its revision changed | Stable save dependencies, deduplicated serialized saves, and immediate pending-save feedback. |
| Concurrent saves could send the same revision | A save queue orders optimistic revisions; unchanged audition requests do not save again. |
| A worker connection could recover a transaction still being applied elsewhere | A process-level file lock separates active applies from abandoned transaction recovery. |
| Editor generated an additional Outro memory cue | Canonical generation is A–E only, including normalization of legacy incoming drafts; existing manual cues remain read-only unless explicitly supported. |
| Opening a constant-tempo draft could regenerate exact ANLZ timestamps | New drafts preserve the source grid; explicit tempo/downbeat edits opt into grid replacement. Rebasing preserves that distinction. |

## Verification

Native computer-use checks covered import entry, Downloads, playlist browsing and contextual inspection, settings in dark/light themes, and read-only Doctor results. Automated desktop checks cover complete and failed imports, retry after folder deletion, blocked Rekordbox handoff, actual waveform rendering, correction editing/audition, protected files, repair confirmations, keyboard navigation, draft persistence, deduplication/history, and 1040px layouts.

The native Doctor reported missing local collection files and two generated active loops in the user's existing library. These are library findings, separate from this interface work; this audit did not apply repairs or write to the live Rekordbox database.

Test screenshots remain ignored under `backend/desktop/.artifacts/`. Release bundles, frozen workers, downloaded audio, caches, and database backups remain outside version control.

Validated on macOS / Apple Silicon: 115 Python tests passed; all 16 Playwright scenarios passed; desktop and terminal TypeScript checks passed; OpenTUI smoke passed; Go launcher built and `--help` succeeded.

## Import review correction

| Before | After |
| --- | --- |
| Sticky action bar could cover analysis and cue controls while scrolling | Footer is a flex sibling of the scrollable workspace and reserves its own height. No overlap or stacking trick is needed. |
| Import settings were separated by loose lines with a blue cue-policy band | One inset neutral settings panel, consistent padding, taller rows, and clearer option titles. |
| Tests covered viewport overflow but missed settings under the footer | Three regression cases at 1040, 1440, and 2048px verify separation throughout scrolling and click both analysis and cue controls. All 19 desktop tests pass. |
