from __future__ import annotations

import json
from types import SimpleNamespace

import numpy as np
import pytest
import soundfile as sf

from app.audio_features import CueHint, TrackFeatures
from app.editor_service import (
    _audio_fingerprint,
    _connect,
    _default_markers,
    _normalise_corrections,
    _record_cue_provenance,
    _write_editor_tags,
    _write_editor_cues,
    build_constant_grid,
    prepare_audition,
    rebase_draft,
    save_draft,
)
from app.soundcloud_common import features_to_dict


def _features(path: str = "/tmp/track.mp3") -> TrackFeatures:
    return TrackFeatures(
        path=path,
        title="Track",
        artist="Artist",
        duration_sec=240.0,
        sample_rate=44100,
        bpm=120.0,
        musical_key="Am",
        camelot_key="8A",
        key_confidence=0.9,
        loudness_dbfs=-10.0,
        peak_dbfs=-1.0,
        energy=7,
        first_downbeat_sec=0.5,
        cue_hints=[
            CueHint("Intro", 0.5, "hot", 0),
            CueHint("Phrase 16", 32.5, "hot", 1),
            CueHint("Phrase 32", 64.5, "hot", 2),
            CueHint("Intro Loop", 8.5, "loop", 3, 12.5, 8),
            CueHint("Exit Loop", 200.5, "loop", 4, 204.5, 8),
            CueHint("Outro", 220.5, "memory", None),
        ],
    )


def test_constant_grid_extends_back_from_first_downbeat_with_bar_numbers():
    grid = build_constant_grid(3.0, 120.0, 1.2)

    assert grid[:4] == [
        {"beat_number": 3, "bpm": 120.0, "time_sec": 0.2},
        {"beat_number": 4, "bpm": 120.0, "time_sec": 0.7},
        {"beat_number": 1, "bpm": 120.0, "time_sec": 1.2},
        {"beat_number": 2, "bpm": 120.0, "time_sec": 1.7},
    ]


def test_normalise_corrections_snaps_markers_and_derives_exact_loop_end():
    features = _features()
    markers = _default_markers(features)
    for marker in markers:
        if marker["role"] == "exit_loop":
            marker["seconds"] = 201.31
            marker["snap_mode"] = "beat"
            marker["loop_beats"] = 4

    result = _normalise_corrections(
        {"bpm": 120, "first_downbeat_sec": 0.5, "camelot_key": "8A", "markers": markers},
        features,
    )
    loop = next(marker for marker in result["markers"] if marker["role"] == "exit_loop")
    grid = result["grid"]
    start_index = next(index for index, beat in enumerate(grid) if beat["time_sec"] == loop["seconds"])

    assert loop["loop_beats"] == 4
    assert loop["end_seconds"] == grid[start_index + 4]["time_sec"]
    assert loop["end_seconds"] - loop["seconds"] == pytest.approx(2.0)


def test_read_only_variable_grid_is_preserved_exactly():
    features = _features()
    grid = [
        {"beat_number": 1, "bpm": 120.0, "time_sec": 0.5},
        {"beat_number": 2, "bpm": 121.0, "time_sec": 0.996},
        {"beat_number": 3, "bpm": 119.5, "time_sec": 1.498},
        {"beat_number": 4, "bpm": 120.5, "time_sec": 1.996},
        {"beat_number": 1, "bpm": 122.0, "time_sec": 2.488},
        {"beat_number": 2, "bpm": 121.5, "time_sec": 2.982},
        {"beat_number": 3, "bpm": 121.0, "time_sec": 3.478},
        {"beat_number": 4, "bpm": 120.0, "time_sec": 3.978},
        {"beat_number": 1, "bpm": 119.0, "time_sec": 4.482},
    ]

    result = _normalise_corrections(
        {
            "bpm": 120,
            "first_downbeat_sec": 0.5,
            "camelot_key": "8A",
            "markers": _default_markers(features),
            "write_grid": False,
            "grid": grid,
        },
        features,
    )

    assert result["write_grid"] is False
    assert result["grid"] == grid


def test_draft_save_uses_optimistic_revision(monkeypatch, tmp_path):
    monkeypatch.setenv("SOUNDCLOUD_DL_APP_DATA_DIR", str(tmp_path / "app"))
    features = _features(str(tmp_path / "track.mp3"))
    corrections = _normalise_corrections(
        {"bpm": 120, "first_downbeat_sec": 0.5, "camelot_key": "8A", "markers": _default_markers(features)},
        features,
    )
    connection = _connect()
    connection.execute(
        "INSERT INTO track_drafts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("draft", "path:track", "", features.path, "audio", "", "", 1, json.dumps(features_to_dict(features)), json.dumps(corrections), "draft", "now", "now"),
    )
    connection.commit()
    connection.close()

    corrections["bpm"] = 124
    saved = save_draft({"draft_id": "draft", "revision": 1, "corrections": corrections})

    assert saved["revision"] == 2
    assert saved["corrections"]["bpm"] == 124
    with pytest.raises(RuntimeError, match="changed in another editor"):
        save_draft({"draft_id": "draft", "revision": 1, "corrections": corrections})


