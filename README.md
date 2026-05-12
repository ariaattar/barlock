# barlock

DJ music analysis tool — bar-accurate beat grid detection, seamless per-bar looping on two decks, Pioneer Rekordbox XML hot-cue export, and a built-in browser for your Rekordbox library.

See [`spec.md`](./spec.md) for the design.

## What it does

- **Beat-grid analysis** via [`beat-this`](https://github.com/CPJKU/beat_this) CRNN — BPM and downbeats accurate to ~25 ms on real music.
- **Two decks** with stacked CDJ-style waveforms, scroll/pinch/Cmd-scroll to zoom (2 s ↔ 128 s window), bar numbers above each downbeat.
- **Seamless looping** — playback runs through `AudioBufferSourceNode` with native `loop=true`/`loopStart`/`loopEnd`. Loop wraps inside the audio hardware, not a JS timer, so there's no click.
- **Beat sync** — match Deck B's tempo (rate-shift) and bar phase to Deck A in one click.
- **Hot cue pads** (8 slots, A–H) snapped to nearest downbeat. Persisted server-side.
- **Library sidebar** — reads your real Rekordbox `master.db` (playlists, BPMs, file paths) and lets you browse `~/Music`, `~/Downloads`, etc. One click loads a track from disk into a deck — no upload.
- **Rekordbox XML export** — `TRACK` / `TEMPO` / `POSITION_MARK` matching Pioneer's spec, importable via *File → Import → Library*.

## Run

**Backend** (port 8765):
```bash
cd backend
uv sync
uv run uvicorn app.main:app --port 8765 --reload
```

**Frontend** (port 5173):
```bash
cd frontend
npm install
npm run dev
```

Open <http://localhost:5173>.

## Verify

```bash
cd backend
uv run pytest -v
```

5 tests cover:
- BPM detection within ±0.5 on a 120 BPM click track
- First downbeat within ±50 ms
- All bar gaps are 2.0 ± 0.05 s
- Full HTTP flow: upload → analyze → cues → Rekordbox XML round-trip

## Stack

- **Backend**: Python 3.10, FastAPI, [`beat-this`](https://github.com/CPJKU/beat_this), [`pyrekordbox`](https://github.com/dylanljones/pyrekordbox), librosa+soundfile (with macOS CoreAudio fallback for m4a/aac), uv.
- **Frontend**: Vite + React + TypeScript, Tailwind v4, shadcn-style components, wavesurfer.js v7 (display only), custom WebAudio playback engine.

## Notes

- Playback engine is a hand-rolled `WebAudioPlayer` using `AudioBufferSourceNode` so loops are sample-accurate — wavesurfer's HTMLAudio path has an audible click on every loop wrap.
- The Rekordbox sidebar reads `~/Library/Pioneer/rekordbox/master.db` via pyrekordbox (read-only). Works while Rekordbox is running (you'll see a warning in the backend log, that's expected).
- m4a / aac files are transcoded transparently via librosa+audioread (CoreAudio backend on macOS). No system ffmpeg required.
- Exported hot cues round-trip position and slot perfectly; CDJ pad color is whatever Rekordbox's default palette assigns per slot (pyrekordbox 0.4 doesn't expose `Red/Green/Blue` on marks).
