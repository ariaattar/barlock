from __future__ import annotations

import json
import math
import re
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Iterable

import numpy as np
import soundfile as sf

AUDIO_EXTS = {".mp3", ".wav", ".aiff", ".aif", ".flac", ".m4a", ".aac", ".ogg", ".opus"}
ANALYSIS_VERSION = 7

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
    )


def analysis_dir(output_dir: Path) -> Path:
    path = output_dir.expanduser() / ".track-analysis"
    path.mkdir(parents=True, exist_ok=True)
    return path


def analyze_paths(paths: Iterable[Path], *, output_dir: Path, use_cache: bool = True) -> list[TrackFeatures]:
    return [analyze_file(path, output_dir=output_dir, use_cache=use_cache) for path in paths]


def analyze_file(path: Path, *, output_dir: Path | None = None, use_cache: bool = True) -> TrackFeatures:
    path = path.expanduser().resolve()
    cache_root = analysis_dir(output_dir or path.parent)
    cache_file = cache_root / f"{_cache_key(path)}.json"
    if use_cache and cache_file.exists():
        try:
            cached = _features_from_json(cache_file.read_text())
            if cached.analysis_version < ANALYSIS_VERSION:
                raise ValueError("stale analysis cache")
            upgraded = _ensure_current_cue_hints(cached)
            if upgraded != cached:
                cache_file.write_text(json.dumps(_features_to_dict(upgraded), indent=2) + "\n")
            return upgraded
        except Exception:
            pass

    features = _analyze_uncached(path)
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
    tags.save(str(path))

    id3 = ID3(str(path))
    id3.delall("COMM:soundcloud-dl:eng")
    comment_parts = [
        f"SoundCloud: {features.source_url}" if features.source_url else "",
        f"Key: {features.musical_key} / {features.camelot_key}".strip(),
        f"BPM: {features.bpm:.2f}" if features.bpm > 0 else "",
        f"Energy: {features.energy}/10",
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


def _analyze_uncached(path: Path) -> TrackFeatures:
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
    tempo, beats = librosa.beat.beat_track(y=y, sr=sr, trim=False)
    bpm = _as_float(tempo)
    beat_times = librosa.frames_to_time(beats, sr=sr) if len(beats) else np.asarray([], dtype=float)
    first_downbeat = float(beat_times[0]) if len(beat_times) else 0.0

    musical_key, confidence = _estimate_key(y, sr)
    camelot = camelot_key(musical_key)
    rms = float(np.sqrt(np.mean(np.square(y)))) if y.size else 0.0
    peak = float(np.max(np.abs(y))) if y.size else 0.0
    loudness = _amp_to_db(rms)
    peak_db = _amp_to_db(peak)
    centroid = librosa.feature.spectral_centroid(y=y, sr=sr)
    centroid_norm = float(np.nanmean(centroid) / (sr / 2)) if centroid.size else 0.0
    energy = _energy_score(bpm=bpm, loudness_dbfs=loudness, centroid_norm=centroid_norm)
    loop_profile = _load_loop_profile(path, duration=duration)
    cues = _cue_hints(
        duration=duration,
        bpm=bpm,
        first_downbeat=first_downbeat,
        loop_profile=loop_profile,
    )

    return TrackFeatures(
        path=str(path),
        title=title,
        artist=artist,
        duration_sec=round(duration, 3),
        sample_rate=int(file_info.samplerate or sr),
        bpm=round(float(bpm), 3),
        musical_key=musical_key,
        camelot_key=camelot,
        key_confidence=round(confidence, 3),
        loudness_dbfs=round(loudness, 3),
        peak_dbfs=round(peak_db, 3),
        energy=energy,
        first_downbeat_sec=round(first_downbeat, 3),
        cue_hints=cues,
        source_id=source_id,
        analysis_version=ANALYSIS_VERSION,
    )


def _estimate_key(y: np.ndarray, sr: int) -> tuple[str, float]:
    import librosa

    chroma = librosa.feature.chroma_stft(y=y, sr=sr)
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


def _cue_hints(
    *,
    duration: float,
    bpm: float,
    first_downbeat: float,
    loop_profile: LoopProfile | None = None,
) -> list[CueHint]:
    bar = 60.0 / bpm * 4.0 if bpm > 0 else 2.0
    phrase_16 = bar * 16
    phrase_32 = bar * 32
    intro_start = max(0.0, first_downbeat)
    intro_loop = (
        _best_intro_loop_candidate(
            duration=duration,
            bar=bar,
            first_downbeat=first_downbeat,
            loop_profile=loop_profile,
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
        )
        if loop_profile is not None
        else None
    )

    values = [
        CueHint("Intro", intro_start, "hot", 0),
        CueHint("Phrase 16", max(0.0, first_downbeat + phrase_16), "hot", 1),
        CueHint("Phrase 32", max(0.0, first_downbeat + phrase_32), "hot", 2),
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
        CueHint("Outro", max(0.0, duration - phrase_32), "memory", None),
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


def _load_loop_profile(path: Path, *, duration: float) -> LoopProfile | None:
    if duration <= 0:
        return None

    import librosa

    windows: list[tuple[float, float]]
    if duration <= 420.0:
        windows = [(0.0, duration)]
    else:
        head = min(140.0, duration)
        tail = min(220.0, duration)
        tail_offset = max(0.0, duration - tail)
        windows = [(0.0, head)]
        if tail_offset > head + 5.0:
            windows.append((tail_offset, tail))

    profiles: list[LoopProfile] = []
    for offset, window in windows:
        try:
            y, sr = librosa.load(str(path), sr=22050, mono=True, offset=offset, duration=window)
        except Exception:
            continue
        profile = _loop_profile_from_audio(y, sample_rate=sr, offset_sec=offset)
        if profile is not None:
            profiles.append(profile)
    return _merge_loop_profiles(profiles)


def _loop_profile_from_audio(y: np.ndarray, *, sample_rate: int, offset_sec: float = 0.0) -> LoopProfile | None:
    if y.size == 0 or sample_rate <= 0:
        return None

    import librosa

    hop = 512
    frame_length = 2048
    rms = librosa.feature.rms(y=y, frame_length=frame_length, hop_length=hop)[0]
    if rms.size == 0:
        return None
    rms = _moving_average(np.maximum(rms, 0.0), 9)
    times = offset_sec + librosa.frames_to_time(np.arange(rms.size), sr=sample_rate, hop_length=hop)

    feature_times: np.ndarray | None = None
    onset: np.ndarray | None = None
    chroma: np.ndarray | None = None
    centroid: np.ndarray | None = None
    bandwidth: np.ndarray | None = None
    try:
        onset = librosa.onset.onset_strength(y=y, sr=sample_rate, hop_length=hop)
        onset = _moving_average(np.maximum(onset, 0.0), 5)
        chroma = librosa.feature.chroma_stft(y=y, sr=sample_rate, hop_length=hop)
        chroma = np.maximum(chroma, 0.0)
        chroma = chroma / np.maximum(np.linalg.norm(chroma, axis=0, keepdims=True), 1e-6)
        centroid = librosa.feature.spectral_centroid(y=y, sr=sample_rate, hop_length=hop)[0]
        centroid = np.maximum(centroid / max(sample_rate / 2.0, 1.0), 0.0)
        bandwidth = librosa.feature.spectral_bandwidth(y=y, sr=sample_rate, hop_length=hop)[0]
        bandwidth = np.maximum(bandwidth / max(sample_rate / 2.0, 1.0), 0.0)
        frame_count = min(onset.size, chroma.shape[1], centroid.size, bandwidth.size, times.size)
        if frame_count > 0:
            feature_times = times[:frame_count]
            onset = onset[:frame_count]
            chroma = chroma[:, :frame_count]
            centroid = centroid[:frame_count]
            bandwidth = bandwidth[:frame_count]
    except Exception:
        feature_times = None
        onset = None
        chroma = None
        centroid = None
        bandwidth = None

    return LoopProfile(
        offset_sec=offset_sec,
        sample_rate=sample_rate,
        y=y,
        rms_times=times,
        rms=rms,
        feature_times=feature_times,
        onset=onset,
        chroma=chroma,
        centroid=centroid,
        bandwidth=bandwidth,
    )


def _best_exit_loop_candidate(
    *,
    duration: float,
    bar: float,
    first_downbeat: float,
    loop_profile: LoopProfile,
    preferred_loop_beats: int | None = None,
) -> tuple[float, float, int] | None:
    if bar <= 0 or duration <= bar * 8:
        return None

    candidate_beats = _loop_beat_options(duration, bar, preferred_loop_beats=preferred_loop_beats)
    if not candidate_beats:
        return None

    keepout = min(8 * bar, max(4 * bar, duration * 0.05))
    earliest = max(first_downbeat + 32 * bar, duration * 0.55, _profile_start(loop_profile) + bar)
    latest_end = min(duration - keepout, _profile_end(loop_profile) - 0.25)
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
    return round(best[0], 3), round(best[1], 3), best[2]


def _best_intro_loop_candidate(
    *,
    duration: float,
    bar: float,
    first_downbeat: float,
    loop_profile: LoopProfile,
) -> tuple[float, float, int] | None:
    if bar <= 0 or duration <= bar * 8:
        return None

    candidate_beats = _loop_beat_options(duration, bar)
    if not candidate_beats:
        return None

    earliest = max(first_downbeat + 4 * bar, _profile_start(loop_profile) + bar)
    latest_end = min(
        first_downbeat + 96 * bar,
        120.0,
        duration - max(bar, 0.5),
        _profile_end(loop_profile) - 0.25,
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
    return round(best[0], 3), round(best[1], 3), best[2]


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
    search_start = _snap_up_to_bar(earliest, first_downbeat, bar)
    search_end = _snap_down_to_bar(latest_end, first_downbeat, bar)
    if search_end <= search_start:
        return []

    beat = bar / 4.0
    for beats in candidate_beats:
        loop_len = beats * beat
        start = search_start
        while start + loop_len <= search_end + 0.001:
            end = start + loop_len
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
            start += bar
    return candidates


def _select_loop_candidate(candidates: list[tuple[float, float, int, float]]) -> tuple[float, float, int, float] | None:
    if not candidates:
        return None

    best_by_beats: dict[int, tuple[float, float, int, float]] = {}
    for candidate in sorted(candidates, key=lambda item: item[3], reverse=True):
        best_by_beats.setdefault(candidate[2], candidate)

    best_4 = best_by_beats.get(4)
    best_8 = best_by_beats.get(8)
    if best_8 is not None and (best_4 is None or best_8[3] >= best_4[3] - 0.06):
        return best_8
    elif best_4 is not None:
        return best_4
    return max(candidates, key=lambda item: item[3])


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
        return 0.68
    return 0.62


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
    return max(0.0, min(score, 1.0))


def _merge_loop_profiles(profiles: list[LoopProfile]) -> LoopProfile | None:
    profiles = [profile for profile in profiles if profile.rms_times.size and profile.rms.size]
    if not profiles:
        return None
    if len(profiles) == 1:
        return profiles[0]

    profiles.sort(key=lambda profile: float(profile.rms_times[0]))
    sample_rate = profiles[0].sample_rate
    y = np.concatenate([profile.y for profile in profiles if profile.y.size])
    rms_times = np.concatenate([profile.rms_times for profile in profiles])
    rms = np.concatenate([profile.rms for profile in profiles])

    if all(profile.feature_times is not None for profile in profiles):
        feature_times = np.concatenate([profile.feature_times for profile in profiles if profile.feature_times is not None])
        onset = (
            np.concatenate([profile.onset for profile in profiles if profile.onset is not None])
            if all(profile.onset is not None for profile in profiles)
            else None
        )
        centroid = (
            np.concatenate([profile.centroid for profile in profiles if profile.centroid is not None])
            if all(profile.centroid is not None for profile in profiles)
            else None
        )
        bandwidth = (
            np.concatenate([profile.bandwidth for profile in profiles if profile.bandwidth is not None])
            if all(profile.bandwidth is not None for profile in profiles)
            else None
        )
        chroma = (
            np.concatenate([profile.chroma for profile in profiles if profile.chroma is not None], axis=1)
            if all(profile.chroma is not None for profile in profiles)
            else None
        )
    else:
        feature_times = None
        onset = None
        centroid = None
        bandwidth = None
        chroma = None

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
    )


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
    return TrackFeatures(**data)


def _ensure_current_cue_hints(features: TrackFeatures) -> TrackFeatures:
    if _cue_layout_is_current(features.cue_hints):
        return features
    return replace(
        features,
        analysis_version=ANALYSIS_VERSION,
        cue_hints=_cue_hints(
            duration=features.duration_sec,
            bpm=features.bpm,
            first_downbeat=features.first_downbeat_sec,
        ),
    )


def _cue_layout_is_current(cues: list[CueHint]) -> bool:
    by_name = {cue.name: cue for cue in cues}
    required = {
        "Intro": ("hot", 0),
        "Phrase 16": ("hot", 1),
        "Phrase 32": ("hot", 2),
        "Outro": ("memory", None),
    }
    optional = {
        "Intro Loop": ("loop", 3),
        "Exit Loop": ("loop", 4),
    }
    for name, (kind, slot) in required.items():
        cue = by_name.get(name)
        if cue is None or cue.kind != kind or cue.hotcue_slot != slot:
            return False
    for name, (kind, slot) in optional.items():
        cue = by_name.get(name)
        if cue is None:
            continue
        if cue.kind != kind or cue.hotcue_slot != slot or cue.end_seconds is None:
            return False
        if cue.loop_beats not in {4, 8}:
            return False
    return True


def _as_float(value) -> float:
    arr = np.asarray(value)
    if arr.size:
        return float(arr.reshape(-1)[0])
    return float(value)
