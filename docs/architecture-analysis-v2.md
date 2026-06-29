# Analysis v2: Top-Tier Cue Generation

**Cue quality is the product.** Everything else in this document — structure, energy, vocals, stems, sparklines — exists in service of placing cues that are *correct on every track, every time*, with conservative behavior when the analyzer is unsure. A wrong cue is worse than a missing cue. A great cue is worth ten good features.

This plan is structured around that primary objective. Sections are ordered by impact on cue quality, not by feature size.

---

## 0. Cue quality, defined precisely

Before any code, we need a definition we can test against. A cue is **correct** when:

1. **Phase-locked.** Its timestamp lies within ±15 ms of a downbeat in the track's beatgrid. Anything looser is audible as a flam when the deck loads.
2. **Musically meaningful.** It marks an event a DJ would actually want to jump to: section change, drop, breakdown start, clean 4/8-bar loop region, intro/outro.
3. **Consistent with the layout contract.** Pad A is always "load and launch", pad C is always "the drop or the safe 32-bar mix-in", etc. The DJ must build muscle memory; surprise placements destroy that.
4. **Conservative under uncertainty.** When confidence is low, the analyzer **degrades to a known-safe heuristic** (bar-counted phrase cues) rather than guessing at a drop. A heuristic Phrase 32 cue at a slightly wrong place is a minor inconvenience; a confidently-wrong "Drop" cue 8 bars early is a public failure on the dance floor.
5. **Idempotent across re-pushes.** Re-running the pipeline on the same track produces the same cues. Already-pushed cues are removed cleanly before new ones are written. No leakage on slot 7 if last week's run wrote there.

We will treat (1)–(5) as acceptance criteria. Every test in this plan exists to defend one of them.

---

## 1. The cue layout contract (frozen)

**This layout is the user-facing API of the tool. It does not change after this version ships.**

| Pad | Hot Kind | Cue Name (high conf.) | Cue Name (fallback) | Source | Always present? |
| --- | --- | --- | --- | --- | --- |
| A | 1 | `Intro` | `Intro` | first downbeat | yes |
| B | 2 | `Build` | `Phrase 16` | structural OR bar*16 | yes |
| C | 3 | `Drop` | `Phrase 32` | structural OR bar*32 | yes |
| D | 5 | `Intro Loop` | — | loop scorer | only if score ≥ accept threshold |
| E | 6 | `Exit Loop` | — | loop scorer | only if score ≥ accept threshold |
| F | 7 | `Breakdown` | — | structural OR energy trough | only if found, ≥ 24 bars in |
| G | 8 | `Last Drop` | — | structural | only if found |
| Memory | 0 | `Outro` | `Outro` | structural outro start OR `duration - 32 bars` | yes |

Three non-negotiable rules from this table:

- **Pad C is always the navigation target for "the drop."** Whether we identified the real drop or fell back to bar 32, the user presses pad C and lands at the safest available mix-in/drop point. This is the most important consistency guarantee in the whole tool.
- **Pads F and G are only written when we're confident.** Empty pads are better than wrong pads.
- **The name reveals the confidence.** "Drop" on pad C means we know it's the drop. "Phrase 32" on pad C means we're guessing. The DJ sees that on the deck screen and treats it accordingly.

Files that encode this contract (and must be updated together so the contract stays consistent):

- `_cue_hints` in `backend/app/audio_features.py` — emits the hints.
- `AUTO_CUE_NAMES` in `backend/app/rekordbox_sync.py` — must allowlist *every* name in the table, both high-conf and fallback variants. Missing names will cause auto-cues to leak across re-pushes.
- `HOT_CUE_KINDS = (1, 2, 3, 5, 6, 7, 8, 9)` — slot 7 (kind 9) is currently unused; we now use it. Slot 0 of `HOT_CUE_KINDS` index goes to pad A.
- `_cue_layout_is_current` in `audio_features.py` — its required/optional dicts gate cache regeneration. We rewrite this function rather than keep its current form, because the new layout is too dynamic to encode in a pair of static dicts (see §6 on cache).