def test_interrupted_file_transaction_is_restored_on_next_open(monkeypatch, tmp_path):
    from app import editor_service

    monkeypatch.setenv("SOUNDCLOUD_DL_APP_DATA_DIR", str(tmp_path / "app"))
    monkeypatch.setattr(editor_service, "rekordbox_running", lambda: False)
    target = tmp_path / "track.mp3"
    backup = tmp_path / "track.backup.mp3"
    target.write_bytes(b"changed")
    backup.write_bytes(b"original")
    connection = _connect()
    connection.execute(
        """INSERT INTO edit_transactions
           (id, draft_id, idempotency_key, state, backup_path, journal_json, error, created_at, updated_at)
           VALUES ('tx', 'draft', 'key', 'files_written', ?, ?, '', 'now', 'now')""",
        (str(tmp_path), json.dumps([{"backup": str(backup), "target": str(target)}])),
    )
    connection.commit()
    connection.close()

    recovered = _connect()
    state = recovered.execute("SELECT state FROM edit_transactions WHERE id='tx'").fetchone()[0]
    recovered.close()

    assert target.read_bytes() == b"original"
    assert state == "rolled_back"


def test_stale_local_draft_rebases_onto_current_audio(monkeypatch, tmp_path):
    from app import editor_service

    monkeypatch.setenv("SOUNDCLOUD_DL_APP_DATA_DIR", str(tmp_path / "app"))
    monkeypatch.setattr(editor_service, "rekordbox_running", lambda: False)
    audio = tmp_path / "track.mp3"
    audio.write_bytes(b"current audio")
    features = _features(str(audio))
    grid = build_constant_grid(features.duration_sec, features.bpm, features.first_downbeat_sec)
    waveform = {
        "source": "local_analysis", "content_id": "", "title": features.title,
        "duration_sec": features.duration_sec, "fingerprint": "current-waveform", "files": [],
        "beat_grid": grid, "preview": {"tag": "LOCAL", "heights": [0.5], "colors": [[1, 2, 3]]},
        "detail": {"tag": "LOCAL", "heights": [0.5], "colors": [[1, 2, 3]]}, "cues": [],
    }
    monkeypatch.setattr(editor_service, "analyze_file", lambda *_args, **_kwargs: features)
    monkeypatch.setattr(editor_service, "_local_waveform", lambda *_args, **_kwargs: waveform)
    corrections = _normalise_corrections(
        {"bpm": 122, "first_downbeat_sec": 0.5, "camelot_key": "8A", "markers": _default_markers(features)},
        features,
    )
    connection = _connect()
    connection.execute(
        "INSERT INTO track_drafts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("draft", f"path:{audio}", "", str(audio), "old-audio", "", "", 1, json.dumps(features_to_dict(features)), json.dumps(corrections), "draft", "now", "now"),
    )
    connection.commit()
    connection.close()

    rebased = rebase_draft({"draft_id": "draft", "revision": 1})

    assert rebased["draft"]["revision"] == 2
    assert rebased["draft"]["status"] == "draft"
    assert rebased["track"]["audio_fingerprint"] == _audio_fingerprint(audio)
    assert rebased["draft"]["corrections"]["bpm"] == 122


def test_prepare_audition_renders_a_short_pcm_window(monkeypatch, tmp_path):
    monkeypatch.setenv("SOUNDCLOUD_DL_APP_DATA_DIR", str(tmp_path / "app"))
    audio = tmp_path / "track.wav"
    sample_rate = 44100
    seconds = np.arange(sample_rate * 8) / sample_rate
    sf.write(audio, np.sin(seconds * 2 * np.pi * 220) * 0.1, sample_rate)
    features = _features(str(audio))
    features = TrackFeatures(**{**features.__dict__, "duration_sec": 8.0})
    markers = _default_markers(features)
    for marker in markers:
        if marker["role"] == "intro_loop":
            marker.update(seconds=1.0, end_seconds=3.0, loop_beats=4, snap_mode="beat")
    corrections = _normalise_corrections(
        {"bpm": 120, "first_downbeat_sec": 0, "camelot_key": "8A", "markers": markers},
        features,
    )
    connection = _connect()
    connection.execute(
        "INSERT INTO track_drafts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("draft", "path:track", "", str(audio), _audio_fingerprint(audio), "", "", 1, json.dumps(features_to_dict(features)), json.dumps(corrections), "draft", "now", "now"),
    )
    connection.commit()
    connection.close()

    result = prepare_audition({"draft_id": "draft", "role": "intro_loop"})
    info = sf.info(result["path"])

    assert result["loop_start_sec"] == pytest.approx(1.0)
    assert result["loop_end_sec"] == pytest.approx(3.0)
    assert info.samplerate == 44100
    assert info.duration == pytest.approx(4.0, abs=0.02)


