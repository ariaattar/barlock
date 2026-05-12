"""Generate a 4/4 click-track WAV at a known BPM.

Used as a ground-truth fixture for analyzer tests.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import soundfile as sf


def make_click_track(
    out_path: Path,
    bpm: float = 120.0,
    bars: int = 16,
    sample_rate: int = 44100,
    beats_per_bar: int = 4,
    first_downbeat_offset_sec: float = 0.0,
) -> dict:
    seconds_per_beat = 60.0 / bpm
    total_beats = bars * beats_per_bar
    duration = first_downbeat_offset_sec + total_beats * seconds_per_beat + 0.5
    n_samples = int(duration * sample_rate)
    audio = np.zeros(n_samples, dtype=np.float32)

    beat_times = []
    downbeat_times = []
    for b in range(total_beats):
        t = first_downbeat_offset_sec + b * seconds_per_beat
        beat_times.append(t)
        if b % beats_per_bar == 0:
            downbeat_times.append(t)
        is_downbeat = (b % beats_per_bar == 0)
        # A short tonal click — a sine burst with an exponential envelope.
        # Downbeats are higher pitched and louder so the model can lock on
        # the bar grid cleanly.
        freq = 1500.0 if is_downbeat else 800.0
        amp = 0.9 if is_downbeat else 0.55
        click_len = int(0.04 * sample_rate)
        idx0 = int(t * sample_rate)
        idx1 = min(n_samples, idx0 + click_len)
        n = idx1 - idx0
        if n <= 0:
            continue
        env = np.exp(-np.linspace(0, 5.0, n))
        sine = np.sin(2 * np.pi * freq * np.arange(n) / sample_rate)
        audio[idx0:idx1] += (amp * env * sine).astype(np.float32)

    sf.write(str(out_path), audio, sample_rate, subtype="PCM_16")
    return {
        "bpm": bpm,
        "beat_times": beat_times,
        "downbeat_times": downbeat_times,
        "duration_sec": duration,
        "sample_rate": sample_rate,
    }


if __name__ == "__main__":
    out = Path(__file__).parent / "fixtures" / "click_120.wav"
    out.parent.mkdir(parents=True, exist_ok=True)
    info = make_click_track(out, bpm=120.0, bars=16, first_downbeat_offset_sec=0.25)
    print(f"wrote {out}: {info['duration_sec']:.2f}s, {len(info['beat_times'])} beats")