---

## 2. Beatgrid: the foundation everything else stands on

Bad cues almost always trace back to a bad beatgrid. Today `librosa.beat.beat_track` is called once and `first_downbeat = beat_times[0]` is taken as the anchor for the whole track. This silently fails on:

- Tracks with a slow intro and a tempo jump at the drop (very common in tech-house edits).
- Tracks with `2/4` or `6/8` feel where librosa locks onto half-tempo or double-tempo.
- Tracks where the first detected beat is actually beat 3 of bar 1.

Cue quality cannot exceed beatgrid quality. So before any structural work we harden the beatgrid:

### 2.1 Tempo sanity and octave correction

After `beat_track` returns `tempo`:

- If `tempo < 85`, re-test at `2 * tempo` by running `librosa.beat.plp` (predominant local pulse) and measuring the variance of inter-beat intervals at both candidates. Pick the candidate with lower variance, prefer the result whose median IBI matches the dominant peak in `librosa.feature.tempogram`.
- If `tempo > 175`, mirror — test at `tempo / 2`.
- Persist the chosen tempo *and* the rejected candidate as `bpm_alternates: list[float]` so the TUI can offer a one-keystroke override.

### 2.2 Downbeat selection (not just first beat)

`beat_track` returns beats, not downbeats. To find downbeat 1 we score each of the four candidate beat-1 phases by:

- Onset strength at that phase, summed over the first 32 bars (downbeat-1 should be the strongest of the four).
- Beat-synced low-band energy (kick frequency, sub-200 Hz) — kicks land on 1.
- Chroma stability across bars at that phase — beat-1 alignment yields more repetitive chroma frames.

We pick the phase with the highest combined score. This replaces `first_downbeat = beat_times[0]`.

### 2.3 Tempo drift detection (constant-tempo guarantee)

For every track:

- Compute `librosa.beat.plp` pulse curve over the analysis window.
- Find the median local tempo and the standard deviation.
- If `std/median > 0.04` (drift > 4%), set `tempo_stable = False` and mark all structural cues with reduced confidence; the comment field includes `tempo: variable`.

A non-stable tempo isn't a hard failure — we still emit the bar-counted fallback cues — but we never claim "Drop" on a variable-tempo track. Confidently-placed structural cues require a stable grid.

### 2.4 Data model

```python
# new fields on TrackFeatures (all default-able):
bpm_alternates: list[float] = field(default_factory=list)
tempo_stable: bool = True
beat_phase_confidence: float = 0.0   # 0..1 score of the chosen downbeat phase
```

### 2.5 Tests

`test_audio_features.py`:

- Half-tempo trap: synthetic 70 BPM dub played at notional 140 BPM. Assert chosen `bpm == 140` and `70.0 in bpm_alternates`.
- Drift: linearly varying tempo over 60 s. Assert `tempo_stable == False`.
- Downbeat phase: signal with kicks on beat 1 only. Assert `first_downbeat_sec` lands on a kick within ±15 ms.

This work is invisible to the user when it succeeds — and it's the difference between cues that snap to the grid in Rekordbox vs. cues that show as red triangles offset from the bar.

---

## 3. Loop selector: harden the existing scorer

The existing `_loop_candidate_score` is already a careful piece of code (groove / boundary / energy stability / clean transition / placement / downbeat / timbre, with a transient penalty). The principle holds; we are not rewriting it. We are **tightening it** in three specific ways because loop cues are the area with the most user-visible failures today.

### 3.1 Vocal exclusion (hard constraint, not a soft penalty)

Today there is no vocal awareness in loop selection. Looping over a vocal phrase is *audibly broken* — DJs do not do it deliberately. So vocal overlap is a hard reject for `Intro Loop`, not a score penalty:

