from __future__ import annotations

from pathlib import Path

import numpy as np
import soundfile as sf

from app.audio_features import (
    CueHint,
    LoopProfile,
    Section,
    TrackFeatures,
    VocalInterval,
    _best_exit_loop_candidate,
    _best_intro_loop_candidate,
    _build_energy_curve,
    _classify_vocal,
    _cue_hints,
    _exit_entry_transient_penalty,
    _heuristic_drop_breakdown,
    _loop_candidate_score,
    _select_loop_candidate,
    _snap_loop_end_to_beat,
    _source_id_from_filename,
    _title_artist_from_filename,
    _vocal_overlap_fraction,
    camelot_key,
    metadata_only_features,
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


def test_metadata_only_features_reads_file_without_analysis(tmp_path):
    path = tmp_path / "Artist - Track [12345].wav"
    sf.write(path, np.zeros(22050, dtype=np.float32), 22050)

    features = metadata_only_features(path)

    assert features.title == "Track"
    assert features.artist == "Artist"
    assert features.source_id == "12345"
    assert features.duration_sec == 1.0
    assert features.sample_rate == 22050
    assert features.bpm == 0.0
    assert features.cue_hints == []
    assert features.analysis_version == 0


def test_heuristic_cue_layout_uses_phrase_landmarks():
    cues = _cue_hints(duration=240.0, bpm=120.0, first_downbeat=0.5)

    by_name = {cue.name: cue for cue in cues}

    assert by_name["Intro"].hotcue_slot == 0
    assert by_name["Phrase 16"].hotcue_slot == 1
    assert by_name["Phrase 16"].seconds == 32.5
    assert by_name["Phrase 32"].hotcue_slot == 2
    assert "Outro" not in by_name
    assert "Intro Loop" not in by_name
    assert "Exit Loop" not in by_name


def test_structural_cue_layout_uses_section_labels():
    sections = [
        Section(start_sec=0.0, end_sec=32.0, label="intro", confidence=0.9),
        Section(start_sec=32.0, end_sec=64.0, label="build", confidence=0.9),
        Section(start_sec=64.0, end_sec=128.0, label="drop", confidence=0.9),
        Section(start_sec=128.0, end_sec=160.0, label="breakdown", confidence=0.9),
        Section(start_sec=160.0, end_sec=192.0, label="last_drop", confidence=0.9),
        Section(start_sec=192.0, end_sec=240.0, label="outro", confidence=0.9),
    ]

    cues = _cue_hints(
        duration=240.0,
        bpm=120.0,
        first_downbeat=0.5,
        sections=sections,
        segmentation_mode="structural",
    )

    by_name = {cue.name: cue for cue in cues}
    # Pad C is always the drop slot — name changes between modes but slot is stable.
    assert by_name["Drop"].hotcue_slot == 2
    assert by_name["Build"].hotcue_slot == 1
    assert by_name["Breakdown"].hotcue_slot == 5
    assert by_name["Last Drop"].hotcue_slot == 6
    assert "Outro" not in by_name


def test_pad_c_is_drop_slot_in_both_modes():
    heuristic = _cue_hints(duration=240.0, bpm=120.0, first_downbeat=0.5)
    sections = [
        Section(start_sec=0.0, end_sec=32.0, label="intro", confidence=0.9),
        Section(start_sec=32.0, end_sec=96.0, label="drop", confidence=0.9),
        Section(start_sec=96.0, end_sec=240.0, label="outro", confidence=0.9),
    ]
    structural = _cue_hints(
        duration=240.0,
        bpm=120.0,
        first_downbeat=0.5,
        sections=sections,
        segmentation_mode="structural",
    )
    heur_pad_c = next(c for c in heuristic if c.hotcue_slot == 2)
    struct_pad_c = next(c for c in structural if c.hotcue_slot == 2)
    assert heur_pad_c.name == "Phrase 32"
    assert struct_pad_c.name == "Drop"
    assert heur_pad_c.kind == "hot"
    assert struct_pad_c.kind == "hot"


def test_pad_c_uses_heuristic_drop_when_energy_peak_is_confident():
    """Even without structural mode, a confident energy-curve drop should land
    on pad C as 'Drop' instead of falling back to bar-32 Phrase 32."""
    curve = [0.2] * 256
    for idx in range(80, 110):
        curve[idx] = 0.9  # clear peak around 62-86s of a 200s track

    cues = _cue_hints(
        duration=200.0,
        bpm=120.0,
        first_downbeat=0.0,
        energy_curve=curve,
        # heuristic mode is the default — no structural confidence
    )

    pad_c = next(c for c in cues if c.hotcue_slot == 2)
    assert pad_c.name == "Drop"
    assert pad_c.kind == "hot"
    # And it lands somewhere inside the energy peak window
    assert 60.0 <= pad_c.seconds <= 90.0


def test_pad_b_uses_section_build_in_heuristic_mode():
    """A section labeled 'build' should claim pad B even when overall
    segmentation confidence is too low for structural mode."""
    sections = [
        Section(start_sec=0.0, end_sec=24.0, label="intro", confidence=0.5),
        Section(start_sec=24.0, end_sec=48.0, label="build", confidence=0.5),
        Section(start_sec=48.0, end_sec=200.0, label="outro", confidence=0.5),
    ]
    cues = _cue_hints(
        duration=200.0,
        bpm=120.0,
        first_downbeat=0.0,
        sections=sections,
        segmentation_mode="heuristic",
    )
    pad_b = next(c for c in cues if c.hotcue_slot == 1)
    assert pad_b.name == "Build"
    assert pad_b.seconds == 24.0


def test_pad_f_breakdown_avoids_clashing_with_pad_c_drop():
    """When sections place Drop and Breakdown within a bar of each other we
    should drop the Breakdown rather than have two adjacent hot cues."""
    sections = [
        Section(start_sec=0.0, end_sec=30.0, label="intro", confidence=0.9),
        Section(start_sec=30.0, end_sec=33.0, label="breakdown", confidence=0.9),
        Section(start_sec=33.0, end_sec=96.0, label="drop", confidence=0.9),
        Section(start_sec=96.0, end_sec=240.0, label="outro", confidence=0.9),
    ]
    cues = _cue_hints(
        duration=240.0,
        bpm=120.0,
        first_downbeat=0.0,
        sections=sections,
        segmentation_mode="structural",
    )
    assert any(c.name == "Drop" for c in cues)
    assert not any(c.name == "Breakdown" for c in cues)


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


def test_exit_loop_entry_penalty_prefers_next_clean_beat_after_pickup():
    beat = 0.5
    times = np.linspace(70.0, 76.0, 121)
    onset = np.full(times.shape, 1.0)
    onset[(times >= 72.0) & (times < 72.5)] = 5.0
    onset[(times >= 72.5) & (times < 73.0)] = 1.1
    profile = LoopProfile(
        offset_sec=70.0,
        sample_rate=10,
        y=np.zeros(60),
        rms_times=times,
        rms=np.full(times.shape, 0.7),
        feature_times=times,
        onset=onset,
    )

    assert _exit_entry_transient_penalty(profile, start=72.0, beat=beat) > 0.5
    assert _exit_entry_transient_penalty(profile, start=72.5, beat=beat) < 0.1


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


def test_intro_loop_rejects_high_confidence_vocal_overlap():
    duration = 120.0
    times = np.linspace(0.0, duration, 241)
    rms = np.full(times.shape, 0.04)
    rms[(times >= 24.0) & (times <= 96.0)] = 0.75
    vocals = (VocalInterval(start_sec=28.0, end_sec=50.0, confidence=0.95),)
    profile = LoopProfile(
        offset_sec=0.0,
        sample_rate=10,
        y=np.zeros(int(duration * 10)),
        rms_times=times,
        rms=rms,
        feature_times=times,
        onset=np.full(times.shape, 0.6),
        vocals=vocals,
    )

    candidate = _best_intro_loop_candidate(
        duration=duration,
        bar=2.0,
        first_downbeat=0.5,
        loop_profile=profile,
    )

    # If a candidate is still chosen, it must not overlap the vocal interval by more than 25%.
    if candidate is not None:
        start, end, _ = candidate
        overlap = max(0.0, min(end, 50.0) - max(start, 28.0))
        assert overlap / max(end - start, 1e-6) <= 0.25


def test_vocal_overlap_fraction_respects_min_confidence():
    vocals = [
        VocalInterval(start_sec=10.0, end_sec=20.0, confidence=0.4),
        VocalInterval(start_sec=30.0, end_sec=34.0, confidence=0.9),
    ]
    assert _vocal_overlap_fraction(vocals, 10.0, 20.0, min_confidence=0.6) == 0.0
    assert _vocal_overlap_fraction(vocals, 30.0, 34.0, min_confidence=0.6) == 1.0


def test_vocal_classification_thresholds():
    assert _classify_vocal(0.0) == "unknown"
    assert _classify_vocal(0.03) == "instrumental"
    assert _classify_vocal(0.10) == "dub"
    assert _classify_vocal(0.40) == "vocal"


def test_loop_end_snap_pulls_to_nearest_beat_within_window():
    bts = np.asarray([0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0])
    profile = LoopProfile(
        offset_sec=0.0,
        sample_rate=10,
        y=np.zeros(60),
        rms_times=np.linspace(0.0, 5.0, 11),
        rms=np.full(11, 0.7),
        beat_times=bts,
    )

    start, end, beats = _snap_loop_end_to_beat(0.0, 4.03, 8, profile, bar=2.0)
    assert end == 4.0
    start, end, beats = _snap_loop_end_to_beat(0.0, 4.20, 8, profile, bar=2.0)
    assert end == 4.20


def test_energy_curve_has_fixed_length_and_unit_range():
    from app.audio_features import _FeatureBundle

    sr = 22050
    duration = 64.0
    times = np.linspace(0.0, duration, 256)
    rms = np.concatenate([np.full(85, 0.05), np.full(86, 0.5), np.full(85, 0.05)])
    rms = rms[: times.size]
    onset = np.maximum(rms - 0.05, 0.0)
    bundle = _FeatureBundle(
        y=np.zeros(int(sr * duration)),
        sr=sr,
        duration_full=duration,
        duration_analysis=duration,
        hop=512,
        times=times,
        rms=rms,
        onset=onset,
        chroma=np.zeros((12, times.size)),
        centroid=np.zeros(times.size),
        bandwidth=np.zeros(times.size),
        tempo=120.0,
        beat_times=np.arange(0.0, duration, 0.5),
        bpm_alternates=[],
        tempo_stable=True,
        first_downbeat=0.0,
        beat_phase_confidence=1.0,
    )

    curve = _build_energy_curve(bundle)
    assert len(curve) == 256
    assert all(0.0 <= v <= 1.0 for v in curve)
    # The middle third should be brighter than the outer thirds for this synthetic signal.
    first_third = float(np.mean(curve[: 256 // 3]))
    middle = float(np.mean(curve[256 // 3 : (2 * 256) // 3]))
    last_third = float(np.mean(curve[(2 * 256) // 3 :]))
    assert middle > first_third
    assert middle > last_third


def test_heuristic_drop_breakdown_thresholds():
    duration = 200.0
    curve = [0.2] * 256
    for idx in range(80, 110):
        curve[idx] = 0.9
    for idx in range(180, 200):
        curve[idx] = 0.1

    breakdown_sec, drop_sec = _heuristic_drop_breakdown(
        duration=duration,
        bar=2.0,
        first_downbeat=0.0,
        energy_curve=curve,
    )

    assert drop_sec is not None
    assert 60.0 <= drop_sec <= 90.0
    assert breakdown_sec is None or breakdown_sec >= 48.0


def test_plateau_select_prefers_robust_candidate_over_spike():
    candidates = [
        (20.0, 24.0, 8, 0.84),
        (20.0 + 0.5, 24.5, 8, 0.85),
        (20.0 + 1.0, 25.0, 8, 0.83),
        (40.0, 44.0, 8, 0.86),
        (40.5, 44.5, 8, 0.60),
        (41.0, 45.0, 8, 0.61),
    ]
    chosen = _select_loop_candidate(candidates)
    assert chosen is not None
    # The plateau cluster around t=20 wins despite the spike's slightly higher peak at t=40.
    assert 19.5 <= chosen[0] <= 21.5


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
