from __future__ import annotations

from pathlib import Path

import numpy as np

from app.audio_features import (
    CueHint,
    LoopProfile,
    TrackFeatures,
    _best_exit_loop_candidate,
    _best_intro_loop_candidate,
    _cue_hints,
    _cue_layout_is_current,
    _ensure_current_cue_hints,
    _loop_candidate_score,
    _source_id_from_filename,
    _title_artist_from_filename,
    camelot_key,
)


def test_camelot_key_mapping_prefers_rekordbox_style_flats():
    assert camelot_key("Dm") == "7A"
    assert camelot_key("C#") == "3B"
    assert camelot_key("C#m") == "12A"


def test_filename_metadata_parsing_with_soundcloud_id():
    path = Path("ABBA - Gimme! Gimme! Gimme! (VIZON Remix) [2256704372].mp3")

    title, artist = _title_artist_from_filename(path)

    assert artist == "ABBA"
    assert title == "Gimme! Gimme! Gimme! (VIZON Remix)"
    assert _source_id_from_filename(path) == "2256704372"


def test_auto_cue_hints_include_landmarks_without_audio_profile():
    cues = _cue_hints(duration=240.0, bpm=120.0, first_downbeat=0.5)

    by_name = {cue.name: cue for cue in cues}

    assert by_name["Intro"].hotcue_slot == 0
    assert by_name["Phrase 16"].hotcue_slot == 1
    assert by_name["Phrase 16"].seconds == 32.5
    assert by_name["Phrase 32"].hotcue_slot == 2
    assert by_name["Outro"].kind == "memory"
    assert "Intro Loop" not in by_name
    assert "Exit Loop" not in by_name


def test_auto_cue_hints_write_scored_intro_and_exit_loops():
    duration = 240.0
    times = np.linspace(0.0, duration, 481)
    rms = np.full(times.shape, 0.08)
    rms[(times >= 16.0) & (times <= 96.0)] = 0.70
    rms[(times >= 144.0) & (times <= 214.0)] = 0.80
    rms[times > 214.0] = np.linspace(0.80, 0.04, int((times > 214.0).sum()))
    profile = _profile_from_rms(duration, times, rms)

    cues = _cue_hints(duration=duration, bpm=120.0, first_downbeat=0.5, loop_profile=profile)

    by_name = {cue.name: cue for cue in cues}
    intro_loop = by_name["Intro Loop"]
    exit_loop = by_name["Exit Loop"]
    assert intro_loop.hotcue_slot == 3
    assert intro_loop.seconds >= 16.0
    assert intro_loop.end_seconds > intro_loop.seconds
    assert intro_loop.loop_beats in {4, 8}
    assert _is_beat_grid_line(intro_loop.seconds, first_downbeat=0.5, beat=0.5)
    assert _is_beat_grid_line(intro_loop.end_seconds, first_downbeat=0.5, beat=0.5)
    assert exit_loop.hotcue_slot == 4
    assert exit_loop.end_seconds <= 214.5
    assert exit_loop.loop_beats in {4, 8}
    assert _is_beat_grid_line(exit_loop.seconds, first_downbeat=0.5, beat=0.5)
    assert _is_beat_grid_line(exit_loop.end_seconds, first_downbeat=0.5, beat=0.5)


def test_exit_loop_candidate_avoids_tail_fade_and_uses_stable_audio():
    duration = 240.0
    times = np.linspace(0.0, duration, 481)
    rms = np.full(times.shape, 0.10)
    stable = (times >= 144.0) & (times <= 214.0)
    rms[stable] = 0.80
    fade = times > 214.0
    rms[fade] = np.linspace(0.80, 0.04, int(fade.sum()))
    profile = _profile_from_rms(duration, times, rms)

    candidate = _best_exit_loop_candidate(
        duration=duration,
        bar=2.0,
        first_downbeat=0.5,
        preferred_loop_beats=8,
        loop_profile=profile,
    )

    assert candidate is not None
    start, end, bars = candidate
    assert 144.0 <= start
    assert end <= 214.5
    assert bars in {4, 8}
    assert _is_beat_grid_line(start, first_downbeat=0.5, beat=0.5)
    assert _is_beat_grid_line(end, first_downbeat=0.5, beat=0.5)