- Compute `vocals: list[VocalInterval]` (see §5 for the detector).
- In `_best_intro_loop_candidate`, before scoring: for each candidate window, if **any** vocal interval with `confidence ≥ 0.6` overlaps the window by more than 25% of its length, reject the candidate.
- For `Exit Loop` the rule is softer: a 20% overlap is allowed, since outro vocals are often a tail-out the DJ may want to ride into the next track. Score penalty: `-0.18 * overlap_fraction`.

### 3.2 Structural awareness

Today the search window for `Intro Loop` is `[first_downbeat + 4 bars, min(96 bars, 120s, ...)]`. Once we have sections (§4):

- If structural confidence is high, restrict the intro loop search to the *labeled intro section* — extended by up to 4 bars into the next section if no good candidate exists in the intro proper.
- Restrict the exit loop search to the *labeled outro section*, similarly extended into the preceding section by up to 4 bars.
- If structural confidence is low, fall back to the existing bar-counted window. This is the same low-conf → conservative pattern we use everywhere.

This eliminates the failure mode where an "Intro Loop" lands on the first build because the build was rhythmically clean.

### 3.3 Two-pass scoring with re-rank

Currently we collect all candidates above the acceptance threshold and pick the highest score. This biases toward over-confident peaks in score space. We add a second pass:

- From the candidate list, take the top 8 by raw score.
- Re-rank them by a *robustness* metric: average score over the candidate ±1 beat (the loop should be a *plateau* in score space, not a single spike).
- The chosen candidate is the top of the re-ranked list.

A spike-only candidate is usually a coincidence in the scoring function; a plateau candidate is a real loop region. This is one of the cheapest, highest-impact changes in the whole plan.

### 3.4 Loop-end snapping

Today the loop end is derived as `start + beats * beat`. Because `beat = bar/4` is itself derived from `bpm`, accumulated error over 8 beats can drift up to ±20 ms. We re-snap the computed end to the nearest detected beat from `beat_track`, within a ±50 ms window. This is the simplest fix for the "loop doesn't quite resync" complaint.

### 3.5 Acceptance threshold raised

Current thresholds: `0.68` for 8-beat loops, `0.62` for 4-beat. With the additions above (vocal hard reject, structural restriction, plateau re-rank) the raw distribution of accepted scores shifts upward. We raise to `0.74` / `0.68` respectively. **Net effect: fewer accepted loops, but the ones we keep are noticeably better.** Empty pad D is better than a wrong loop on pad D.

### 3.6 Tests

`test_audio_features.py`:

- Plateau vs. spike: synthetic feature streams where two candidates have equal peak score but different score-curvature; assert the plateau candidate is picked.
- Vocal hard reject: candidate that would otherwise win, with a high-confidence vocal interval covering 40% of it — assert it is *not* in the candidate list at all (rejected pre-scoring).
- End-snap: assert the chosen `end_seconds` lies within 15 ms of a beat time from `librosa.beat.beat_track`.
- Threshold ablation: store a small fixture of analyzer outputs and pin the count of accepted loops at the new threshold — regressions in scoring will move that count.

---

## 4. Phrase-aware structural segmentation

This is the highest-leverage *new* capability for cue quality. It is the difference between "auto cues" and "useful auto cues."

### 4.1 Algorithm

We use librosa's repetition-based segmentation, which is the right tool for this music: house, tech-house, techno, melodic / progressive. It does not require a trained model and is robust to mix variations.

Pipeline (operating on the shared decode from §7):