def test_editor_tag_write_only_changes_bpm_and_key(tmp_path):
    from mutagen.easyid3 import EasyID3

    audio = tmp_path / "track.mp3"
    audio.write_bytes(b"")
    original = EasyID3()
    original["title"] = "Manual title"
    original["artist"] = "Manual artist"
    original.save(audio)
    features = _features(str(audio))

    _write_editor_tags(audio, features)
    tags = EasyID3(audio)

    assert tags["title"] == ["Manual title"]
    assert tags["artist"] == ["Manual artist"]
    assert tags["bpm"] == ["120.0"]
    assert tags["initialkey"] == ["8A"]


class _Query:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows


class _CueDb:
    def __init__(self, cues):
        self.cues = cues
        self.added = []

    def get_cue(self, **_kwargs):
        return _Query(self.cues)

    def add(self, value):
        self.added.append(value)

    def generate_unused_id(self, *_args, **_kwargs):
        return "new-cue"


def _cue(**changes):
    values = {
        "ID": "manual", "Kind": 1, "InMsec": 500, "OutMsec": -1,
        "CueMicrosec": None, "Color": -1, "ColorTableIndex": None,
        "ActiveLoop": None, "Comment": "Manual", "BeatLoopSize": None,
    }
    values.update(changes)
    return SimpleNamespace(**values)


def test_editor_cue_write_preserves_unowned_occupied_slot():
    db = _CueDb([_cue()])
    content = SimpleNamespace(ID="track", UUID="uuid", HotCueAutoLoad="off", CueUpdated="0")
    marker = _default_markers(_features())[0]

    written = _write_editor_cues(db, content, [marker], {})

    assert written == {}
    assert db.cues[0].InMsec == 500
    assert db.cues[0].Comment == "Manual"
    assert db.added == []


def test_editor_cue_write_updates_only_explicitly_owned_cue():
    owned = _cue(ID="owned", InMsec=500, Comment="Intro")
    db = _CueDb([owned])
    content = SimpleNamespace(ID="track", UUID="uuid", HotCueAutoLoad="off", CueUpdated="0")
    marker = {**_default_markers(_features())[0], "seconds": 4.5}

    written = _write_editor_cues(db, content, [marker], {"intro": {"cue_id": "owned"}})

    assert written == {"intro": "owned"}
    assert owned.InMsec == 4500
    assert owned.Comment == "Intro"
    assert content.HotCueAutoLoad == "on"


def test_cue_provenance_waits_for_the_final_transaction_commit(monkeypatch, tmp_path):
    monkeypatch.setenv("SOUNDCLOUD_DL_APP_DATA_DIR", str(tmp_path / "app"))
    connection = _connect()
    row = {
        "content_id": "track",
        "track_key": "rekordbox:track",
        "revision": 3,
    }
    marker = _default_markers(_features())[0]

    _record_cue_provenance(
        connection,
        row,
        [marker],
        {"intro": "cue-id"},
        cue_digest="current-digest",
    )
    assert connection.execute("SELECT COUNT(*) FROM cue_provenance").fetchone()[0] == 1
    connection.rollback()
    assert connection.execute("SELECT COUNT(*) FROM cue_provenance").fetchone()[0] == 0
    connection.close()


def test_editor_never_generates_memory_cues_even_from_legacy_hints():
    markers = _default_markers(_features())
    assert [marker["pad"] for marker in markers] == ["A", "B", "C", "D", "E"]
    assert all(marker["kind"] != "memory" for marker in markers)
    legacy = {"role": "outro", "kind": "memory", "seconds": 220.5}
    corrected = _normalise_corrections({"markers": [*markers, legacy]}, _features())
    assert all(marker["kind"] != "memory" for marker in corrected["markers"])


def test_connect_does_not_recover_an_active_editor_transaction(monkeypatch, tmp_path):
    from app.editor_service import _editor_write_lock
    monkeypatch.setenv("SOUNDCLOUD_DL_APP_DATA_DIR", str(tmp_path))
    monkeypatch.setattr("app.editor_service.rekordbox_running", lambda: False)
    connection = _connect()
    connection.execute("INSERT INTO edit_transactions (id, draft_id, idempotency_key, state, created_at, updated_at) VALUES ('active', 'draft', 'once', 'prepared', 'now', 'now')")
    connection.commit()
    with _editor_write_lock() as acquired:
        assert acquired
        other = _connect()
        assert other.execute("SELECT state FROM edit_transactions").fetchone()[0] == "prepared"
        other.close()
    recovered = _connect()
    assert recovered.execute("SELECT state FROM edit_transactions").fetchone()[0] == "rolled_back"
    recovered.close()
    connection.close()
