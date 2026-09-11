> Historical proposal. This document is retained for context; see the root README and current architecture for maintained behavior.

# Mixer — DJ Music Analysis Tool

A two-deck DJ-style analysis app for tracks, bar-accurate looping, and Pioneer/Rekordbox-compatible cue export.

## Goals

1. **Accurate beat & bar analysis** matching Rekordbox's beatgrid as closely as possible (same first-downbeat, same BPM, same 4/4 phrasing).
2. **Per-bar looping** on 2 decks with adjustable loop length (1, 2, 4, 8, 16, 32 bars; ½ and ¼ bar fractions).
3. **Hot cue authoring** that exports to Pioneer Rekordbox XML so cues round-trip to CDJ-3000 / XDJ hardware via the standard Rekordbox import flow.

Non-goals (v1): mixing/crossfading, effects, key/tonality detection, sync between decks, real-time pitch shift, library management.

---

## Architecture

```
┌──────────────────────────────┐        ┌──────────────────────────────┐
│ React + Vite + shadcn/ui     │  HTTP  │ FastAPI (Python 3.10)        │
│ - Two-deck waveform UI       │ ─────▶ │ - /api/analyze (upload)      │
│ - Bar markers & loop region  │  WS    │ - /api/decks/:id/...         │
│ - Hot cue pads               │ ─────▶ │ - /api/export/rekordbox      │
│ - <audio> element playback   │        │                              │
│   driven by analysis JSON    │        │ Analysis: beat_this (CRNN)   │
└──────────────────────────────┘        │ Audio I/O: soundfile/librosa │
                                        │ XML: pyrekordbox             │
                                        │ Storage: ./data/{track_id}/  │
                                        └──────────────────────────────┘
```

**Why this split?** Audio playback lives in the browser via the HTML5 `<audio>` element fed from a backend-served static file. Loop logic is computed on the client from the bar-grid JSON (the backend has already done the hard work of finding downbeat positions). This avoids streaming raw PCM over the wire and keeps the backend stateless for playback.

The backend's role:
- Accept audio uploads and analyze them
- Persist analysis results
- Serve the analyzed file back for `<audio>` playback
- Export a Rekordbox-compatible XML for the user's tracks + hot cues

---

## Library choices

| Concern | Library | Why |
|---|---|---|
| Beat & downbeat tracking | **`beat-this`** (CPJKU) | State-of-the-art 2024 CRNN; trained on broad genre set; works on modern Python without `madmom`. Outputs beats *and* downbeats — exactly what's needed for bar gridlines. |
| Audio decode | `soundfile` + `librosa.load` | Robust formats; resampling for analysis. |
| Web framework | `FastAPI` + `uvicorn` | Async, type-hinted, fast iteration. |
| Rekordbox XML | `pyrekordbox` | Official-ish community lib that writes Pioneer's XML (`TRACK`/`TEMPO`/`POSITION_MARK`) with the right attribute conventions. |
| Frontend | `Vite` + `React` + `TypeScript` + `Tailwind` + `shadcn/ui` | Clean, accessible components without a heavy UI kit. |
| Waveform render | `wavesurfer.js` v7 + custom React wrapper | Battle-tested waveform with region/markers; we overlay our own bar lines. |

We do **not** use `madmom` directly — it's stuck on Python ≤3.9. `beat-this` runs inference without it.

---

## Data model

### Track analysis (`./data/{track_id}/analysis.json`)
```jsonc
{
  "track_id": "uuid",
  "filename": "song.mp3",
  "duration_sec": 234.56,
  "sample_rate": 44100,
  "bpm": 124.03,                  // median inter-beat BPM
  "beats": [0.412, 0.895, 1.378, ...],          // seconds
  "downbeats": [0.412, 2.345, 4.279, ...],      // seconds; bar starts
  "time_signature": "4/4",
  "first_downbeat_sec": 0.412
}
```

### Hot cue (`./data/{track_id}/cues.json`)
```jsonc
{
  "cues": [
    {"slot": 0, "name": "INTRO", "position_sec": 0.412, "color": [40, 226, 20], "type": "hot"},
    {"slot": 1, "name": "DROP",  "position_sec": 64.5,  "color": [255, 100, 0], "type": "hot"},
    {"slot": null, "name": "BREAK", "position_sec": 128.0, "color": [200,200,0], "type": "memory"}
  ]
}
```

Hot cue slots map to Pioneer pad indices: 0=A, 1=B, … up to 7=H (CDJ-3000 supports 8). `slot: null` = memory cue.

---

## REST API