1. Beat-synchronous CQT + MFCC features.
2. Recurrence: `R = librosa.segment.recurrence_matrix(features_sync, mode='affinity', sym=True)`.
3. Path enhance: `Rf = librosa.segment.path_enhance(R, n=15)`.
4. Laplacian: convert to a graph Laplacian, compute the first `k` eigenvectors via `scipy.sparse.linalg.eigsh`.
5. KMeans cluster the eigenvectors into `k_candidate` labels for each `k ∈ {3,4,5,6,7}`.
6. Pick the `k` that maximizes silhouette score, subject to producing between 3 and 9 segments.
7. Boundary times = beat times where the cluster label changes.
8. Snap each boundary to the nearest downbeat from §2. Reject any boundary that doesn't snap within ±1 beat.

### 4.2 Labeling (heuristic, well-understood)

We do **not** label every section. We label only the ones that map onto cue slots:

- **Intro**: the section beginning at t ≈ 0.
- **Outro**: the section ending at t ≈ duration.
- **Drop**: the section maximizing `mean_rms * mean_onset_density * mean_centroid_normalized`. Tiebreak: earlier. Must score > 1.4 × second-best to be labeled `Drop`; otherwise no `Drop` label, only `Phrase 32` fallback.
- **Breakdown**: the lowest-energy section between intro and drop, OR between drop and outro. Must be at least 8 bars long.
- **Build**: a section immediately preceding `Drop` whose energy slope across its bars is monotonically positive.
- **Last Drop**: the second-highest-energy section, if it occurs after the first `Drop` and is itself > 1.2 × the median section energy.

Everything else is labeled `section` and contributes only to the section count / structure visualization, not to cue placement.

### 4.3 Confidence model

A single `segmentation_confidence ∈ [0, 1]` summarizes:

- `boundary_snap_quality` (median over labeled boundaries): 1.0 within ½ beat, decaying to 0 at 2 beats.
- `drop_dominance`: `(drop_score / second_best_score - 1.0)` clipped to `[0, 1]`.
- `coverage`: 1.0 if 3 ≤ section_count ≤ 9, else 0.
- `tempo_stable` (from §2): hard multiplier — 1.0 if stable, 0.5 if not.

Combined: `0.35 * snap + 0.35 * drop_dominance + 0.20 * coverage_score + 0.10 * fixed_bias`, then `* tempo_stable_multiplier`.

**Threshold for using structural labels: `0.65`.** Below this, the fallback names (`Phrase 16`, `Phrase 32`) are written instead and `segmentation_mode = "heuristic"`. The cue *positions* may still be informed by structure when boundaries snap cleanly, but the labels are conservative.

### 4.4 Data model

```python
@dataclass(frozen=True)
class Section:
    start_sec: float
    end_sec: float
    label: str   # "intro" | "build" | "drop" | "breakdown" | "last_drop" | "outro" | "section"
    confidence: float

# new on TrackFeatures:
sections: list[Section] = field(default_factory=list)
segmentation_confidence: float = 0.0
segmentation_mode: str = "heuristic"  # "structural" | "heuristic"
```

### 4.5 Cue placement (the actual point of this section)

In `_cue_hints`, after computing structure:

```python
if segmentation_mode == "structural":
    pad_B = build_section.start_sec if build_section else intro.start_sec + bar*16
    pad_C = drop_section.start_sec
    pad_F = breakdown_section.start_sec if breakdown_section else None
    pad_G = last_drop_section.start_sec if last_drop_section else None
    outro_memory = outro_section.start_sec
    name_B = "Build" if build_section else "Phrase 16"
    name_C = "Drop"
else:
    pad_B = first_downbeat + bar*16
    pad_C = first_downbeat + bar*32
    pad_F = energy_trough_sec  # from §5, only if it passes thresholds
    pad_G = None
    outro_memory = duration - bar*32
    name_B = "Phrase 16"
    name_C = "Phrase 32"
```

Every emitted second-value is then `_snap_down_to_bar`'d to the beatgrid. **No cue is ever emitted off-grid.** This is enforced by an assertion in the test suite.

### 4.6 Tests

`test_audio_features.py`:

