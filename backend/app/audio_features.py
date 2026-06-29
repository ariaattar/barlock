from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
import tempfile
from dataclasses import asdict, dataclass, field, fields, replace
from pathlib import Path
from typing import Iterable

import numpy as np
import soundfile as sf

AUDIO_EXTS = {".mp3", ".wav", ".aiff", ".aif", ".flac", ".m4a", ".aac", ".ogg", ".opus"}
ANALYSIS_VERSION = 9
ENERGY_CURVE_BINS = 256

NOTE_NAMES = ["C", "Db", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]
MAJOR_PROFILE = np.asarray([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
MINOR_PROFILE = np.asarray([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])

CAMELot_MAJOR = {
    "B": "1B",
    "F#": "2B",
    "Db": "3B",
    "Ab": "4B",
    "Eb": "5B",
    "Bb": "6B",
    "F": "7B",
    "C": "8B",
    "G": "9B",
    "D": "10B",
    "A": "11B",
    "E": "12B",
}
CAMELot_MINOR = {
    "Abm": "1A",
    "Ebm": "2A",
    "Bbm": "3A",
    "Fm": "4A",
    "Cm": "5A",
    "Gm": "6A",
    "Dm": "7A",
    "Am": "8A",
    "Em": "9A",
    "Bm": "10A",
    "F#m": "11A",
    "Dbm": "12A",
}


@dataclass(frozen=True)
class CueHint:
    name: str
    seconds: float
    kind: str = "memory"
    hotcue_slot: int | None = None
    end_seconds: float | None = None
    loop_beats: int | None = None


@dataclass(frozen=True)
class Section:
    start_sec: float
    end_sec: float
    label: str  # intro|build|drop|breakdown|last_drop|outro|section
    confidence: float


@dataclass(frozen=True)
class VocalInterval:
    start_sec: float
    end_sec: float
    confidence: float


@dataclass(frozen=True)
class LoopProfile:
    offset_sec: float
    sample_rate: int
    y: np.ndarray
    rms_times: np.ndarray
    rms: np.ndarray
    feature_times: np.ndarray | None = None
    onset: np.ndarray | None = None
    chroma: np.ndarray | None = None
    centroid: np.ndarray | None = None
    bandwidth: np.ndarray | None = None
    vocals: tuple["VocalInterval", ...] = field(default_factory=tuple)
    beat_times: np.ndarray | None = None


@dataclass(frozen=True)
class TrackFeatures:
    path: str
    title: str
    artist: str
    duration_sec: float
    sample_rate: int
    bpm: float
    musical_key: str
    camelot_key: str
    key_confidence: float
    loudness_dbfs: float
    peak_dbfs: float
    energy: int
    first_downbeat_sec: float
    cue_hints: list[CueHint]
    source_url: str = ""
    source_id: str = ""
    analysis_version: int = ANALYSIS_VERSION
    bpm_alternates: list[float] = field(default_factory=list)
    tempo_stable: bool = True
    beat_phase_confidence: float = 0.0
    sections: list[Section] = field(default_factory=list)
    segmentation_confidence: float = 0.0
    segmentation_mode: str = "heuristic"  # structural|heuristic
    energy_curve: list[float] = field(default_factory=list)
    energy_curve_hz: float = 0.0
    vocals: list[VocalInterval] = field(default_factory=list)
    vocal_class: str = "unknown"
    vocal_coverage: float = 0.0
    instrumental_path: str = ""

    @property
    def rekordbox_key(self) -> str:
        return self.musical_key


def audio_files(folder: Path) -> list[Path]:
    root = folder.expanduser()
    if not root.exists():
        return []
    return sorted(
        path for path in root.rglob("*")
        if path.is_file() and path.suffix.lower() in AUDIO_EXTS
        and not path.name.endswith(".instrumental.mp3")
    )


def analysis_dir(output_dir: Path) -> Path:
    path = output_dir.expanduser() / ".track-analysis"
    path.mkdir(parents=True, exist_ok=True)
    return path


def analyze_paths(paths: Iterable[Path], *, output_dir: Path, use_cache: bool = True) -> list[TrackFeatures]:
    return [analyze_file(path, output_dir=output_dir, use_cache=use_cache) for path in paths]


def analyze_one_worker(args: tuple[str, str, bool, bool]) -> TrackFeatures:
    """Picklable entry point for process-pool workers.

    Args is a tuple of (path, output_dir, use_cache, extract_vocal_stems) — only
    strings/bools so the work item travels cleanly across the process boundary.
    """
    path_str, output_dir_str, use_cache, extract_vocal_stems = args
    return analyze_file(
        Path(path_str),
        output_dir=Path(output_dir_str) if output_dir_str else None,
        use_cache=use_cache,
        extract_vocal_stems=extract_vocal_stems,
    )


def analyze_file(
    path: Path,
    *,
    output_dir: Path | None = None,
    use_cache: bool = True,
    extract_vocal_stems: bool = False,
) -> TrackFeatures:
    path = path.expanduser().resolve()
    cache_root = analysis_dir(output_dir or path.parent)
    cache_file = cache_root / f"{_cache_key(path)}.json"
    if use_cache and cache_file.exists():
        try:
            cached = _features_from_json(cache_file.read_text())
            if cached.analysis_version == ANALYSIS_VERSION:
                return cached
        except Exception:
            pass

    features = _analyze_uncached(path, extract_vocal_stems=extract_vocal_stems)
    cache_file.write_text(json.dumps(_features_to_dict(features), indent=2) + "\n")
    return features


def write_id3_tags(path: Path, features: TrackFeatures) -> None:
    if path.suffix.lower() != ".mp3":
        return

    from mutagen.easyid3 import EasyID3
    from mutagen.id3 import COMM, ID3, ID3NoHeaderError

    try:
        EasyID3.RegisterTextKey("initialkey", "TKEY")
    except ValueError:
        pass

    try:
        tags = EasyID3(str(path))
    except ID3NoHeaderError:
        tags = EasyID3()

    if features.title:
        tags["title"] = features.title
    if features.artist:
        tags["artist"] = features.artist
    if features.bpm > 0:
        tags["bpm"] = str(round(features.bpm, 2))
    if features.camelot_key or features.musical_key:
        tags["initialkey"] = features.camelot_key or features.musical_key
    if features.vocal_class and features.vocal_class != "unknown":
        tags["grouping"] = features.vocal_class.title()
    tags.save(str(path))

    id3 = ID3(str(path))
    id3.delall("COMM:soundcloud-dl:eng")
    comment_parts = [
        f"SoundCloud: {features.source_url}" if features.source_url else "",
        f"Key: {features.musical_key} / {features.camelot_key}".strip(),
        f"BPM: {features.bpm:.2f}" if features.bpm > 0 else "",
        f"Energy: {features.energy}/10",
        f"Vocal: {features.vocal_class}" if features.vocal_class not in {"", "unknown"} else "",
    ]
    id3.add(
        COMM(
            encoding=3,
            lang="eng",
            desc="soundcloud-dl",
            text=" | ".join(part for part in comment_parts if part),
        )
    )
    id3.save(str(path))


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


@dataclass
class _FeatureBundle:
    y: np.ndarray
    sr: int
    duration_full: float
    duration_analysis: float
    hop: int
    times: np.ndarray  # frame times in seconds, in the analysis window
    rms: np.ndarray
    onset: np.ndarray
    chroma: np.ndarray
    centroid: np.ndarray
    bandwidth: np.ndarray
    tempo: float
    beat_times: np.ndarray
    bpm_alternates: list[float]
    tempo_stable: bool
    first_downbeat: float
    beat_phase_confidence: float


def _analyze_uncached(path: Path, *, extract_vocal_stems: bool = False) -> TrackFeatures:
    import librosa

    title, artist = _title_artist_from_filename(path)
    source_id = _source_id_from_filename(path)
    file_info = sf.info(str(path))
    full_duration = float(file_info.duration)
    analysis_duration = min(full_duration, 180.0) if full_duration > 0 else None
    y, sr = librosa.load(str(path), sr=22050, mono=True, duration=analysis_duration)
    if y.size == 0:
        raise ValueError(f"empty audio file: {path}")

    duration = full_duration or float(librosa.get_duration(y=y, sr=sr))
    bundle = _build_feature_bundle(y, sr, duration_full=duration)

    musical_key, confidence = _estimate_key_from_chroma(bundle.chroma)
    camelot = camelot_key(musical_key)
    rms_all = float(np.sqrt(np.mean(np.square(y)))) if y.size else 0.0
    peak = float(np.max(np.abs(y))) if y.size else 0.0
    loudness = _amp_to_db(rms_all)
    peak_db = _amp_to_db(peak)
    centroid_norm = float(np.nanmean(bundle.centroid)) if bundle.centroid.size else 0.0
    energy = _energy_score(bpm=bundle.tempo, loudness_dbfs=loudness, centroid_norm=centroid_norm)

    vocals = _detect_vocals_hpss(y, sr, bundle=bundle)
    vocal_coverage = _vocal_coverage(vocals, bundle.duration_analysis)
    vocal_class = _classify_vocal(vocal_coverage)
    instrumental_path = ""
    if extract_vocal_stems:
        demucs_result = _demucs_extract(path, vocal_class=vocal_class)
        if demucs_result is not None:
            instrumental_path, demucs_vocals = demucs_result
            if demucs_vocals:
                vocals = demucs_vocals
                vocal_coverage = _vocal_coverage(vocals, bundle.duration_analysis)
                vocal_class = _classify_vocal(vocal_coverage)

    loop_profile = _build_loop_profile_from_bundle(
        bundle,
        path=path,
        full_duration=duration,
        vocals=vocals,
    )

    energy_curve = _build_energy_curve(bundle)
    sections, segmentation_confidence, segmentation_mode = _segment_sections(
        bundle,
        energy_curve=energy_curve,
    )

    cues = _cue_hints(
        duration=duration,
        bpm=bundle.tempo,
        first_downbeat=bundle.first_downbeat,
        loop_profile=loop_profile,
        sections=sections,
        segmentation_mode=segmentation_mode,
        energy_curve=energy_curve,
    )

    return TrackFeatures(
        path=str(path),
        title=title,
        artist=artist,
        duration_sec=round(duration, 3),
        sample_rate=int(file_info.samplerate or sr),
        bpm=round(float(bundle.tempo), 3),
        musical_key=musical_key,
        camelot_key=camelot,
        key_confidence=round(confidence, 3),
        loudness_dbfs=round(loudness, 3),
        peak_dbfs=round(peak_db, 3),
        energy=energy,
        first_downbeat_sec=round(bundle.first_downbeat, 3),
        cue_hints=cues,
        source_id=source_id,
        analysis_version=ANALYSIS_VERSION,
        bpm_alternates=[round(x, 3) for x in bundle.bpm_alternates],
        tempo_stable=bundle.tempo_stable,
        beat_phase_confidence=round(bundle.beat_phase_confidence, 3),
        sections=sections,
        segmentation_confidence=round(segmentation_confidence, 3),
        segmentation_mode=segmentation_mode,
        energy_curve=[round(float(v), 3) for v in energy_curve],
        energy_curve_hz=1.0,
        vocals=vocals,
        vocal_class=vocal_class,
        vocal_coverage=round(vocal_coverage, 3),
        instrumental_path=instrumental_path,
    )


def _build_feature_bundle(y: np.ndarray, sr: int, *, duration_full: float) -> _FeatureBundle:
    import librosa

    hop = 512
    frame_length = 2048
    duration_analysis = float(len(y) / sr) if sr > 0 else 0.0

    rms = librosa.feature.rms(y=y, frame_length=frame_length, hop_length=hop)[0]
    rms = _moving_average(np.maximum(rms, 0.0), 9)
    times = librosa.frames_to_time(np.arange(rms.size), sr=sr, hop_length=hop)

    onset = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop)
    onset = _moving_average(np.maximum(onset, 0.0), 5)
    chroma = librosa.feature.chroma_stft(y=y, sr=sr, hop_length=hop)
    chroma = np.maximum(chroma, 0.0)
    chroma = chroma / np.maximum(np.linalg.norm(chroma, axis=0, keepdims=True), 1e-6)
    centroid = librosa.feature.spectral_centroid(y=y, sr=sr, hop_length=hop)[0]
    centroid = np.maximum(centroid / max(sr / 2.0, 1.0), 0.0)
    bandwidth = librosa.feature.spectral_bandwidth(y=y, sr=sr, hop_length=hop)[0]
    bandwidth = np.maximum(bandwidth / max(sr / 2.0, 1.0), 0.0)
    frame_count = min(rms.size, onset.size, chroma.shape[1], centroid.size, bandwidth.size, times.size)
    rms = rms[:frame_count]
    onset = onset[:frame_count]
    chroma = chroma[:, :frame_count]
    centroid = centroid[:frame_count]
    bandwidth = bandwidth[:frame_count]
    times = times[:frame_count]

    tempo_raw, beats_frames = librosa.beat.beat_track(y=y, sr=sr, trim=False)
    tempo_raw = _as_float(tempo_raw)
    beat_times_raw = librosa.frames_to_time(beats_frames, sr=sr, hop_length=512) if len(beats_frames) else np.asarray([], dtype=float)

    tempo, alternates = _correct_tempo_octave(
        y=y,
        sr=sr,
        raw_tempo=tempo_raw,
        beat_times=beat_times_raw,
        onset=onset,
        times=times,
    )
    # If octave was halved/doubled, regenerate beat_times at the corrected tempo.
    if not _close(tempo, tempo_raw, rel=0.02):
        try:
            _, beats_frames = librosa.beat.beat_track(y=y, sr=sr, bpm=float(tempo), trim=False)
            beat_times = librosa.frames_to_time(beats_frames, sr=sr, hop_length=512) if len(beats_frames) else beat_times_raw
        except Exception:
            beat_times = beat_times_raw
    else:
        beat_times = beat_times_raw

    tempo_stable = _check_tempo_stability(onset, times, tempo)
    first_downbeat, phase_conf = _pick_first_downbeat(
        y=y,
        sr=sr,
        beat_times=beat_times,
        onset=onset,
        times=times,
        chroma=chroma,
    )

    return _FeatureBundle(
        y=y,
        sr=sr,
        duration_full=duration_full,
        duration_analysis=duration_analysis,
        hop=hop,
        times=times,
        rms=rms,
        onset=onset,
        chroma=chroma,
        centroid=centroid,
        bandwidth=bandwidth,
        tempo=float(tempo),
        beat_times=beat_times,
        bpm_alternates=alternates,
        tempo_stable=tempo_stable,
        first_downbeat=float(first_downbeat),
        beat_phase_confidence=float(phase_conf),
    )


# ---------------------------------------------------------------------------
# Beatgrid hardening
# ---------------------------------------------------------------------------


def _correct_tempo_octave(
    *,
    y: np.ndarray,
    sr: int,
    raw_tempo: float,
    beat_times: np.ndarray,
    onset: np.ndarray,
    times: np.ndarray,
) -> tuple[float, list[float]]:
    if raw_tempo <= 0:
        return float(raw_tempo), []

    candidates: list[float] = [float(raw_tempo)]
    if raw_tempo < 85.0:
        candidates.append(raw_tempo * 2.0)
    if raw_tempo > 175.0:
        candidates.append(raw_tempo / 2.0)
    # No octave fix needed
    if len(candidates) == 1:
        return float(raw_tempo), []

    scored: list[tuple[float, float]] = []
    for cand in candidates:
        score = _score_tempo_candidate(cand, onset=onset, times=times, beat_times=beat_times)
        scored.append((score, cand))
    scored.sort(reverse=True)
    chosen = scored[0][1]
    alternates = [round(c, 3) for _, c in scored[1:] if not _close(c, chosen, rel=0.02)]
    return float(chosen), alternates


def _score_tempo_candidate(
    tempo: float,
    *,
    onset: np.ndarray,
    times: np.ndarray,
    beat_times: np.ndarray,
) -> float:
    if tempo <= 0 or onset.size == 0 or times.size == 0:
        return 0.0
    beat_period = 60.0 / tempo
    # Use existing beat_times if available; otherwise lay down a grid.
    if beat_times.size >= 4:
        ibis = np.diff(beat_times)
        if ibis.size == 0:
            return 0.0
        expected = beat_period
        # Sometimes detected IBIs are at the wrong octave too; compare both half and double.
        scaled = []
        for factor in (1.0, 2.0, 0.5):
            adj = ibis * factor
            err = np.abs(adj - expected) / max(expected, 1e-6)
            scaled.append(float(np.mean(err < 0.10)))
        match = max(scaled)
    else:
        match = 0.0
    # Onset energy at the tempo grid
    if times.size:
        analysis_start = float(times[0])
        analysis_end = float(times[-1])
        grid = np.arange(analysis_start, analysis_end, beat_period)
        if grid.size > 4:
            indices = np.clip(np.searchsorted(times, grid), 0, onset.size - 1)
            on_grid = float(np.mean(onset[indices]))
            off_grid_idx = np.clip(np.searchsorted(times, grid + beat_period / 2.0), 0, onset.size - 1)
            off_grid = float(np.mean(onset[off_grid_idx]))
            contrast = (on_grid - off_grid) / max(on_grid + off_grid, 1e-6)
        else:
            contrast = 0.0
    else:
        contrast = 0.0
    # Prefer tempos in the dance music sweet spot 100..140
    sweet = max(0.0, 1.0 - abs(tempo - 122.0) / 60.0)
    return 0.55 * match + 0.30 * max(contrast, 0.0) + 0.15 * sweet


def _check_tempo_stability(onset: np.ndarray, times: np.ndarray, tempo: float) -> bool:
    if onset.size < 64 or tempo <= 0:
        return True
    # PLP is expensive; use IBI-style variance via autocorrelation of the onset envelope.
    try:
        import librosa

        plp = librosa.beat.plp(onset_envelope=onset, sr=22050, hop_length=512)
    except Exception:
        return True
    if plp.size < 32:
        return True
    # Local tempo from PLP peaks
    peaks = np.where(np.diff(np.sign(np.diff(plp))) < 0)[0] + 1
    if peaks.size < 8:
        return True
    inter = np.diff(peaks)
    if inter.size == 0 or float(np.median(inter)) <= 1e-6:
        return True
    drift = float(np.std(inter) / np.median(inter))
    return drift < 0.18


def _pick_first_downbeat(
    *,
    y: np.ndarray,
    sr: int,
    beat_times: np.ndarray,
    onset: np.ndarray,
    times: np.ndarray,
    chroma: np.ndarray,
) -> tuple[float, float]:
    if beat_times.size == 0:
        return 0.0, 0.0
    if beat_times.size < 8:
        return float(beat_times[0]), 0.4
    # Low-band energy curve
    try:
        import librosa

        S = np.abs(librosa.stft(y, n_fft=2048, hop_length=512))
        freqs = librosa.fft_frequencies(sr=sr, n_fft=2048)
        low_mask = freqs < 200.0
        low_band = S[low_mask].sum(axis=0)
        low_band = _moving_average(low_band, 3)
        if low_band.size > times.size:
            low_band = low_band[: times.size]
        elif low_band.size < times.size:
            low_band = np.pad(low_band, (0, times.size - low_band.size))
    except Exception:
        low_band = np.zeros_like(onset)

    # 4 candidate phases: beats 0, 1, 2, 3 are beat-1 of the bar
    scores: list[float] = []
    for phase in range(4):
        bar_one_beats = beat_times[phase::4]
        if bar_one_beats.size < 4:
            scores.append(0.0)
            continue
        on_idx = np.clip(np.searchsorted(times, bar_one_beats), 0, max(onset.size - 1, 0))
        on_strength = float(np.mean(onset[on_idx]))
        low_strength = float(np.mean(low_band[on_idx]))
        # Chroma stability across bar-1 beats — beat-1 alignment yields more repetitive chroma
        chroma_vecs = []
        for idx in on_idx[:8]:
            if idx < chroma.shape[1]:
                chroma_vecs.append(chroma[:, idx])
        if len(chroma_vecs) >= 2:
            avg = np.mean(chroma_vecs, axis=0)
            sims = [float(np.dot(v, avg) / max(np.linalg.norm(v) * np.linalg.norm(avg), 1e-6)) for v in chroma_vecs]
            chroma_stability = float(np.mean(sims))
        else:
            chroma_stability = 0.5
        scores.append(0.45 * on_strength + 0.35 * low_strength + 0.20 * chroma_stability)

    if not any(scores):
        return float(beat_times[0]), 0.3
    scores_arr = np.asarray(scores)
    scores_arr = (scores_arr - scores_arr.min()) / max(scores_arr.max() - scores_arr.min(), 1e-9)
    best = int(np.argmax(scores_arr))
    second = float(np.partition(scores_arr, -2)[-2]) if scores_arr.size >= 2 else 0.0
    confidence = float(scores_arr[best] - second) if second < scores_arr[best] else 0.3
    confidence = max(0.0, min(1.0, confidence + 0.3))
    chosen = float(beat_times[best]) if best < beat_times.size else float(beat_times[0])
    return chosen, confidence


# ---------------------------------------------------------------------------
# Key estimation (reuse precomputed chroma)
# ---------------------------------------------------------------------------


def _estimate_key_from_chroma(chroma: np.ndarray) -> tuple[str, float]:
    if chroma.size == 0:
        return "", 0.0
    vector = np.maximum(np.nanmean(chroma, axis=1), 0)
    if not np.any(vector):
        return "", 0.0
    vector = vector / np.linalg.norm(vector)

    candidates: list[tuple[float, str]] = []
    for i, note in enumerate(NOTE_NAMES):
        major = np.roll(MAJOR_PROFILE, i)
        minor = np.roll(MINOR_PROFILE, i)
        major = major / np.linalg.norm(major)
        minor = minor / np.linalg.norm(minor)
        candidates.append((float(np.dot(vector, major)), note))
        candidates.append((float(np.dot(vector, minor)), f"{note}m"))

    candidates.sort(reverse=True, key=lambda item: item[0])
    best_score, best_key = candidates[0]
    second_score = candidates[1][0] if len(candidates) > 1 else 0.0
    confidence = max(0.0, min(1.0, best_score - second_score + 0.5))
    return best_key, confidence


def camelot_key(key: str) -> str:
    if not key:
        return ""
    normalized = _normalize_key(key)
    if normalized.endswith("m"):
        return CAMELot_MINOR.get(normalized, "")
    return CAMELot_MAJOR.get(normalized, "")


def _normalize_key(key: str) -> str:
    return {
        "C#": "Db",
        "C#m": "Dbm",
        "D#": "Eb",
        "D#m": "Ebm",
        "G#": "Ab",
        "G#m": "Abm",
        "A#": "Bb",
        "A#m": "Bbm",
    }.get(key, key)


# ---------------------------------------------------------------------------
# Loop profile (extended with vocals + beat_times)
# ---------------------------------------------------------------------------


def _build_loop_profile_from_bundle(
    bundle: _FeatureBundle,
    *,
    path: Path,
    full_duration: float,
    vocals: list[VocalInterval],
) -> LoopProfile | None:
    base = LoopProfile(
        offset_sec=0.0,
        sample_rate=bundle.sr,
        y=bundle.y,
        rms_times=bundle.times,
        rms=bundle.rms,
        feature_times=bundle.times,
        onset=bundle.onset,
        chroma=bundle.chroma,
        centroid=bundle.centroid,
        bandwidth=bundle.bandwidth,
        vocals=tuple(vocals),
        beat_times=bundle.beat_times,
    )
    if full_duration <= 420.0:
        return base

    # For long tracks decode a tail window and merge for Exit Loop search.
    import librosa

    tail_window = min(220.0, full_duration)
    tail_offset = max(0.0, full_duration - tail_window)
    if tail_offset <= bundle.duration_analysis + 5.0:
        return base
    try:
        y_tail, sr_tail = librosa.load(str(path), sr=22050, mono=True, offset=tail_offset, duration=tail_window)
    except Exception:
        return base
    if y_tail.size == 0:
        return base
    tail_bundle = _build_feature_bundle(y_tail, sr_tail, duration_full=tail_window)
    tail_profile = LoopProfile(
        offset_sec=float(tail_offset),
        sample_rate=tail_bundle.sr,
        y=tail_bundle.y,
        rms_times=tail_bundle.times + tail_offset,
        rms=tail_bundle.rms,
        feature_times=tail_bundle.times + tail_offset,
        onset=tail_bundle.onset,
        chroma=tail_bundle.chroma,
        centroid=tail_bundle.centroid,
        bandwidth=tail_bundle.bandwidth,
        vocals=tuple(),
        beat_times=tail_bundle.beat_times + tail_offset if tail_bundle.beat_times.size else tail_bundle.beat_times,
    )
    return _merge_loop_profiles([base, tail_profile])


def _merge_loop_profiles(profiles: list[LoopProfile]) -> LoopProfile | None:
    profiles = [p for p in profiles if p.rms_times.size and p.rms.size]
    if not profiles:
        return None
    if len(profiles) == 1:
        return profiles[0]

    profiles.sort(key=lambda profile: float(profile.rms_times[0]))
    sample_rate = profiles[0].sample_rate
    y = np.concatenate([p.y for p in profiles if p.y.size])
    rms_times = np.concatenate([p.rms_times for p in profiles])
    rms = np.concatenate([p.rms for p in profiles])

    if all(p.feature_times is not None for p in profiles):
        feature_times = np.concatenate([p.feature_times for p in profiles])  # type: ignore[arg-type]
        onset = (
            np.concatenate([p.onset for p in profiles])  # type: ignore[arg-type]
            if all(p.onset is not None for p in profiles)
            else None
        )
        centroid = (
            np.concatenate([p.centroid for p in profiles])  # type: ignore[arg-type]
            if all(p.centroid is not None for p in profiles)
            else None
        )
        bandwidth = (
            np.concatenate([p.bandwidth for p in profiles])  # type: ignore[arg-type]
            if all(p.bandwidth is not None for p in profiles)
            else None
        )
        chroma = (
            np.concatenate([p.chroma for p in profiles], axis=1)  # type: ignore[arg-type]
            if all(p.chroma is not None for p in profiles)
            else None
        )
    else:
        feature_times = None
        onset = None
        centroid = None
        bandwidth = None
        chroma = None

    beat_times_parts = [p.beat_times for p in profiles if p.beat_times is not None and p.beat_times.size]
    beat_times = np.concatenate(beat_times_parts) if beat_times_parts else None
    vocals = tuple(v for p in profiles for v in p.vocals)

    return LoopProfile(
        offset_sec=float(rms_times[0]),
        sample_rate=sample_rate,
        y=y,
        rms_times=rms_times,
        rms=rms,
        feature_times=feature_times,
        onset=onset,
        chroma=chroma,
        centroid=centroid,
        bandwidth=bandwidth,
        vocals=vocals,
        beat_times=beat_times,
    )


# ---------------------------------------------------------------------------
# Energy curve
# ---------------------------------------------------------------------------


def _build_energy_curve(bundle: _FeatureBundle) -> list[float]:
    if bundle.times.size == 0 or bundle.duration_analysis <= 0:
        return [0.0] * ENERGY_CURVE_BINS
    duration = bundle.duration_analysis
    seconds = np.arange(0.0, duration, 1.0)
    if seconds.size < 4:
        seconds = np.linspace(0.0, max(duration, 1.0), max(8, ENERGY_CURVE_BINS))
    rms_norm = _safe_normalize(bundle.rms)
    onset_norm = _safe_normalize(bundle.onset)
    centroid_norm = bundle.centroid  # already normalized to [0,1]
    # Onset density per second: count threshold-crossings of onset over its 70th percentile.
    onset_thresh = float(np.percentile(bundle.onset, 70)) if bundle.onset.size else 0.0
    onset_density = np.zeros_like(seconds)
    for idx, t in enumerate(seconds):
        lo, hi = t, t + 1.0
        mask = (bundle.times >= lo) & (bundle.times < hi)
        if not np.any(mask):
            continue
        window = bundle.onset[mask]
        crossings = int(np.sum((window[1:] > onset_thresh) & (window[:-1] <= onset_thresh))) if window.size > 1 else 0
        onset_density[idx] = min(crossings / 8.0, 1.0)

    rms_sec = np.zeros_like(seconds)
    flux_sec = np.zeros_like(seconds)
    for idx, t in enumerate(seconds):
        lo, hi = t, t + 1.0
        mask = (bundle.times >= lo) & (bundle.times < hi)
        if not np.any(mask):
            continue
        rms_sec[idx] = float(np.mean(rms_norm[mask]))
        flux_sec[idx] = float(np.mean(onset_norm[mask]))

    raw = 0.50 * rms_sec + 0.30 * flux_sec + 0.20 * onset_density
    raw = np.clip(raw, 0.0, 1.0)
    # Downsample to ENERGY_CURVE_BINS
    if raw.size == ENERGY_CURVE_BINS:
        out = raw
    else:
        xs = np.linspace(0.0, 1.0, raw.size)
        xt = np.linspace(0.0, 1.0, ENERGY_CURVE_BINS)
        out = np.interp(xt, xs, raw)
    return [float(v) for v in out]


def _safe_normalize(values: np.ndarray) -> np.ndarray:
    if values.size == 0:
        return values
    ref = float(np.percentile(values, 95)) if values.size else 0.0
    if ref <= 1e-9:
        return np.zeros_like(values)
    return np.clip(values / ref, 0.0, 1.0)


# ---------------------------------------------------------------------------
# Structural segmentation
# ---------------------------------------------------------------------------


def _segment_sections(
    bundle: _FeatureBundle,
    *,
    energy_curve: list[float],
) -> tuple[list[Section], float, str]:
    if bundle.beat_times.size < 12 or bundle.duration_analysis <= 30.0:
        return [], 0.0, "heuristic"
    try:
        boundaries_sec = _compute_section_boundaries(bundle)
    except Exception:
        boundaries_sec = []
    if not boundaries_sec or len(boundaries_sec) < 2:
        return [], 0.0, "heuristic"

    bar = 60.0 / bundle.tempo * 4.0 if bundle.tempo > 0 else 2.0
    # Snap boundaries to nearest downbeat
    snapped: list[tuple[float, float]] = []  # (snapped_sec, snap_quality 0..1)
    for sec in boundaries_sec:
        snap_sec = _snap_down_to_bar(sec, bundle.first_downbeat, bar)
        offset = abs(sec - snap_sec)
        quality = max(0.0, 1.0 - offset / max(bar / 2.0, 1e-6))
        snapped.append((snap_sec, quality))

    # Dedup adjacent boundaries within 1 bar
    cleaned: list[tuple[float, float]] = []
    for entry in snapped:
        if not cleaned or entry[0] - cleaned[-1][0] >= bar - 0.05:
            cleaned.append(entry)
    if not cleaned or cleaned[0][0] > 0.5:
        cleaned.insert(0, (0.0, 1.0))
    cleaned.append((bundle.duration_full, 1.0))

    # Build sections
    sections_raw: list[Section] = []
    snap_qualities: list[float] = []
    for i in range(len(cleaned) - 1):
        start, q = cleaned[i]
        end, _ = cleaned[i + 1]
        if end - start < bar * 4.0:
            continue
        sections_raw.append(Section(start_sec=round(start, 3), end_sec=round(end, 3), label="section", confidence=round(q, 3)))
        snap_qualities.append(q)
    if len(sections_raw) < 3 or len(sections_raw) > 12:
        return [], 0.0, "heuristic"

    # Score each section's energy by sampling the curve
    duration = max(bundle.duration_full, 1e-6)
    bins = ENERGY_CURVE_BINS
    def section_energy(s: Section) -> float:
        lo = int(np.clip(int(s.start_sec / duration * bins), 0, bins - 1))
        hi = int(np.clip(int(s.end_sec / duration * bins), lo + 1, bins))
        return float(np.mean(energy_curve[lo:hi])) if hi > lo else 0.0
    energies = [section_energy(s) for s in sections_raw]

    # Label
    labels = ["section"] * len(sections_raw)
    labels[0] = "intro"
    labels[-1] = "outro"
    middle_idx = list(range(1, len(sections_raw) - 1))
    if not middle_idx:
        return [], 0.0, "heuristic"

    middle_energies = [(i, energies[i]) for i in middle_idx]
    middle_energies.sort(key=lambda item: item[1], reverse=True)
    drop_idx, drop_energy = middle_energies[0]
    drop_dominance = 0.0
    if len(middle_energies) >= 2:
        second_energy = middle_energies[1][1]
        drop_dominance = max(0.0, (drop_energy / max(second_energy, 1e-6)) - 1.0)
    if drop_dominance >= 0.25:
        labels[drop_idx] = "drop"

    # Breakdown: lowest energy in middle, at least 8 bars long, between intro and outro
    breakdown_idx = min(middle_idx, key=lambda i: energies[i])
    if breakdown_idx != drop_idx and sections_raw[breakdown_idx].end_sec - sections_raw[breakdown_idx].start_sec >= bar * 8:
        if energies[breakdown_idx] < (np.median(energies) * 0.7):
            labels[breakdown_idx] = "breakdown"

    # Build before drop
    if labels[drop_idx] == "drop" and drop_idx - 1 in middle_idx:
        pre = sections_raw[drop_idx - 1]
        # Sample energy slope across bins of the previous section
        lo = int(pre.start_sec / duration * bins)
        hi = int(pre.end_sec / duration * bins)
        slope = 0.0
        if hi - lo > 4:
            chunk = energy_curve[lo:hi]
            xs = np.arange(len(chunk))
            slope = float(np.polyfit(xs, chunk, 1)[0])
        if slope > 0.0:
            labels[drop_idx - 1] = "build"

    # Last drop
    if labels[drop_idx] == "drop" and drop_idx + 1 <= len(sections_raw) - 2:
        # second-highest energy after the first drop
        post_idx = [i for i in middle_idx if i > drop_idx]
        if post_idx:
            best_post = max(post_idx, key=lambda i: energies[i])
            if energies[best_post] > np.median(energies) * 1.1 and best_post != drop_idx:
                labels[best_post] = "last_drop"

    sections = [
        Section(start_sec=s.start_sec, end_sec=s.end_sec, label=labels[i], confidence=s.confidence)
        for i, s in enumerate(sections_raw)
    ]

    coverage_score = 1.0 if 3 <= len(sections) <= 9 else 0.0
    snap_score = float(np.median(snap_qualities)) if snap_qualities else 0.0
    drop_score = min(1.0, drop_dominance / 0.6)
    tempo_mult = 1.0 if bundle.tempo_stable else 0.5
    confidence = (0.35 * snap_score + 0.35 * drop_score + 0.20 * coverage_score + 0.10) * tempo_mult
    mode = "structural" if confidence >= 0.65 and any(lbl == "drop" for lbl in labels) else "heuristic"
    return sections, confidence, mode


def _compute_section_boundaries(bundle: _FeatureBundle) -> list[float]:
    import librosa
    from sklearn.cluster import KMeans  # type: ignore

    beat_frames = librosa.time_to_frames(bundle.beat_times, sr=bundle.sr, hop_length=bundle.hop)
    if beat_frames.size < 12:
        return []
    chroma = bundle.chroma
    if chroma.shape[1] == 0:
        return []
    # Build a richer feature stack: CQT-derived chroma + MFCC
    try:
        mfcc = librosa.feature.mfcc(y=bundle.y, sr=bundle.sr, n_mfcc=13, hop_length=bundle.hop)
    except Exception:
        mfcc = np.zeros((13, chroma.shape[1]))
    if mfcc.shape[1] != chroma.shape[1]:
        m = min(mfcc.shape[1], chroma.shape[1])
        mfcc = mfcc[:, :m]
        chroma_use = chroma[:, :m]
    else:
        chroma_use = chroma
    feats = np.vstack([chroma_use, mfcc])
    # Beat-sync
    beat_frames = beat_frames[beat_frames < feats.shape[1]]
    if beat_frames.size < 6:
        return []
    feats_sync = librosa.util.sync(feats, beat_frames, aggregate=np.median)
    if feats_sync.shape[1] < 8:
        return []
    # Recurrence
    rec = librosa.segment.recurrence_matrix(feats_sync, mode="affinity", sym=True)
    try:
        rec = librosa.segment.path_enhance(rec, n=15)
    except Exception:
        pass

    best: list[float] = []
    best_score = -1.0
    n_beats = feats_sync.shape[1]
    for k in (3, 4, 5, 6, 7):
        if n_beats < k * 4:
            continue
        try:
            evals, evecs = _laplacian_eigs(rec, k)
        except Exception:
            continue
        if evecs.size == 0:
            continue
        # Normalize rows
        norm = np.linalg.norm(evecs, axis=1, keepdims=True)
        norm[norm < 1e-9] = 1.0
        X = evecs / norm
        try:
            km = KMeans(n_clusters=k, n_init=5, random_state=0).fit(X)
        except Exception:
            continue
        labels = km.labels_
        changes = np.where(np.diff(labels) != 0)[0] + 1
        seg_count = int(changes.size + 1)
        if not (3 <= seg_count <= 9):
            continue
        # Score by mean intra-segment similarity in feats_sync (cosine)
        score = _intra_segment_similarity(feats_sync, changes)
        if score > best_score:
            best_score = score
            # Map cluster change indices back to time
            beat_idx = np.clip(changes, 0, beat_frames.size - 1)
            best = [float(librosa.frames_to_time(beat_frames[i], sr=bundle.sr, hop_length=bundle.hop)) for i in beat_idx]
    return sorted(best)


def _laplacian_eigs(affinity: np.ndarray, k: int) -> tuple[np.ndarray, np.ndarray]:
    import scipy.sparse.linalg as spla  # type: ignore
    from scipy.sparse import csr_matrix, eye  # type: ignore

    A = csr_matrix(affinity)
    deg = np.asarray(A.sum(axis=1)).flatten()
    deg_inv_sqrt = 1.0 / np.sqrt(np.maximum(deg, 1e-9))
    D = csr_matrix((deg_inv_sqrt, (np.arange(deg.size), np.arange(deg.size))), shape=A.shape)
    L = eye(A.shape[0]) - D @ A @ D
    # Smallest eigenvalues
    try:
        evals, evecs = spla.eigsh(L, k=min(k, L.shape[0] - 1), which="SM")
    except Exception:
        # Fallback dense
        L_dense = L.toarray()
        evals, evecs = np.linalg.eigh(L_dense)
        evecs = evecs[:, :k]
    return evals, evecs


def _intra_segment_similarity(feats: np.ndarray, changes: np.ndarray) -> float:
    if feats.shape[1] == 0:
        return 0.0
    segments: list[tuple[int, int]] = []
    prev = 0
    for c in changes.tolist():
        segments.append((prev, int(c)))
        prev = int(c)
    segments.append((prev, feats.shape[1]))

    sims: list[float] = []
    for lo, hi in segments:
        if hi - lo < 2:
            continue
        seg = feats[:, lo:hi]
        mean_vec = np.mean(seg, axis=1, keepdims=True)
        # cosine similarity of each frame to the mean
        num = (seg * mean_vec).sum(axis=0)
        denom = np.linalg.norm(seg, axis=0) * np.linalg.norm(mean_vec)
        denom = np.maximum(denom, 1e-9)
        sims.append(float(np.mean(num / denom)))
    return float(np.mean(sims)) if sims else 0.0


# ---------------------------------------------------------------------------
# Vocal detection
# ---------------------------------------------------------------------------


def _detect_vocals_hpss(y: np.ndarray, sr: int, *, bundle: _FeatureBundle) -> list[VocalInterval]:
    import librosa

    if y.size == 0:
        return []
    try:
        y_harm, y_perc = librosa.effects.hpss(y)
    except Exception:
        return []
    hop = bundle.hop
    try:
        harm_rms = librosa.feature.rms(y=y_harm, frame_length=2048, hop_length=hop)[0]
        perc_rms = librosa.feature.rms(y=y_perc, frame_length=2048, hop_length=hop)[0]
        contrast = librosa.feature.spectral_contrast(y=y, sr=sr, hop_length=hop)
        mid_contrast = np.mean(contrast[1:5], axis=0)
    except Exception:
        return []
    n = min(harm_rms.size, perc_rms.size, mid_contrast.size, bundle.times.size)
    if n < 16:
        return []
    harm_rms = harm_rms[:n]
    perc_rms = perc_rms[:n]
    mid_contrast = mid_contrast[:n]
    times = bundle.times[:n]

    harm_med = float(np.median(harm_rms))
    contrast_med = float(np.median(mid_contrast))
    total = harm_rms + perc_rms + 1e-9
    perc_ratio = perc_rms / total

    likely = (
        (harm_rms > harm_med * 1.20)
        & (mid_contrast > contrast_med * 1.10)
        & (perc_ratio < 0.75)
    )
    if not np.any(likely):
        return []
    frame_dt = float(times[1] - times[0]) if times.size > 1 else 0.025
    # 0.4-second median filter — short enough to keep brief vocal hooks, long enough to drop single-frame noise.
    likely = _median_filter_bool(likely, window=max(3, int(0.4 / max(frame_dt, 1e-6))))
    intervals = _runs_to_intervals(likely, times)
    intervals = _merge_intervals(intervals, gap=1.5)
    intervals = [iv for iv in intervals if iv[1] - iv[0] >= 1.0]
    # Per-interval confidence: mean of normalized harm_rms in interval
    ref = max(float(np.percentile(harm_rms, 95)), 1e-9)
    out: list[VocalInterval] = []
    for start, end in intervals:
        mask = (times >= start) & (times <= end)
        if not np.any(mask):
            continue
        conf = float(np.clip(np.mean(harm_rms[mask]) / ref, 0.0, 1.0))
        out.append(VocalInterval(start_sec=round(start, 3), end_sec=round(end, 3), confidence=round(conf, 3)))
    return out


def _median_filter_bool(arr: np.ndarray, window: int) -> np.ndarray:
    if window <= 1 or arr.size <= window:
        return arr
    pad = window // 2
    padded = np.pad(arr.astype(int), (pad, pad), mode="edge")
    out = np.zeros_like(arr, dtype=bool)
    for i in range(arr.size):
        out[i] = padded[i : i + window].sum() > (window // 2)
    return out


def _runs_to_intervals(mask: np.ndarray, times: np.ndarray) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    n = mask.size
    i = 0
    while i < n:
        if mask[i]:
            j = i
            while j < n and mask[j]:
                j += 1
            out.append((float(times[i]), float(times[min(j, n - 1)])))
            i = j
        else:
            i += 1
    return out


def _merge_intervals(intervals: list[tuple[float, float]], *, gap: float) -> list[tuple[float, float]]:
    if not intervals:
        return []
    intervals = sorted(intervals)
    merged = [intervals[0]]
    for start, end in intervals[1:]:
        last_start, last_end = merged[-1]
        if start - last_end <= gap:
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))
    return merged