All endpoints return JSON. Audio files are served from `/files/{track_id}`.

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/analyze` | Multipart upload of an audio file; returns `track_id` and full analysis. |
| `GET`  | `/api/tracks` | List analyzed tracks. |
| `GET`  | `/api/tracks/{track_id}` | Get analysis JSON. |
| `GET`  | `/files/{track_id}` | Raw audio file (for `<audio>` element). |
| `GET`  | `/api/tracks/{track_id}/cues` | Get cues. |
| `PUT`  | `/api/tracks/{track_id}/cues` | Replace cues. |
| `POST` | `/api/export/rekordbox` | Body: list of `track_id`s. Returns a Rekordbox XML file. |
| `DELETE` | `/api/tracks/{track_id}` | Remove track + analysis. |

Deck/loop state lives entirely in the frontend — the backend has no notion of "which deck is playing what." This keeps the backend stateless and trivially restart-safe.

---

## Per-bar loop semantics (frontend)

Given downbeats `D = [d0, d1, …, dN]`:

- **Bar `i`** spans `[D[i], D[i+1])`.
- **Loop of `k` bars starting at bar `i`** spans `[D[i], D[i+k])`.
- **Half/quarter bars** are linearly interpolated from the neighboring beats (still bar-aligned in start, sub-bar in length).
- Playback loop is implemented by listening to `<audio>`'s `timeupdate` and seeking back to `loop.start` when `currentTime >= loop.end`. We pre-roll the loop region in `wavesurfer` for visualization.

UI controls per deck:
- `+ / – bars` buttons cycle through `[1/4, 1/2, 1, 2, 4, 8, 16, 32]`.
- Loop on/off toggle.
- Hot cue pads (8 slots) that snap to nearest downbeat by default (toggle to disable snap).

---

## Rekordbox XML export (the compatibility-critical part)

Pioneer's spec (from `xml_format_list.pdf` and confirmed via pyrekordbox's docs):

**`<TRACK>`** — one per track. Required attributes for CDJ playback:
- `TrackID` (any unique int), `Name`, `Artist`, `Location` (file:// URI, URL-encoded),
- `TotalTime` (seconds, int), `AverageBpm` (decimal),
- `SampleRate`, `BitRate`, `Kind` (e.g. "MP3 File").

**`<TEMPO>`** — beat grid; one per tempo change. For a constant-tempo track we emit a single entry:
- `Inizio` = seconds of first downbeat
- `Bpm` = computed BPM
- `Metro` = "4/4"
- `Battito` = 1 (first beat of measure)

**`<POSITION_MARK>`** — cues and loops:
- `Type` = `"0"` cue, `"4"` loop
- `Start` = seconds; `End` = seconds (loops only)
- `Num` = `0..7` for hot cues A–H, `-1` for memory cues
- `Red`/`Green`/`Blue` = 0–255 (hot cues display this color on CDJ pads)
- `Name` = label

**CDJ-3000 hot cue colors** — Pioneer maps 24-bit RGB to its closest pad color internally; any RGB value works, just expect snapping. We default cue colors to Rekordbox's standard 8-color palette.

We write to USB-importable XML, *not* directly to the master.db, so users import into Rekordbox via *File → Import → Library*, which then syncs to USB / CDJ.

---

## Verification plan

### Backend (build-and-prove-it-works)
1. **Unit**: pytest on the analyzer with a 4/4 click-track WAV at exactly 120 BPM — expect detected BPM within ±0.5, first downbeat within 25 ms, all downbeats within 30 ms.
2. **Integration**: end-to-end `curl` script:
   - Upload `tests/fixtures/click_120.wav` → get `track_id`.
   - GET analysis → assert BPM, downbeat count.
   - PUT cues → GET back.
   - POST export → write XML, parse with `pyrekordbox.RekordboxXml` and re-read cues.
3. **Rekordbox round-trip**: open the exported XML in Rekordbox 7 → confirm beatgrid lines up and hot cues appear on the correct beats. (Manual; documented in `docs/manual-verification.md`.)

### Frontend
Tested in the browser:
- Load a deck → see waveform with bar lines on every downbeat.
- Toggle bar-loop length → loop region resizes to span correct # of bars.
- Play → audio loops exactly at bar boundary (audible click test with metronome track).
- Press hot cue pad → playhead jumps to stored position; persist via PUT; reload page → cues remain.

---

## Directory layout

```
mixer/
├── spec.md                    # this doc
├── backend/
│   ├── pyproject.toml         # uv-managed
│   ├── app/
│   │   ├── main.py            # FastAPI app
│   │   ├── analysis.py        # beat_this wrapper
│   │   ├── storage.py         # ./data/ persistence
│   │   ├── rekordbox.py       # XML export via pyrekordbox
│   │   └── models.py          # Pydantic schemas
│   └── tests/
│       ├── fixtures/
│       └── test_*.py
├── frontend/
│   ├── package.json
│   ├── src/
│   │   ├── App.tsx
│   │   ├── components/
│   │   │   ├── Deck.tsx
│   │   │   ├── Waveform.tsx
│   │   │   ├── BarLoopControls.tsx
│   │   │   ├── HotCuePads.tsx
│   │   │   └── ui/            # shadcn components
│   │   └── lib/
│   │       └── api.ts
└── data/                      # runtime: per-track folders
```

---

## Implementation order

1. **Backend first** (this PR's first phase): scaffold, `/api/analyze` working end-to-end with `beat_this`, verified via pytest + curl on a known click track. **No frontend until backend is provably correct.**
2. **Rekordbox export**: implement and round-trip via pyrekordbox.
3. **Frontend** scaffold with shadcn, then the deck + waveform + bar loop + cue pads.
4. **Manual Rekordbox verification** with a real track.