- Synthetic four-section track at distinct energies. Assert 4 sections, correct labels, `segmentation_mode == "structural"`.
- Single-section drone. Assert `segmentation_mode == "heuristic"`, Phrase 16/32 cues present at exactly bar*16 and bar*32.
- Boundary grid: for every section, assert `start_sec` lies on a downbeat within ±½ beat.
- Confidence floor: a track with ambiguous drop dominance (1.05× ratio) → assert `segmentation_mode == "heuristic"` even though sections were detected.
- Drop slot consistency: structural-mode track and heuristic-mode track both produce a cue on hot slot 2 (pad C). The *name* differs; the *slot* does not.

`test_rekordbox_sync.py`:

- All new auto-cue names (`Build`, `Drop`, `Breakdown`, `Last Drop`) round-trip through `AUTO_CUE_NAMES` so re-push removes them cleanly.
- Slot 7 (kind 9) tracks: re-push of an externally-edited track does NOT overwrite a user's slot 7 cue, because we only write to slots 5–7 on tracks whose comment starts with `soundcloud-dl |`.

---

## 5. Energy curve + Breakdown/Drop fallback

The energy curve is **secondary** to structural segmentation for cue placement (structural wins when available), but **primary** for the heuristic fallback path — and it's the data the TUI needs for visualization.

### 5.1 Curve definition

Computed on a 1.0-second hop over the analysis window:

```
e_rms     = max(0, (dBFS(rms) + 40) / 40)
e_flux    = mean onset_strength in window / 90th-pct of onset_strength
e_density = onset count / max_onsets_per_sec
e_t       = clip(0.50*e_rms + 0.30*e_flux + 0.20*e_density, 0, 1)
```

Downsampled to exactly 256 floats with linear interpolation, 3-decimal precision in the cache JSON.

### 5.2 Heuristic-mode Drop / Breakdown placement

Used only when `segmentation_mode == "heuristic"`. Both are emitted as *memory cues* (kind 0) to avoid clashing with the hot Phrase 16 / Phrase 32 cues on pads B and C.

- **Drop**: argmax of the curve, snapped to nearest downbeat. Requires `max > 0.65` and `max > 1.3 × median`. Otherwise no Drop cue is emitted.
- **Breakdown**: argmin of a smoothed curve (8-bar kernel), snapped to nearest downbeat. Requires `min < 0.35`, position at least 24 bars from start AND 24 bars from end.

If either threshold fails we emit no cue. **An empty memory slot is correct behavior.**

### 5.3 Structural-mode use

In structural mode, the curve is no longer needed for cue placement (sections drive cues). It is kept in the cache anyway because:

- The TUI displays it as a sparkline.
- It is the data we'd use later for harmonic-and-energy-aware playlist ordering.
- Storing it costs ~1.5 KB per track.

### 5.4 Data model

```python
energy_curve: list[float] = field(default_factory=list)  # exactly 256 floats in [0,1]
energy_curve_hz: float = 0.0                              # original sample rate of curve before downsample (informational)
```

### 5.5 Tests

- `len(features.energy_curve) == 256` always.
- All values in `[0.0, 1.0]`.
- Loud → quiet → loud synthetic track: middle third of the curve has values strictly less than first and last thirds (mean comparison).
- Heuristic-mode Drop emitted only when thresholds met (positive and negative cases).

---

## 6. Vocals: intervals, classification, optional stems

In service of cue quality, the vocal detector has **one job that matters**: tell the loop selector where the vocals are, so it never loops over them. Everything else — track tagging, stem export — is bonus.

### 6.1 Default path (always on)

Uses only existing dependencies. For each frame in the shared spectrogram:

- Harmonic component via `librosa.effects.hpss(y)[0]`, then RMS on the harmonic component.
- `librosa.feature.spectral_contrast` averaged across mid bands (400 Hz – 4 kHz).
- Frame is vocal-likely if: `harmonic_rms > median(harmonic_rms) * 1.3` AND `contrast > median(contrast) * 1.2` AND `percussive_ratio < 0.7`.