def test_intro_loop_candidate_skips_silent_opening():
    duration = 120.0
    times = np.linspace(0.0, duration, 241)
    rms = np.full(times.shape, 0.04)
    rms[(times >= 24.0) & (times <= 96.0)] = 0.75
    profile = _profile_from_rms(duration, times, rms)

    candidate = _best_intro_loop_candidate(
        duration=duration,
        bar=2.0,
        first_downbeat=0.5,
        loop_profile=profile,
    )

    assert candidate is not None
    start, end, bars = candidate
    assert start >= 24.0
    assert end > start
    assert bars in {4, 8}
    assert _is_beat_grid_line(start, first_downbeat=0.5, beat=0.5)
    assert _is_beat_grid_line(end, first_downbeat=0.5, beat=0.5)


def test_loop_candidate_rejects_final_bar_fill():
    duration = 96.0
    times = np.linspace(0.0, duration, 193)
    rms = np.full(times.shape, 0.65)
    rms[(times >= 30.0) & (times <= 32.0)] = 2.20
    profile = _profile_from_rms(duration, times, rms)

    score = _loop_candidate_score(
        profile,
        start=28.0,
        end=32.0,
        bar=2.0,
        duration=duration,
        role="intro",
    )

    assert score is None


def test_cached_features_without_loops_are_upgraded():
    features = TrackFeatures(
        path="/tmp/test.mp3",
        title="Test",
        artist="Artist",
        duration_sec=240.0,
        sample_rate=44100,
        bpm=120.0,
        musical_key="Dm",
        camelot_key="7A",
        key_confidence=0.8,
        loudness_dbfs=-10.0,
        peak_dbfs=-0.1,
        energy=8,
        first_downbeat_sec=0.5,
        cue_hints=[CueHint("Intro", 0.5, "hot", 0)],
    )

    upgraded = _ensure_current_cue_hints(features)

    assert not any(cue.kind == "loop" for cue in upgraded.cue_hints)
    assert _cue_layout_is_current(upgraded.cue_hints)


def test_cached_features_with_old_loop_layout_are_upgraded():
    features = TrackFeatures(
        path="/tmp/test.mp3",
        title="Test",
        artist="Artist",
        duration_sec=240.0,
        sample_rate=44100,
        bpm=120.0,
        musical_key="Dm",
        camelot_key="7A",
        key_confidence=0.8,
        loudness_dbfs=-10.0,
        peak_dbfs=-0.1,
        energy=8,
        first_downbeat_sec=0.5,
        cue_hints=[
            CueHint("Intro", 0.5, "hot", 0),
            CueHint("Intro Loop", 0.5, "loop", 1, 32.5, 16),
            CueHint("Phrase 32", 64.5, "hot", 2),
            CueHint("Exit Loop", 198.5, "loop", 3, 230.5, 16),
            CueHint("Outro", 176.0, "memory", None),
        ],
    )

    upgraded = _ensure_current_cue_hints(features)

    assert _cue_layout_is_current(upgraded.cue_hints)
    by_name = {cue.name: cue for cue in upgraded.cue_hints}
    assert by_name["Phrase 16"].hotcue_slot == 1
    assert "Intro Loop" not in by_name
    assert "Exit Loop" not in by_name


def _profile_from_rms(duration: float, times: np.ndarray, rms: np.ndarray) -> LoopProfile:
    return LoopProfile(
        offset_sec=0.0,
        sample_rate=10,
        y=np.zeros(int(duration * 10)),
        rms_times=times,
        rms=rms,
    )


def _is_beat_grid_line(value: float, *, first_downbeat: float, beat: float) -> bool:
    return abs(round((value - first_downbeat) / beat) - ((value - first_downbeat) / beat)) < 0.002
