"""End-to-end API integration test."""
from __future__ import annotations

import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app import storage
from tests.make_click import make_click_track

FIXTURE_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session")
def click_120():
    out = FIXTURE_DIR / "click_120.wav"
    if not out.exists():
        out.parent.mkdir(parents=True, exist_ok=True)
        make_click_track(out, bpm=120.0, bars=16, first_downbeat_offset_sec=0.25)
    return out


@pytest.fixture
def client():
    return TestClient(app)


def test_full_flow(client, click_120, tmp_path):
    # 1) upload + analyze
    with open(click_120, "rb") as f:
        r = client.post(
            "/api/analyze",
            files={"file": ("click_120.wav", f, "audio/wav")},
        )
    assert r.status_code == 200, r.text
    a = r.json()
    track_id = a["track_id"]
    assert 119.5 < a["bpm"] < 120.5
    assert len(a["downbeats"]) >= 4

    try:
        # 2) GET analysis
        r2 = client.get(f"/api/tracks/{track_id}")
        assert r2.status_code == 200
        assert r2.json()["track_id"] == track_id

        # 3) list tracks
        r3 = client.get("/api/tracks")
        assert r3.status_code == 200
        assert any(t["track_id"] == track_id for t in r3.json())

        # 4) audio file served
        r4 = client.get(f"/files/{track_id}")
        assert r4.status_code == 200
        assert len(r4.content) > 1000

        # 5) PUT/GET cues
        cues_payload = {
            "cues": [
                {
                    "slot": 0,
                    "name": "INTRO",
                    "position_sec": a["first_downbeat_sec"],
                    "color": [40, 226, 20],
                    "type": "hot",
                },
                {
                    "slot": None,
                    "name": "BREAK",
                    "position_sec": 8.25,
                    "color": [255, 100, 0],
                    "type": "memory",
                },
            ]
        }
        r5 = client.put(f"/api/tracks/{track_id}/cues", json=cues_payload)
        assert r5.status_code == 200
        assert r5.json()["cues"][0]["name"] == "INTRO"

        r6 = client.get(f"/api/tracks/{track_id}/cues")
        assert r6.status_code == 200
        assert len(r6.json()["cues"]) == 2

        # 6) Rekordbox XML export round-trip
        r7 = client.post("/api/export/rekordbox", json={"track_ids": [track_id]})
        assert r7.status_code == 200
        assert r7.headers["content-type"].startswith("application/xml")

        xml_path = tmp_path / "rekordbox.xml"
        xml_path.write_bytes(r7.content)
        # re-parse via pyrekordbox
        from pyrekordbox import RekordboxXml
        x = RekordboxXml(path=str(xml_path))
        assert x.num_tracks == 1
        t = x.get_tracks()[0]
        assert abs(float(t.AverageBpm) - 120.0) < 0.5
        assert len(t.tempos) >= 1
        # cues should round-trip: 1 hot + 1 memory
        marks = list(t.marks)
        nums = sorted(m.Num for m in marks)
        assert nums == [-1, 0], f"unexpected cue nums: {nums}"
    finally:
        # 7) cleanup
        client.delete(f"/api/tracks/{track_id}")
        assert storage.load_analysis(track_id) is None