Then:

- Smooth the binary vocal-likely vector with a 2-second median filter (removes single-frame jitter).
- Merge adjacent intervals separated by < 1 second.
- Discard intervals shorter than 1.5 seconds.
- Per-interval confidence: mean of normalized harmonic_rms over the interval.

### 6.2 Opt-in stronger path (`extract_vocal_stems = true`)

Runs Demucs subprocess (`demucs --two-stems=vocals --segment 30`). The vocal stem's RMS curve IS the vocal-presence curve. We:

- Convert vocal-stem RMS > a threshold (calibrated against quiet-room noise floor) into intervals.
- Save the instrumental stem next to the source as `<filename>.instrumental.mp3` at 320 kbps via ffmpeg.
- Set `instrumental_path` on the features.

Gated by config flag AND demucs-importable. If either is missing, silently fall back to §6.1 and log a status event.

### 6.3 Classification

```python
vocal_coverage = sum(end - start for v in vocals) / duration

if vocal_coverage < 0.05:   vocal_class = "instrumental"
elif vocal_coverage < 0.25: vocal_class = "dub"
else:                       vocal_class = "vocal"
```

The thresholds are intentionally wide so noisy HPSS detection doesn't flip categories. The Rekordbox comment carries `| vocal` / `| dub` / `| instrumental` for collection filtering.

### 6.4 Data model

```python
@dataclass(frozen=True)
class VocalInterval:
    start_sec: float
    end_sec: float
    confidence: float

vocals: list[VocalInterval] = field(default_factory=list)
vocal_class: str = "unknown"
vocal_coverage: float = 0.0
instrumental_path: str = ""
```

### 6.5 Use in loop selector

Already covered in §3.1: hard reject of Intro Loop candidates with > 25% high-confidence vocal overlap; soft penalty (-0.18 × overlap) on Exit Loop.

### 6.6 Tests

- Synthetic signal with harmonic burst → assert one interval covering that range, `vocal_class == "dub"` for partial coverage.
- Loop hard-reject: candidate that would otherwise win the scorer, with a vocal interval covering 40% of it → assert it is not in the candidate list.
- Demucs path: monkeypatch the subprocess to write a tiny fake stem; assert `instrumental_path` is set and the file exists.
- Classification thresholds: parameterized test for coverage values around 0.05 and 0.25 boundaries.

---

## 7. Shared decode and feature pipeline

All three new features (sections, energy curve, vocals) and the existing analyses (key, energy aggregate, loop profile) read the same audio. Decoding once is non-negotiable.

Refactor `_analyze_uncached` so it:

1. Loads audio once: `y, sr = librosa.load(...)` with the existing 180-second cap.
2. Computes a single STFT, single onset envelope, single chroma, single beat-track output. These become an internal `_FeatureBundle` (in-memory only, never serialized).
3. Passes the bundle to:
   - existing key estimator (refactor to accept the precomputed chroma)
   - existing energy aggregate
   - **new** beatgrid hardener (§2)
   - existing loop profile builder (extended to consume vocals)
   - **new** segmenter (§4)
   - **new** energy curve builder (§5)
   - **new** vocal detector (§6)

For tracks > 420 s the existing tail-window decode is retained but only feeds the loop profile (looking for an Exit Loop in the tail). Sections, energy curve, and vocals run on the head window only — which is already the analysis window, so no behavioral regression.

This refactor lands as a *no-behavior-change* PR first, before any of the new features. That gives us a clean baseline.

---

## 8. Cache and versioning