def _vocal_coverage(intervals: list[VocalInterval], duration: float) -> float:
    if duration <= 0:
        return 0.0
    total = sum(max(0.0, v.end_sec - v.start_sec) for v in intervals)
    return float(min(1.0, total / duration))


def _classify_vocal(coverage: float) -> str:
    if coverage <= 0.0:
        return "unknown"
    if coverage < 0.05:
        return "instrumental"
    if coverage < 0.25:
        return "dub"
    return "vocal"


def _demucs_extract(path: Path, *, vocal_class: str) -> tuple[str, list[VocalInterval]] | None:
    if shutil.which("demucs") is None:
        return None
    target = path.with_name(path.stem + ".instrumental.mp3")
    if target.exists():
        return str(target), []
    with tempfile.TemporaryDirectory() as tmp:
        try:
            subprocess.run(
                [
                    "demucs",
                    "--two-stems",
                    "vocals",
                    "--mp3",
                    "-o",
                    tmp,
                    str(path),
                ],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=600,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        # demucs writes to <tmp>/<model>/<stem>/no_vocals.mp3
        for candidate in Path(tmp).rglob("no_vocals.mp3"):
            shutil.copy2(candidate, target)
            return str(target), []
    return None


# ---------------------------------------------------------------------------
# Cue placement
# ---------------------------------------------------------------------------


def _cue_hints(
    *,
    duration: float,
    bpm: float,
    first_downbeat: float,
    loop_profile: LoopProfile | None = None,
    sections: list[Section] | None = None,
    segmentation_mode: str = "heuristic",
    energy_curve: list[float] | None = None,
) -> list[CueHint]:
    bar = 60.0 / bpm * 4.0 if bpm > 0 else 2.0
    sections = sections or []
    energy_curve = energy_curve or []
    intro_start = max(0.0, first_downbeat)

    structural = segmentation_mode == "structural"
    by_label = {s.label: s for s in sections}

    if structural:
        build = by_label.get("build")
        drop = by_label.get("drop")
        breakdown = by_label.get("breakdown")
        last_drop = by_label.get("last_drop")
        outro = by_label.get("outro")
        pad_b_name = "Build"
        pad_b_sec = build.start_sec if build is not None else (intro_start + bar * 16)
        if build is None:
            pad_b_name = "Phrase 16"
        pad_c_name = "Drop"
        pad_c_sec = drop.start_sec if drop is not None else (intro_start + bar * 32)
        if drop is None:
            pad_c_name = "Phrase 32"
        pad_f = (
            CueHint("Breakdown", breakdown.start_sec, "hot", 5)
            if breakdown is not None
            else None
        )
        pad_g = (
            CueHint("Last Drop", last_drop.start_sec, "hot", 6)
            if last_drop is not None
            else None
        )
        outro_sec = outro.start_sec if outro is not None else max(0.0, duration - bar * 32)
    else:
        pad_b_name = "Phrase 16"
        pad_b_sec = max(0.0, first_downbeat + bar * 16)
        pad_c_name = "Phrase 32"
        pad_c_sec = max(0.0, first_downbeat + bar * 32)
        breakdown_sec, drop_sec = _heuristic_drop_breakdown(
            duration=duration,
            bar=bar,
            first_downbeat=first_downbeat,
            energy_curve=energy_curve,
        )
        pad_f = (
            CueHint("Breakdown", breakdown_sec, "memory", None)
            if breakdown_sec is not None
            else None
        )
        # Drop in heuristic mode lands as memory cue (the hot slot is already Phrase 32)
        pad_g = (
            CueHint("Drop", drop_sec, "memory", None)
            if drop_sec is not None
            else None
        )
        outro_sec = max(0.0, duration - bar * 32)

    intro_loop = (
        _best_intro_loop_candidate(
            duration=duration,
            bar=bar,
            first_downbeat=first_downbeat,
            loop_profile=loop_profile,
            sections=sections if structural else None,
        )
        if loop_profile is not None
        else None
    )
    exit_loop = (
        _best_exit_loop_candidate(
            duration=duration,
            bar=bar,
            first_downbeat=first_downbeat,
            loop_profile=loop_profile,
            sections=sections if structural else None,
        )
        if loop_profile is not None
        else None
    )

    values = [
        CueHint("Intro", intro_start, "hot", 0),
        CueHint(pad_b_name, pad_b_sec, "hot", 1),
        CueHint(pad_c_name, pad_c_sec, "hot", 2),
        (
            CueHint("Intro Loop", intro_loop[0], "loop", 3, intro_loop[1], intro_loop[2])
            if intro_loop is not None
            else None
        ),
        (
            CueHint("Exit Loop", exit_loop[0], "loop", 4, exit_loop[1], exit_loop[2])
            if exit_loop is not None
            else None
        ),
        pad_f,
        pad_g,
        CueHint("Outro", outro_sec, "memory", None),
    ]

    seen: set[tuple[str, int | None, int]] = set()
    cues: list[CueHint] = []
    for cue in values:
        if cue is None:
            continue
        seconds = round(min(max(cue.seconds, 0.0), max(duration - 0.1, 0.0)), 3)
        end_seconds = (
            round(min(max(cue.end_seconds, seconds + 0.1), duration), 3)
            if cue.end_seconds is not None
            else None
        )
        if cue.kind == "loop" and (end_seconds is None or end_seconds <= seconds):
            continue
        bucket = int(seconds * 10)
        key = (cue.kind, cue.hotcue_slot, bucket)
        if key in seen:
            continue
        seen.add(key)
        cues.append(CueHint(cue.name, seconds, cue.kind, cue.hotcue_slot, end_seconds, cue.loop_beats))
    return cues


def _heuristic_drop_breakdown(
    *,
    duration: float,
    bar: float,
    first_downbeat: float,
    energy_curve: list[float],
) -> tuple[float | None, float | None]:
    if not energy_curve or duration <= bar * 8 or bar <= 0:
        return None, None
    curve = np.asarray(energy_curve, dtype=float)
    n = curve.size
    if n < 16:
        return None, None
    times = np.linspace(0.0, duration, n)

    # Drop
    drop_sec: float | None = None
    peak_idx = int(np.argmax(curve))
    peak_val = float(curve[peak_idx])
    median = float(np.median(curve))
    if peak_val > 0.65 and peak_val > 1.3 * max(median, 1e-6):
        candidate = float(times[peak_idx])
        snapped = _snap_down_to_bar(candidate, first_downbeat, bar)
        if 24 * bar <= snapped <= duration - 8 * bar:
            drop_sec = snapped

    # Breakdown: minimum of smoothed curve, away from start and end
    kernel = max(3, int(round((bar * 8 / duration) * n)))
    smoothed = _moving_average(curve, kernel)
    margin_idx = int((24 * bar / duration) * n)
    if margin_idx * 2 < smoothed.size:
        window = smoothed[margin_idx:-margin_idx] if margin_idx > 0 else smoothed
        if window.size:
            local_min = float(np.min(window))
            if local_min < 0.35:
                rel_idx = int(np.argmin(window)) + margin_idx
                candidate = float(times[rel_idx])
                snapped = _snap_down_to_bar(candidate, first_downbeat, bar)
                if 24 * bar <= snapped <= duration - 24 * bar:
                    return snapped, drop_sec
    return None, drop_sec


# ---------------------------------------------------------------------------
# Loop candidates (extends the prior scorer with vocal hard reject, plateau
# re-rank, beat-end snap, and raised thresholds.)
# ---------------------------------------------------------------------------


def _best_exit_loop_candidate(
    *,
    duration: float,
    bar: float,
    first_downbeat: float,
    loop_profile: LoopProfile,
    preferred_loop_beats: int | None = None,
    sections: list[Section] | None = None,
) -> tuple[float, float, int] | None:
    if bar <= 0 or duration <= bar * 8:
        return None
    candidate_beats = _loop_beat_options(duration, bar, preferred_loop_beats=preferred_loop_beats)
    if not candidate_beats:
        return None

    keepout = min(8 * bar, max(4 * bar, duration * 0.05))
    base_earliest = max(first_downbeat + 32 * bar, duration * 0.55, _profile_start(loop_profile) + bar)
    base_latest_end = min(duration - keepout, _profile_end(loop_profile) - 0.25)
    earliest, latest_end = _restrict_window(
        sections=sections, role="exit", base_start=base_earliest, base_end=base_latest_end, bar=bar
    )

    candidates = _search_loop_candidates(
        loop_profile,
        duration=duration,
        bar=bar,
        first_downbeat=first_downbeat,
        earliest=earliest,
        latest_end=latest_end,
        candidate_beats=candidate_beats,
        role="exit",
    )
    best = _select_loop_candidate(candidates)
    if best is None:
        return None
    start, end, beats = _snap_loop_end_to_beat(best[0], best[1], int(best[2]), loop_profile, bar)
    return round(start, 3), round(end, 3), int(beats)


def _best_intro_loop_candidate(
    *,
    duration: float,
    bar: float,
    first_downbeat: float,
    loop_profile: LoopProfile,
    sections: list[Section] | None = None,
) -> tuple[float, float, int] | None:
    if bar <= 0 or duration <= bar * 8:
        return None
    candidate_beats = _loop_beat_options(duration, bar)
    if not candidate_beats:
        return None

    base_earliest = max(first_downbeat + 4 * bar, _profile_start(loop_profile) + bar)
    base_latest_end = min(
        first_downbeat + 96 * bar,
        120.0,
        duration - max(bar, 0.5),
        _profile_end(loop_profile) - 0.25,
    )
    earliest, latest_end = _restrict_window(
        sections=sections, role="intro", base_start=base_earliest, base_end=base_latest_end, bar=bar
    )

    candidates = _search_loop_candidates(
        loop_profile,
        duration=duration,
        bar=bar,
        first_downbeat=first_downbeat,
        earliest=earliest,
        latest_end=latest_end,
        candidate_beats=candidate_beats,
        role="intro",
    )
    best = _select_loop_candidate(candidates)
    if best is None:
        return None
    start, end, beats = _snap_loop_end_to_beat(best[0], best[1], int(best[2]), loop_profile, bar)
    return round(start, 3), round(end, 3), int(beats)


def _restrict_window(
    *,
    sections: list[Section] | None,
    role: str,
    base_start: float,
    base_end: float,
    bar: float,
) -> tuple[float, float]:
    if not sections:
        return base_start, base_end
    target = "intro" if role == "intro" else "outro"
    section = next((s for s in sections if s.label == target), None)
    if section is None:
        return base_start, base_end
    # Extend by up to 4 bars into the next section if intro is short.
    if role == "intro":
        start = max(base_start, section.start_sec)
        end = min(base_end, section.end_sec + 4 * bar)
    else:
        start = max(base_start, section.start_sec - 4 * bar)
        end = min(base_end, section.end_sec)
    if end <= start + 4 * bar:
        return base_start, base_end
    return start, end


def _search_loop_candidates(
    profile: LoopProfile,
    *,
    duration: float,
    bar: float,
    first_downbeat: float,
    earliest: float,
    latest_end: float,
    candidate_beats: list[int],
    role: str,
) -> list[tuple[float, float, int, float]]:
    if latest_end <= earliest:
        return []

    candidates: list[tuple[float, float, int, float]] = []
    beat = bar / 4.0
    search_start = _snap_up_to_beat(earliest, first_downbeat, beat)
    search_end = _snap_down_to_beat(latest_end, first_downbeat, beat)
    if search_end <= search_start:
        return []

    for beats in candidate_beats:
        loop_len = beats * beat
        start = search_start
        while start + loop_len <= search_end + 0.001:
            end = start + loop_len
            if _vocal_overlap_fraction(profile.vocals, start, end, min_confidence=0.6) > 0.25 and role == "intro":
                start += beat
                continue
            score = _loop_candidate_score(
                profile,
                start=start,
                end=end,
                bar=bar,
                duration=duration,
                role=role,
            )
            if score is not None and score >= _loop_acceptance_threshold(beats):
                candidates.append((start, end, beats, score))
            start += beat
    return candidates


def _vocal_overlap_fraction(
    vocals: tuple[VocalInterval, ...] | list[VocalInterval],
    start: float,
    end: float,
    *,
    min_confidence: float,
) -> float:
    if not vocals or end <= start:
        return 0.0
    total = 0.0
    for v in vocals:
        if v.confidence < min_confidence:
            continue
        lo = max(start, v.start_sec)
        hi = min(end, v.end_sec)
        if hi > lo:
            total += hi - lo
    return total / max(end - start, 1e-6)


def _select_loop_candidate(candidates: list[tuple[float, float, int, float]]) -> tuple[float, float, int, float] | None:
    if not candidates:
        return None
    # Plateau re-rank: from top 8 by raw score, pick the one with highest mean score
    # over candidates within 1 beat of its start (proxy for score-plateau robustness).
    by_score = sorted(candidates, key=lambda item: item[3], reverse=True)
    top = by_score[:8]
    if len(top) <= 1:
        return top[0]
    # Group by beats length so we don't mix 4-beat and 8-beat plateaus.
    grouped: dict[int, list[tuple[float, float, int, float]]] = {}
    for c in candidates:
        grouped.setdefault(c[2], []).append(c)

    def plateau_score(candidate: tuple[float, float, int, float]) -> float:
        beats = candidate[2]
        same = grouped.get(beats, [])
        near = [c[3] for c in same if abs(c[0] - candidate[0]) <= max(0.6, 60.0 / 120.0)]
        if not near:
            return candidate[3]
        return float(np.mean(near))

    re_ranked = sorted(top, key=plateau_score, reverse=True)
    return re_ranked[0]


def _loop_beat_options(duration: float, bar: float, *, preferred_loop_beats: int | None = None) -> list[int]:
    if bar <= 0:
        return []
    beat = bar / 4.0
    order = (8, 4)
    maximum = preferred_loop_beats or 8
    options = [
        beats
        for beats in order
        if beats <= maximum and duration >= beats * beat * 2.5
    ]
    return list(dict.fromkeys(options))


def _loop_acceptance_threshold(beats: int) -> float:
    if beats >= 8:
        return 0.74
    return 0.68


def _snap_loop_end_to_beat(start: float, end: float, beats: int, profile: LoopProfile, bar: float) -> tuple[float, float, int]:
    if profile.beat_times is None or profile.beat_times.size == 0:
        return start, end, beats
    bts = profile.beat_times
    nearest = bts[np.argmin(np.abs(bts - end))]
    if abs(nearest - end) <= 0.05:
        return start, float(nearest), beats
    return start, end, beats


def _loop_candidate_score(
    profile: LoopProfile,
    *,
    start: float,
    end: float,
    bar: float,
    duration: float,
    role: str = "exit",
) -> float | None:
    segment = _rms_between(profile, start, end)
    if segment.size < 4:
        return None

    rms_floor = max(float(np.percentile(profile.rms, 35)), 1e-6)
    rms_ref = max(float(np.percentile(profile.rms, 85)), rms_floor * 1.5, 1e-6)
    mean_energy = float(np.mean(segment))
    if mean_energy < rms_floor * 1.05:
        return None

    probe = max(0.35, min(bar, (end - start) / 4.0))
    first_bar = _rms_vector(profile, start, start + probe, 12)
    last_bar = _rms_vector(profile, end - probe, end, 12)
    if first_bar is None or last_bar is None:
        return None

    first_mean = max(float(np.mean(first_bar)), 1e-6)
    last_mean = max(float(np.mean(last_bar)), 1e-6)
    edge_db = abs(_ratio_db(last_mean, first_mean))
    if edge_db > 3.5:
        return None

    quarter = max(probe, (end - start) / 4.0)
    first_quarter = _rms_between(profile, start, min(end, start + quarter))
    last_quarter = _rms_between(profile, max(start, end - quarter), end)
    if first_quarter.size and last_quarter.size:
        ramp_db = _ratio_db(float(np.mean(last_quarter)), float(np.mean(first_quarter)))
        if role == "exit" and ramp_db < -2.8:
            return None
        if abs(ramp_db) > 4.0:
            return None
    else:
        ramp_db = 0.0

    rms_range_db = _ratio_db(float(np.percentile(segment, 95)), max(float(np.percentile(segment, 5)), 1e-6))
    if rms_range_db > 8.0:
        return None

    fill_penalty = _transition_penalty(profile, start=start, end=end, bar=bar)
    if fill_penalty >= 0.85:
        return None

    onset_score = _onset_presence_score(profile, start=start, end=end)
    if onset_score < 0.22:
        return None

    start_edge = max(float(first_bar[0]), 1e-6)
    end_edge = max(float(last_bar[-1]), 1e-6)
    boundary_match = 1.0 - min(abs(end_edge - start_edge) / max(end_edge, start_edge), 1.0)

    first_norm = first_bar / first_mean
    last_norm = last_bar / last_mean
    shape_match = 1.0 - min(float(np.mean(np.abs(first_norm - last_norm))) / 1.5, 1.0)

    feature_match = _feature_sequence_match(
        profile,
        start,
        start + probe,
        end - probe,
        end,
        size=20,
    )
    half_match = _feature_sequence_match(
        profile,
        start,
        start + (end - start) / 2.0,
        start + (end - start) / 2.0,
        end,
        size=28,
    )
    bar_consistency = _bar_feature_consistency(profile, start=start, end=end, bar=bar)

    stability = 1.0 - min(float(np.std(segment)) / max(mean_energy, 1e-6), 1.0)
    ramp_score = 1.0 - min(abs(ramp_db) / 4.0, 1.0)
    energy_stability = 0.65 * stability + 0.35 * ramp_score
    energy_score = min(mean_energy / rms_ref, 1.0)
    placement_score = _loop_role_score(role=role, start=start, duration=duration)
    downbeat_score = _downbeat_strength(profile, start=start)
    boundary_score = 0.45 * boundary_match + 0.35 * shape_match + 0.20 * feature_match
    groove_score = 0.70 * bar_consistency + 0.30 * half_match
    timbre_score = 0.70 * feature_match + 0.30 * half_match
    clean_transition = 1.0 - fill_penalty
    beat = bar / 4.0
    length_beats = int(round(max(1.0, (end - start) / max(beat, 1e-6))))
    length_bias = {4: 1.0, 8: 0.985}.get(length_beats, 0.96)
    entry_penalty = _exit_entry_transient_penalty(profile, start=start, beat=beat) if role == "exit" else 0.0
    vocal_overlap = _vocal_overlap_fraction(profile.vocals, start, end, min_confidence=0.6)
    vocal_penalty = (0.20 if role == "intro" else 0.18) * vocal_overlap

    score = (
        0.25 * groove_score
        + 0.20 * boundary_score
        + 0.15 * energy_stability
        + 0.15 * clean_transition
        + 0.10 * downbeat_score
        + 0.08 * placement_score
        + 0.07 * timbre_score
    )
    score = min(score * length_bias + 0.03 * energy_score + 0.02 * onset_score, 1.0)
    score -= 0.09 * entry_penalty
    score -= vocal_penalty
    return max(0.0, min(score, 1.0))


def _profile_start(profile: LoopProfile) -> float:
    if profile.rms_times.size:
        return float(profile.rms_times[0])
    return float(profile.offset_sec)


def _profile_end(profile: LoopProfile) -> float:
    if profile.rms_times.size:
        return float(profile.rms_times[-1])
    if profile.sample_rate > 0:
        return float(profile.offset_sec + len(profile.y) / profile.sample_rate)
    return float(profile.offset_sec)


def _ratio_db(numerator: float, denominator: float) -> float:
    return 20.0 * math.log10(max(numerator, 1e-6) / max(denominator, 1e-6))


def _transition_penalty(profile: LoopProfile, *, start: float, end: float, bar: float) -> float:
    last_start = max(start, end - max(bar, 0.25))
    body_rms = _rms_between(profile, start, last_start)
    last_rms = _rms_between(profile, last_start, end)
    if body_rms.size == 0 or last_rms.size == 0:
        return 0.20

    body_peak = max(float(np.percentile(body_rms, 80)), 1e-6)
    last_peak = max(float(np.percentile(last_rms, 95)), 1e-6)
    rms_jump = max(0.0, min((last_peak / body_peak - 1.0) / 2.0, 1.0))
    last_range = min(_ratio_db(float(np.percentile(last_rms, 95)), float(np.percentile(last_rms, 20))) / 10.0, 1.0)

    onset_jump = 0.0
    if profile.feature_times is not None and profile.onset is not None and profile.onset.size:
        body_onset = _values_between(profile.feature_times, profile.onset, start, last_start)
        last_onset = _values_between(profile.feature_times, profile.onset, last_start, end)
        if body_onset.size and last_onset.size:
            body_ref = max(float(np.percentile(body_onset, 75)), 1e-6)
            last_ref = max(float(np.percentile(last_onset, 95)), 1e-6)
            onset_jump = max(0.0, min((last_ref / body_ref - 1.0) / 2.25, 1.0))

    return max(0.0, min(0.45 * rms_jump + 0.35 * onset_jump + 0.20 * last_range, 1.0))


def _exit_entry_transient_penalty(profile: LoopProfile, *, start: float, beat: float) -> float:
    if beat <= 0 or profile.feature_times is None or profile.onset is None or profile.onset.size == 0:
        return 0.0

    first = _values_between(profile.feature_times, profile.onset, start, start + beat)
    following = _values_between(profile.feature_times, profile.onset, start + beat, start + 2 * beat)
    if first.size == 0 or following.size == 0:
        return 0.0

    first_peak = max(float(np.percentile(first, 90)), 1e-6)
    following_peak = max(float(np.percentile(following, 90)), 1e-6)
    surge = (first_peak - following_peak) / max(first_peak, following_peak, 1e-6)
    return max(0.0, min((surge - 0.08) / 0.25, 1.0))


def _onset_presence_score(profile: LoopProfile, *, start: float, end: float) -> float:
    if profile.feature_times is None or profile.onset is None or profile.onset.size == 0:
        return 0.55
    values = _values_between(profile.feature_times, profile.onset, start, end)
    if values.size == 0:
        return 0.55
    ref = max(float(np.percentile(profile.onset, 65)), 1e-6)
    if ref <= 1e-6:
        return 0.55
    return max(0.0, min(float(np.mean(values)) / ref, 1.0))


def _downbeat_strength(profile: LoopProfile, *, start: float) -> float:
    if profile.feature_times is None or profile.onset is None or profile.onset.size == 0:
        return 0.55
    values = _values_between(profile.feature_times, profile.onset, start - 0.12, start + 0.12)
    if values.size == 0:
        return 0.55
    ref = max(float(np.percentile(profile.onset, 90)), 1e-6)
    if ref <= 1e-6:
        return 0.55
    return max(0.0, min(float(np.max(values)) / ref, 1.0))


def _loop_role_score(*, role: str, start: float, duration: float) -> float:
    if duration <= 0:
        return 0.55
    if role == "intro":
        intro_window = min(duration * 0.45, 120.0)
        return 1.0 - 0.45 * min(max(start / max(intro_window, 1.0), 0.0), 1.0)
    return min(max((start - duration * 0.55) / max(duration * 0.35, 1.0), 0.0), 1.0)


def _values_between(times: np.ndarray, values: np.ndarray, start: float, end: float) -> np.ndarray:
    limit = min(times.size, values.size)
    if limit == 0 or end <= start:
        return np.asarray([], dtype=float)
    times = times[:limit]
    values = values[:limit]
    mask = (times >= start) & (times <= end)
    return values[mask]


def _feature_sequence_match(
    profile: LoopProfile,
    start_a: float,
    end_a: float,
    start_b: float,
    end_b: float,
    *,
    size: int,
) -> float:
    seq_a = _feature_sequence(profile, start_a, end_a, size)
    seq_b = _feature_sequence(profile, start_b, end_b, size)
    if seq_a is None or seq_b is None:
        return 0.55

    combined = np.concatenate([seq_a, seq_b], axis=1)
    spread = np.percentile(combined, 95, axis=1) - np.percentile(combined, 5, axis=1)
    valid = spread > 1e-5
    if not np.any(valid):
        return 0.55

    diff = np.abs(seq_a[valid] - seq_b[valid]) / spread[valid, None]
    return 1.0 - min(float(np.mean(diff)) / 1.2, 1.0)


def _feature_sequence(profile: LoopProfile, start: float, end: float, size: int) -> np.ndarray | None:
    if end <= start or profile.feature_times is None:
        return None
    times = profile.feature_times
    if times.size == 0 or start < times[0] or end > times[-1] + 0.25:
        return None

    points = np.linspace(start, end, size)
    rows: list[np.ndarray] = []
    rms = _rms_vector(profile, start, end, size)
    if rms is not None:
        rows.append(rms / max(float(np.percentile(profile.rms, 90)), 1e-6))
    if profile.onset is not None:
        rows.append(np.interp(points, times, profile.onset) / max(float(np.percentile(profile.onset, 90)), 1e-6))
    if profile.centroid is not None:
        rows.append(np.interp(points, times, profile.centroid))
    if profile.bandwidth is not None:
        rows.append(np.interp(points, times, profile.bandwidth))
    if profile.chroma is not None:
        for chroma_row in profile.chroma:
            rows.append(np.interp(points, times, chroma_row))
    if not rows:
        return None
    return np.vstack(rows)


def _bar_feature_consistency(profile: LoopProfile, *, start: float, end: float, bar: float) -> float:
    if bar <= 0:
        return 0.55

    vectors: list[np.ndarray] = []
    current = start
    while current + bar <= end + 0.001:
        vector = _bar_feature_vector(profile, current, current + bar)
        if vector is not None:
            vectors.append(vector)
        current += bar

    if len(vectors) < 2:
        return 0.55

    adjacent = [_cosine01(vectors[index], vectors[index + 1]) for index in range(len(vectors) - 1)]
    boundary = _cosine01(vectors[-1], vectors[0])
    return float(np.mean(adjacent + [boundary]))


def _bar_feature_vector(profile: LoopProfile, start: float, end: float) -> np.ndarray | None:
    if profile.feature_times is None or start >= end:
        return None
    mask = (profile.feature_times >= start) & (profile.feature_times <= end)
    if not np.any(mask):
        return None

    values: list[float] = []
    rms = _rms_between(profile, start, end)
    if rms.size:
        values.append(float(np.mean(rms) / max(np.percentile(profile.rms, 90), 1e-6)))
    if profile.onset is not None:
        values.append(float(np.mean(profile.onset[mask]) / max(np.percentile(profile.onset, 90), 1e-6)))
    if profile.centroid is not None:
        values.append(float(np.mean(profile.centroid[mask])))
    if profile.bandwidth is not None:
        values.append(float(np.mean(profile.bandwidth[mask])))
    if profile.chroma is not None:
        values.extend(float(value) for value in np.mean(profile.chroma[:, mask], axis=1))
    if not values:
        return None
    return np.asarray(values, dtype=float)


def _cosine01(a: np.ndarray, b: np.ndarray) -> float:
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    if denom <= 1e-9:
        return 0.55
    return max(0.0, min((float(np.dot(a, b)) / denom + 1.0) / 2.0, 1.0))


def _rms_between(profile: LoopProfile, start: float, end: float) -> np.ndarray:
    mask = (profile.rms_times >= start) & (profile.rms_times <= end)
    return profile.rms[mask]


def _rms_vector(profile: LoopProfile, start: float, end: float, size: int) -> np.ndarray | None:
    if end <= start or profile.rms_times.size == 0:
        return None
    if start < profile.rms_times[0] or end > profile.rms_times[-1] + 0.25:
        return None
    points = np.linspace(start, end, size)
    return np.interp(points, profile.rms_times, profile.rms)


def _snap_down_to_bar(value: float, first_downbeat: float, bar: float) -> float:
    if bar <= 0 or value <= first_downbeat:
        return max(0.0, value)
    bars = math.floor((value - first_downbeat) / bar)
    return first_downbeat + bars * bar


def _snap_up_to_bar(value: float, first_downbeat: float, bar: float) -> float:
    if bar <= 0 or value <= first_downbeat:
        return max(0.0, value)
    bars = math.ceil((value - first_downbeat) / bar)
    return first_downbeat + bars * bar


def _snap_down_to_beat(value: float, first_downbeat: float, beat: float) -> float:
    if beat <= 0 or value <= first_downbeat:
        return max(0.0, value)
    beats = math.floor((value - first_downbeat) / beat)
    return first_downbeat + beats * beat


def _snap_up_to_beat(value: float, first_downbeat: float, beat: float) -> float:
    if beat <= 0 or value <= first_downbeat:
        return max(0.0, value)
    beats = math.ceil((value - first_downbeat) / beat)
    return first_downbeat + beats * beat


def _moving_average(values: np.ndarray, width: int) -> np.ndarray:
    if width <= 1 or values.size < width:
        return values
    kernel = np.ones(width) / width
    return np.convolve(values, kernel, mode="same")


def _energy_score(*, bpm: float, loudness_dbfs: float, centroid_norm: float) -> int:
    bpm_score = min(max((bpm - 90.0) / 55.0, 0.0), 1.0)
    loud_score = min(max((loudness_dbfs + 28.0) / 20.0, 0.0), 1.0)
    bright_score = min(max(centroid_norm * 2.2, 0.0), 1.0)
    score = 1.0 + 9.0 * (0.45 * bpm_score + 0.40 * loud_score + 0.15 * bright_score)
    return int(round(min(max(score, 1.0), 10.0)))


def _amp_to_db(value: float) -> float:
    if value <= 0:
        return -120.0
    return 20.0 * math.log10(value)


def _title_artist_from_filename(path: Path) -> tuple[str, str]:
    stem = re.sub(r"\s*\[\d{5,}\]\s*$", "", path.stem).strip()
    if " - " in stem:
        artist, title = stem.split(" - ", 1)
        return title.strip(), artist.strip()
    return stem, ""


def _source_id_from_filename(path: Path) -> str:
    match = re.search(r"\[(\d{5,})\]\s*$", path.stem)
    return match.group(1) if match else ""


def _cache_key(path: Path) -> str:
    stat = path.stat()
    source_id = _source_id_from_filename(path)
    if source_id:
        return source_id
    safe_stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", path.stem).strip("_")[:80] or "track"
    return f"{safe_stem}-{stat.st_size}-{int(stat.st_mtime)}"


def _features_to_dict(features: TrackFeatures) -> dict:
    data = asdict(features)
    data["cue_hints"] = [asdict(cue) for cue in features.cue_hints]
    data["sections"] = [asdict(s) for s in features.sections]
    data["vocals"] = [asdict(v) for v in features.vocals]
    return data


def _features_from_json(text: str) -> TrackFeatures:
    data = json.loads(text)
    data["analysis_version"] = int(data.get("analysis_version", 0) or 0)
    cue_hints = []
    for cue in data.get("cue_hints", []):
        if "loop_bars" in cue and "loop_beats" not in cue:
            cue["loop_beats"] = int(cue.pop("loop_bars") or 0) * 4 or None
        else:
            cue.pop("loop_bars", None)
        cue_hints.append(CueHint(**cue))
    data["cue_hints"] = cue_hints
    data["sections"] = [Section(**s) for s in data.get("sections", [])]
    data["vocals"] = [VocalInterval(**v) for v in data.get("vocals", [])]
    # Drop unknown keys so older caches with extra fields don't crash deserialization.
    valid = {f.name for f in fields(TrackFeatures)}
    data = {k: v for k, v in data.items() if k in valid}
    return TrackFeatures(**data)


def _close(a: float, b: float, *, rel: float) -> bool:
    if b == 0:
        return abs(a - b) < rel
    return abs(a - b) / max(abs(b), 1e-9) < rel


def _as_float(value) -> float:
    arr = np.asarray(value)
    if arr.size:
        return float(arr.reshape(-1)[0])
    return float(value)
