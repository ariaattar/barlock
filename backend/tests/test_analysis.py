"""Beat/bar analysis verification against a known-truth click track."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.analysis import analyze
from tests.make_click import make_click_track

FIXTURE_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session")
def click_120(tmp_path_factory):
    out = FIXTURE_DIR / "click_120.wav"
    if not out.exists():
        out.parent.mkdir(parents=True, exist_ok=True)
        make_click_track(out, bpm=120.0, bars=16, first_downbeat_offset_sec=0.25)
    return out


def test_bpm_within_tolerance(click_120):
    r = analyze("click120", click_120)
    assert abs(r.bpm - 120.0) < 0.5, f"BPM off: {r.bpm}"


def test_first_downbeat_within_tolerance(click_120):
    r = analyze("click120", click_120)
    # We allowed analyzer to lock onto the nearest detected beat; truth = 0.25s.
    assert abs(r.first_downbeat_sec - 0.25) < 0.05, (
        f"first_downbeat off: {r.first_downbeat_sec}"
    )


def test_bar_grid_is_4_beats_apart(click_120):
    r = analyze("click120", click_120)
    seconds_per_bar = 60.0 / 120.0 * 4  # = 2.0s at 120 BPM 4/4
    assert len(r.downbeats) >= 4, f"too few downbeats: {len(r.downbeats)}"
    diffs = [r.downbeats[i + 1] - r.downbeats[i] for i in range(len(r.downbeats) - 1)]
    # All bar gaps should be close to 2.0s.
    for i, d in enumerate(diffs):
        assert abs(d - seconds_per_bar) < 0.05, (
            f"bar {i}->{i+1} gap = {d}s, expected {seconds_per_bar}"
        )


def test_beats_match_expected_count(click_120):
    # 16 bars * 4 beats = 64 beats; allow ±2 for edge effects.
    r = analyze("click120", click_120)
    assert 62 <= len(r.beats) <= 66, f"unexpected beat count: {len(r.beats)}"