- `ANALYSIS_VERSION: 8` → `9`. One bump, on the final merge.
- `_features_from_json` already raises on mismatch and the caller re-analyzes. Good.
- `_ensure_current_cue_hints` currently regenerates cues from `(duration, bpm, first_downbeat)` only. The new cue layout depends on `sections`, `energy_curve`, `vocals`, and `tempo_stable`. **We delete `_ensure_current_cue_hints` entirely.** Keeping it is dangerous: it would silently produce wrong cues from incomplete cached data. Version-bump-driven full re-analysis is the safe path.
- `_cue_layout_is_current` likewise becomes obsolete; remove it.
- The TUI shows a `Re-analyzing (v8 → v9, this is one-time)` status line on the first run after upgrade so users understand the wait. Existing progress bar handles the rest.

---

## 9. Rekordbox push integrity

The push path is where bad cues become permanent in the user's library. Three hard rules:

### 9.1 Auto-cue allowlist is authoritative

`AUTO_CUE_NAMES` is extended to:

```python
AUTO_CUE_NAMES = {
    "Intro",
    "Phrase 16", "Phrase 32",   # heuristic-mode names
    "Build", "Drop",             # structural-mode names for pads B and C
    "Intro Loop", "Exit Loop",
    "Breakdown", "Last Drop",
    "Outro",
}
```

A test enumerates every possible cue name `_cue_hints` can emit and asserts each is in `AUTO_CUE_NAMES`. New names without allowlist entries cause cue leakage on re-push.

### 9.2 Slot ownership

We only write hot-cue slots 5, 6, 7 (kinds 7, 8, 9) on tracks whose existing Rekordbox `Commnt` starts with `soundcloud-dl |`. For tracks the user has manually added or edited, we limit ourselves to the existing slots 0–4. This is a behavior change — today we *don't* write 5–7 at all, so there's no existing slot 7 to overwrite — but we encode the rule now so a user who later adds their own slot 7 cue on a managed track keeps it.

### 9.3 Idempotency test

A new integration test:

1. Push a feature set.
2. Re-push the same feature set unchanged.
3. Assert: zero cues added, zero cues removed, zero duplicates created. `added_cues + added_loops == 0` AND every cue in the DB is unique by `(Kind, InMsec)`.

This is the single most important test in the suite. If it fails we have shipped a regression that corrupts user libraries on re-runs.

---

## 10. Validation: how we know cues are actually good

Code-level unit tests cannot prove cue quality on real tracks. We add a **calibration suite**:

### 10.1 Calibration corpus

A small set of locally-stored reference tracks (NOT committed to the repo — listed in a manifest at `backend/tests/fixtures/calibration_manifest.json` that points to local paths the developer maintains). Each track has a hand-labeled ground truth:

```json
{
  "path": "~/Music/refs/jenny.mp3",
  "expected": {
    "bpm": 124,
    "first_downbeat_sec": 0.48,
    "drop_sec": 134.5,
    "breakdown_sec": 94.2,
    "intro_end_sec": 32.0,
    "outro_start_sec": 286.0,
    "vocal_class": "dub"
  }
}
```

### 10.2 `soundcloud-dl calibrate` subcommand

New bridge subcommand `calibrate` runs the analyzer over every manifest entry and reports:

- BPM error (Hz).
- Downbeat error (ms).
- Drop placement error (bars off — half a bar = pass, two bars off = fail).
- Confidence-mode correctness (did we choose `structural` when ground truth has clean structure?).

Output is a single table; a developer running this before a PR sees if their change broke any reference track.

### 10.3 Acceptance gate for cue work

No PR that touches `_cue_hints`, `_loop_candidate_score`, segmentation, beatgrid hardening, or vocal detection may merge if the calibration suite regresses on any reference track. This is enforced by convention (CI hook is overkill for a single-developer tool), but it is documented in `AGENTS.md`.

This is the discipline that turns "auto cues" into "top-tier auto cues."

---

## 11. TUI surface (minimum needed for the cue work)

Cue quality is the product, so the TUI must let the user **see what was decided** and **fix it fast** when they disagree:

### 11.1 Analyze list screen

Per row: title, BPM, key, energy badge, 32-char sparkline, `V`/`D`/`I` vocal badge, `S` if structural mode, `H` if heuristic mode.

