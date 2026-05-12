from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import numpy as np
import soundfile as sf

from .models import Analysis

logger = logging.getLogger("mixer.analysis")

_FILE2BEATS = None


def _get_file2beats():
    global _FILE2BEATS
    if _FILE2BEATS is None:
        from beat_this.inference import File2Beats

        _FILE2BEATS = File2Beats(checkpoint_path="final0", device="cpu", dbn=False)
    return _FILE2BEATS


def _ensure_decodable(path: Path) -> Path:
    """Return a path that all downstream tools (soundfile, torchaudio) can
    read. If `path` is m4a / aac / wma / mp4 / mov etc., transcode through
    librosa/audioread (CoreAudio on macOS) into a sibling WAV file.

    soundfile alone can't read m4a; torchaudio needs ffmpeg or sox. librosa
    via audioread can use CoreAudio's ExtAudioFile on macOS, so it handles
    these formats with no system dependency.
    """
    try:
        sf.info(str(path))
        return path
    except Exception:
        pass

    logger.info("transcoding %s -> wav via librosa/audioread", path.name)
    import librosa

    y, sr = librosa.load(str(path), sr=None, mono=False)
    if y.ndim == 1:
        y = y[np.newaxis, :]
    wav_path = path.with_suffix(".decoded.wav")
    sf.write(str(wav_path), y.T, int(sr), subtype="PCM_16")
    return wav_path


def _audio_info(path: Path) -> tuple[float, int]:
    info = sf.info(str(path))
    return float(info.duration), int(info.samplerate)


def _median_bpm(beats: np.ndarray) -> float:
    if len(beats) < 2:
        return 0.0
    diffs = np.diff(beats)
    diffs = diffs[diffs > 0]
    if len(diffs) == 0:
        return 0.0
    return float(60.0 / np.median(diffs))


def _bar_grid_4_4(beats: np.ndarray, first_downbeat: float) -> np.ndarray:
    """Snap raw downbeat predictions to a clean 4/4 grid.

    Why: beat_this's per-frame downbeat predictions can include noisy
    every-other-beat hits on sparse/synthetic audio. For Rekordbox export and
    bar-loop UI we need a strictly 4-beats-per-bar grid anchored at the first
    downbeat. We take every 4th detected beat starting from the index closest
    to the predicted first downbeat.
    """
    if len(beats) == 0:
        return np.array([], dtype=float)
    start_idx = int(np.argmin(np.abs(beats - first_downbeat)))
    return beats[start_idx::4]


def analyze(track_id: str, audio_file: Path, original_filename: Optional[str] = None) -> Analysis:
    decodable = _ensure_decodable(audio_file)
    duration, sr = _audio_info(decodable)
    f2b = _get_file2beats()
    beats, downbeats = f2b(str(decodable))

    beats = np.asarray(beats, dtype=float)
    downbeats = np.asarray(downbeats, dtype=float)

    bpm = _median_bpm(beats)
    raw_first_db = (
        float(downbeats[0]) if len(downbeats)
        else float(beats[0]) if len(beats)
        else 0.0
    )
    bar_grid = _bar_grid_4_4(beats, raw_first_db)
    first_db = float(bar_grid[0]) if len(bar_grid) else raw_first_db

    return Analysis(
        track_id=track_id,
        filename=original_filename or audio_file.name,
        duration_sec=duration,
        sample_rate=sr,
        bpm=round(bpm, 3),
        beats=[round(float(t), 4) for t in beats],
        downbeats=[round(float(t), 4) for t in bar_grid],
        time_signature="4/4",
        first_downbeat_sec=round(first_db, 4),
    )