### 11.2 Detail pane on Enter

For the focused track:

- Full-width 256-point sparkline with vertical markers at each cue's timestamp.
- Section bands (colored ribbons under the sparkline) when in structural mode.
- Vocal intervals (light overlay).
- Cue list: name, hot slot, time, confidence. Cue list is editable: arrow keys to a cue, `[`/`]` to nudge by half a beat, `d` to delete, `enter` to confirm.
- BPM override: press `b`, type the override (or pick from `bpm_alternates`), re-analyze with the override locked.

### 11.3 Settings additions

- "Extract vocal stems (slow, requires demucs)" toggle, default off.
- "Cue confidence threshold" advanced setting (default 0.65 from §4.3). Hidden behind an "Advanced" submenu.

The TUI changes are scoped tightly. Nice-to-haves like waveform rendering, live preview playback, or saved overrides per track are explicit non-goals here.

---

## 12. Implementation order

Each step is a self-contained PR. Each merges only when its tests *and* the calibration suite pass.

1. **Pipeline refactor.** Extract `_FeatureBundle`, decode-once, no behavior change. Existing tests stay green.
2. **Beatgrid hardening (§2).** Tempo octave correction, downbeat phase selection, drift detection. The biggest single quality jump and a prerequisite for everything below.
3. **Loop selector hardening (§3).** Plateau re-rank, end-snap, threshold raise. Self-contained.
4. **Energy curve (§5).** Additive; enables sparkline.
5. **Vocal detector — HPSS path (§6.1, §6.3).** Wire vocal-hard-reject into the loop selector (§3.1). This is when loop quality on vocal-heavy tracks visibly improves.
6. **Structural segmentation (§4).** The biggest *new* feature for cue quality. Bumps the cue layout names. Calibration suite earns its keep here.
7. **Demucs stem export (§6.2).** Optional, gated, last.
8. **TUI sparkline + detail pane (§11).** Lands last so the demo lines up with everything underneath working.

`ANALYSIS_VERSION` is bumped exactly once, at the merge of step 6 (or as late as step 7 if 7 ships in the same release window).

---

## 13. Explicit non-goals

- **No machine-learned segmenter.** msaf / madmom / pretrained models are out of scope. They add heavy deps for marginal gains on this music. Revisit only if calibration shows librosa segmentation failing on a class of tracks.
- **No waveform written to Rekordbox.** Rekordbox regenerates its own waveform on load. Writing the binary blob via pyrekordbox is brittle.
- **No track-to-track similarity / recommendation.** Different feature, different document.
- **No per-section color tagging in Rekordbox.** The comment string carries the structure summary; that's enough.
- **No live preview playback in the TUI.** Wanting it is reasonable but it's a much larger surface (audio output, ffplay process management, beat-synced playback) and it does not improve the cues that ship.
- **No multi-anchor beatgrid.** We detect drift and flag it; we still write a single tempo to Rekordbox. Multi-anchor grids are a separate, much larger workstream.

---

## 14. Summary: why this is top-tier

1. The cue layout is a frozen, named contract (§1). Users build muscle memory.
2. Beatgrid is hardened *before* anything else (§2). Bad cues come from bad grids.
3. The loop scorer that already does careful work gets three targeted fixes that eliminate its visible failure modes (§3).
4. Real structural segmentation replaces fixed bar offsets *only when confidence is high* (§4). Otherwise we fall back, conservatively, to the existing behavior.
5. Vocal awareness becomes a hard constraint on loop selection, not a nice-to-have (§3.1, §6).
6. A calibration suite on real reference tracks prevents quiet regressions (§10).
7. Rekordbox push idempotency is enforced by test, not by hope (§9.3).

Every other capability in the document — energy curve, stems, sparkline, classification — exists to make the cues better or to make their quality visible. None of them is the headline. The headline is: **cues that are right.**
