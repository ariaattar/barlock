from __future__ import annotations

import copy
import datetime as dt
import fcntl
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
from contextlib import contextmanager
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any
from uuid import uuid4

from pyrekordbox import Rekordbox6Database
from pyrekordbox.anlz import AnlzFile
from pyrekordbox.db6 import tables

from .audio_features import (
    CAMELot_MAJOR,
    CAMELot_MINOR,
    CueHint,
    TrackFeatures,
    analysis_cache_path,
    analyze_file,
    camelot_key,
    save_analysis_features,
)
from .rekordbox_sync import (
    REKORDBOX_DIR,
    _beat_loop_size,
    backup_rekordbox,
    rekordbox_running,
    rekordbox_track,
    rekordbox_waveform,
)
from .soundcloud_common import features_from_dict, features_to_dict

EDITOR_SCHEMA_VERSION = 1
CANONICAL_MARKERS = (
    ("intro", "A", "Intro", "hot", 0),
    ("phrase16", "B", "Phrase 16", "hot", 1),
    ("phrase32", "C", "Phrase 32", "hot", 2),
    ("intro_loop", "D", "Intro Loop", "loop", 3),
    ("exit_loop", "E", "Exit Loop", "loop", 4),
)
ROLE_BY_NAME = {name: role for role, _pad, name, _kind, _slot in CANONICAL_MARKERS}
ROLE_BY_SLOT = {slot: role for role, _pad, _name, _kind, slot in CANONICAL_MARKERS if slot is not None}
CAMELot_TO_KEY = {value: key for key, value in {**CAMELot_MAJOR, **CAMELot_MINOR}.items()}
VALID_CAMELOT = tuple(f"{number}{letter}" for number in range(1, 13) for letter in ("A", "B"))
EDITOR_WAVEFORM_MAX_POINTS = 180000


def app_data_dir() -> Path:
    override = os.environ.get("SOUNDCLOUD_DL_APP_DATA_DIR", "").strip()
    root = Path(override).expanduser() if override else Path.home() / "Library" / "Application Support" / "SoundCloud DL"
    root.mkdir(parents=True, exist_ok=True)
    return root


def editor_db_path() -> Path:
    return app_data_dir() / "app.db"


def _connect() -> sqlite3.Connection:
    connection = sqlite3.connect(editor_db_path())
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS track_drafts (
            id TEXT PRIMARY KEY,
            track_key TEXT NOT NULL UNIQUE,
            content_id TEXT NOT NULL DEFAULT '',
            path TEXT NOT NULL,
            audio_fingerprint TEXT NOT NULL,
            anlz_fingerprint TEXT NOT NULL DEFAULT '',
            cue_digest TEXT NOT NULL DEFAULT '',
            revision INTEGER NOT NULL,
            base_features_json TEXT NOT NULL,
            corrections_json TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS cue_provenance (
            content_id TEXT NOT NULL,
            cue_id TEXT NOT NULL,
            track_key TEXT NOT NULL,
            role TEXT NOT NULL,
            hotcue_slot INTEGER,
            draft_revision INTEGER NOT NULL,
            cue_digest TEXT NOT NULL DEFAULT '',
            adopted INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (content_id, role)
        );
        CREATE UNIQUE INDEX IF NOT EXISTS cue_provenance_cue_id
            ON cue_provenance(content_id, cue_id);
        CREATE TABLE IF NOT EXISTS edit_transactions (
            id TEXT PRIMARY KEY,
            draft_id TEXT NOT NULL,
            idempotency_key TEXT NOT NULL UNIQUE,
            state TEXT NOT NULL,
            backup_path TEXT NOT NULL DEFAULT '',
            journal_json TEXT NOT NULL DEFAULT '[]',
            error TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        """
    )
    columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(edit_transactions)")}
    if "journal_json" not in columns:
        connection.execute("ALTER TABLE edit_transactions ADD COLUMN journal_json TEXT NOT NULL DEFAULT '[]'")
        connection.commit()
    _recover_incomplete_transactions(connection)
    return connection


@contextmanager
def _editor_write_lock():
    """Serialize writes/recovery across the short-lived desktop worker processes."""
    with (app_data_dir() / "editor-write.lock").open("a") as handle:
        acquired = False
        try:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            except BlockingIOError:
                pass
            yield acquired
        finally:
            if acquired:
                fcntl.flock(handle, fcntl.LOCK_UN)


def _recover_incomplete_transactions(connection: sqlite3.Connection) -> None:
    with _editor_write_lock() as acquired:
        if acquired:
            _recover_abandoned_transactions(connection)


def _recover_abandoned_transactions(connection: sqlite3.Connection) -> None:
    rows = connection.execute(
        "SELECT * FROM edit_transactions WHERE state IN ('prepared', 'files_written', 'db_committed')"
    ).fetchall()
    if not rows or rekordbox_running():
        return
    for row in rows:
        if row["state"] == "prepared":
            connection.execute(
                "UPDATE edit_transactions SET state='rolled_back', error='Recovered an interrupted prepared transaction', updated_at=? WHERE id=?",
                (_now(), row["id"]),
            )
            continue
        restored = _restore_journal_entries(row["journal_json"])
        connection.execute(
            "UPDATE edit_transactions SET state='rolled_back', error=?, updated_at=? WHERE id=?",
            (f"Recovered interrupted transaction; restored {restored} file(s)", _now(), row["id"]),
        )
    connection.commit()


def _restore_journal_entries(raw: str) -> int:
    restored = 0
    for entry in json.loads(raw or "[]"):
        source_value = str(entry.get("backup") or "")
        source = Path(source_value) if source_value else None
        target_value = str(entry.get("target") or "")
        if not target_value:
            continue
        target = Path(target_value)
        if entry.get("remove"):
            target.unlink(missing_ok=True)
            restored += 1
        elif source is not None and source.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            restored += 1
    return restored


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _audio_fingerprint(path: Path) -> str:
    path = path.expanduser().resolve()
    stat = path.stat()
    digest = hashlib.sha256()
    digest.update(str(path).encode())
    digest.update(str(stat.st_size).encode())
    sample_size = 128 * 1024
    with path.open("rb") as handle:
        digest.update(handle.read(sample_size))
        if stat.st_size > sample_size * 2:
            handle.seek(max(0, stat.st_size // 2 - sample_size // 2))
            digest.update(handle.read(sample_size))
            handle.seek(max(0, stat.st_size - sample_size))
            digest.update(handle.read(sample_size))
    return digest.hexdigest()


def _cue_digest(cues: list[dict[str, Any]]) -> str:
    stable = [
        {
            "id": str(cue.get("id", "")),
            "kind": int(cue.get("kind", 0) or 0),
            "name": str(cue.get("name", "")),
            "in_sec": round(float(cue.get("in_sec", 0) or 0), 3),
            "out_sec": None if cue.get("out_sec") is None else round(float(cue["out_sec"]), 3),
        }
        for cue in cues
    ]
    return hashlib.sha256(json.dumps(stable, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _local_waveform(path: Path, features: TrackFeatures, *, max_points: int = 4000) -> dict[str, Any]:
    import librosa
    import numpy as np

    fingerprint = _audio_fingerprint(path)
    cache_dir = app_data_dir() / "cache" / "waveforms"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / f"{fingerprint[:24]}-{max_points}.json"
    heights: list[float]
    try:
        heights = [float(value) for value in json.loads(cache_file.read_text())["heights"]]
    except (OSError, ValueError, KeyError, TypeError):
        y, _sr = librosa.load(str(path), sr=11025, mono=True)
        if y.size == 0:
            heights = [0.0]
        else:
            points = min(max_points, max(200, int(features.duration_sec * 12)))
            boundaries = np.linspace(0, y.size, points + 1, dtype=int)
            heights = [float(np.max(np.abs(y[boundaries[i]:boundaries[i + 1]]))) if boundaries[i + 1] > boundaries[i] else 0.0 for i in range(points)]
            peak = max(heights, default=1.0) or 1.0
            heights = [round(value / peak, 4) for value in heights]
        temporary = cache_file.with_name(f".{cache_file.name}.{os.getpid()}.tmp")
        temporary.write_text(json.dumps({"heights": heights}, separators=(",", ":")))
        os.replace(temporary, cache_file)
    colors = [[58, 162, 224] for _ in heights]
    grid = build_constant_grid(features.duration_sec, features.bpm, features.first_downbeat_sec)
    lane = {"tag": "LOCAL", "heights": heights, "colors": colors}
    return {
        "source": "local_analysis",
        "content_id": "",
        "title": features.title,
        "duration_sec": features.duration_sec,
        "fingerprint": fingerprint,
        "files": [],
        "beat_grid": grid,
        "preview": lane,
        "detail": lane,
        "cues": [],
    }


def build_constant_grid(duration_sec: float, bpm: float, first_downbeat_sec: float) -> list[dict[str, Any]]:
    if bpm <= 0 or duration_sec <= 0:
        return []
    beat = 60.0 / bpm
    first = max(0.0, min(float(first_downbeat_sec), duration_sec))
    preceding = int(first // beat)
    start = first - preceding * beat
    start_number = ((-preceding) % 4) + 1
    grid: list[dict[str, Any]] = []
    index = 0
    value = start
    while value <= duration_sec + 0.001:
        grid.append({
            "beat_number": ((start_number - 1 + index) % 4) + 1,
            "bpm": round(bpm, 2),
            "time_sec": round(max(0.0, value), 3),
        })
        index += 1
        value = start + index * beat
    return grid


def _snap_time(value: float, grid: list[dict[str, Any]], *, downbeat: bool) -> float:
    candidates = [beat for beat in grid if not downbeat or int(beat["beat_number"]) == 1]
    if not candidates:
        return max(0.0, value)
    return float(min(candidates, key=lambda beat: abs(float(beat["time_sec"]) - value))["time_sec"])


def _default_markers(features: TrackFeatures) -> list[dict[str, Any]]:
    by_role: dict[str, CueHint] = {}
    for hint in features.cue_hints:
        role = ROLE_BY_NAME.get(hint.name)
        if role is None and hint.hotcue_slot is not None:
            role = ROLE_BY_SLOT.get(int(hint.hotcue_slot))
        if role and role not in by_role:
            by_role[role] = hint

    beat = 60.0 / max(features.bpm, 1.0)
    bar = beat * 4
    fallback = {
        "intro": features.first_downbeat_sec,
        "phrase16": features.first_downbeat_sec + 16 * bar,
        "phrase32": features.first_downbeat_sec + 32 * bar,
        "intro_loop": features.first_downbeat_sec + 4 * bar,
        "exit_loop": max(features.first_downbeat_sec, features.duration_sec - 16 * bar),
    }
    markers: list[dict[str, Any]] = []
    for role, pad, name, kind, slot in CANONICAL_MARKERS:
        hint = by_role.get(role)
        seconds = float(hint.seconds) if hint else float(fallback[role])
        loop_beats = int(hint.loop_beats or 8) if kind == "loop" and hint else (8 if kind == "loop" else None)
        markers.append({
            "role": role,
            "pad": pad,
            "name": name,
            "kind": kind,
            "hotcue_slot": slot,
            "seconds": round(max(0.0, min(seconds, features.duration_sec)), 3),
            "end_seconds": round(float(hint.end_seconds), 3) if hint and hint.end_seconds is not None else None,
            "loop_beats": loop_beats,
            "snap_mode": "downbeat",
            "ownership": "suggested",
            "source_cue_id": "",
            "conflict": False,
        })
    return markers


def _normalise_corrections(raw: dict[str, Any], features: TrackFeatures) -> dict[str, Any]:
    bpm = round(float(raw.get("bpm", features.bpm)), 2)
    if bpm < 60 or bpm > 200:
        raise ValueError("BPM must be between 60 and 200")
    first = round(float(raw.get("first_downbeat_sec", features.first_downbeat_sec)), 3)
    if first < 0 or first > features.duration_sec:
        raise ValueError("First downbeat must be inside the track")
    camelot = str(raw.get("camelot_key", features.camelot_key) or "").upper()
    if camelot not in VALID_CAMELOT:
        raise ValueError("Key must be an alphanumeric Camelot key from 1A to 12B")
    write_grid = bool(raw.get("write_grid", True))
    if write_grid:
        grid = build_constant_grid(features.duration_sec, bpm, first)
    else:
        grid = [
            {
                "beat_number": int(beat["beat_number"]),
                "bpm": round(float(beat["bpm"]), 2),
                "time_sec": round(float(beat["time_sec"]), 3),
            }
            for beat in raw.get("grid", [])
            if 0 <= float(beat.get("time_sec", -1)) <= features.duration_sec
        ]
        if not grid:
            grid = build_constant_grid(features.duration_sec, bpm, first)
    incoming = {str(marker.get("role")): dict(marker) for marker in raw.get("markers", []) if isinstance(marker, dict)}
    markers: list[dict[str, Any]] = []
    defaults = {marker["role"]: marker for marker in _default_markers(features)}
    for role, pad, name, kind, slot in CANONICAL_MARKERS:
        marker = {**defaults[role], **incoming.get(role, {})}
        snap_mode = "beat" if marker.get("snap_mode") == "beat" else "downbeat"
        seconds = max(0.0, min(float(marker.get("seconds", 0)), features.duration_sec))
        seconds = _snap_time(seconds, grid, downbeat=snap_mode == "downbeat")
        loop_beats = None
        end_seconds = None
        if kind == "loop":
            requested = int(marker.get("loop_beats") or 8)
            loop_beats = 4 if requested == 4 else 8
            start_index = min(range(len(grid)), key=lambda index: abs(float(grid[index]["time_sec"]) - seconds))
            end_index = start_index + loop_beats
            if end_index >= len(grid):
                start_index = max(0, len(grid) - loop_beats - 1)
            seconds = float(grid[start_index]["time_sec"])
            end_seconds = float(grid[start_index + loop_beats]["time_sec"])
        markers.append({
            "role": role,
            "pad": pad,
            "name": name,
            "kind": kind,
            "hotcue_slot": slot,
            "seconds": round(seconds, 3),
            "end_seconds": None if end_seconds is None else round(end_seconds, 3),
            "loop_beats": loop_beats,
            "snap_mode": snap_mode,
            "ownership": str(marker.get("ownership") or "suggested"),
            "source_cue_id": str(marker.get("source_cue_id") or ""),
            "conflict": bool(marker.get("conflict", False)),
        })
    return {
        "bpm": bpm,
        "first_downbeat_sec": first,
        "camelot_key": camelot,
        "musical_key": CAMELot_TO_KEY[camelot],
        "write_grid": write_grid,
        "markers": markers,
        "grid": grid,
    }


def _provenance(connection: sqlite3.Connection, content_id: str) -> dict[str, sqlite3.Row]:
    if not content_id:
        return {}
    return {row["role"]: row for row in connection.execute("SELECT * FROM cue_provenance WHERE content_id = ?", (content_id,))}


def _decorate_cues_and_markers(
    corrections: dict[str, Any], cues: list[dict[str, Any]], provenance: dict[str, sqlite3.Row], managed: bool
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    cues_by_id = {str(cue["id"]): cue for cue in cues}
    owned_ids = {str(row["cue_id"]): role for role, row in provenance.items()}
    existing: list[dict[str, Any]] = []
    for cue in cues:
        cue_id = str(cue["id"])
        role = owned_ids.get(cue_id, ROLE_BY_NAME.get(str(cue.get("name") or ""), ""))
        ownership = "app" if cue_id in owned_ids else "legacy_candidate" if managed and role else "manual"
        existing.append({**cue, "role": role, "ownership": ownership, "locked": ownership != "app"})

    occupied_kinds = {int(cue.get("kind", 0) or 0): str(cue["id"]) for cue in cues if int(cue.get("kind", 0) or 0) > 0}
    markers: list[dict[str, Any]] = []
    for marker in corrections["markers"]:
        role = marker["role"]
        owned = provenance.get(role)
        source_cue = cues_by_id.get(str(owned["cue_id"])) if owned else None
        result = dict(marker)
        if source_cue is not None:
            if result.get("ownership") != "app" or result.get("source_cue_id") != str(source_cue["id"]):
                result.update({
                    "seconds": float(source_cue["in_sec"]),
                    "end_seconds": source_cue.get("out_sec"),
                    "loop_beats": int(source_cue.get("loop_beats") or result.get("loop_beats") or 0) or None,
                })
            result.update({"ownership": "app", "source_cue_id": str(source_cue["id"])})
        kind = _marker_kind(result)
        occupant = occupied_kinds.get(kind)
        if kind == 0:
            occupant = next((
                str(cue["id"]) for cue in cues
                if int(cue.get("kind", 0) or 0) == 0
                and abs(float(cue.get("in_sec", 0)) - float(result["seconds"])) <= 0.5
            ), None)
        result["conflict"] = bool(occupant and occupant != result.get("source_cue_id"))
        markers.append(result)
    corrections = {**corrections, "markers": markers}
    return existing, corrections


def _marker_kind(marker: dict[str, Any]) -> int:
    slot = marker.get("hotcue_slot")
    if slot is None:
        return 0
    return (1, 2, 3, 5, 6, 7, 8, 9)[int(slot)]


def _grid_editable(features: TrackFeatures, waveform: dict[str, Any] | None) -> bool:
    if not features.tempo_stable:
        return False
    bpms = [float(beat.get("bpm", 0)) for beat in (waveform or {}).get("beat_grid", []) if float(beat.get("bpm", 0)) > 0]
    return not bpms or max(bpms) - min(bpms) <= 0.1


def _authoritative_track_features(
    features: TrackFeatures,
    track_info: Any,
    waveform: dict[str, Any],
) -> TrackFeatures:
    if not waveform.get("beat_grid"):
        return features
    bpms = [float(beat["bpm"]) for beat in waveform["beat_grid"] if float(beat.get("bpm", 0)) > 0]
    bpm = sorted(bpms)[len(bpms) // 2] if bpms else features.bpm
    first = next(
        (float(beat["time_sec"]) for beat in waveform["beat_grid"] if int(beat["beat_number"]) == 1),
        features.first_downbeat_sec,
    )
    musical_key = track_info.musical_key if track_info and track_info.musical_key else features.musical_key
    return replace(
        features,
        bpm=bpm,
        first_downbeat_sec=first,
        musical_key=musical_key,
        camelot_key=camelot_key(musical_key) or features.camelot_key,
    )


def load_editor(*, content_id: str = "", path: str = "") -> dict[str, Any]:
    content_id = str(content_id or "").strip()
    track_info = None
    waveform: dict[str, Any] | None = None
    managed = False
    if content_id:
        track_info = rekordbox_track(content_id)
        path = track_info.folder_path
        managed = track_info.soundcloud_dl_managed
        waveform = asdict(rekordbox_waveform(content_id, max_points=EDITOR_WAVEFORM_MAX_POINTS))
    audio_path = Path(path).expanduser().resolve() if path else None
    if audio_path is None or not audio_path.exists():
        raise ValueError("The local audio file is required to edit this track")
    features = analyze_file(audio_path, output_dir=audio_path.parent)
    if waveform is None:
        waveform = _local_waveform(audio_path, features)
    else:
        features = _authoritative_track_features(features, track_info, waveform)
    track_key = f"rekordbox:{content_id}" if content_id else f"path:{audio_path}"
    audio_fingerprint = _audio_fingerprint(audio_path)
    anlz_fingerprint = str(waveform.get("fingerprint", "")) if content_id else ""
    raw_cues = [dict(cue) for cue in waveform.get("cues", [])]
    cue_digest = _cue_digest(raw_cues)

    connection = _connect()
    try:
        row = connection.execute("SELECT * FROM track_drafts WHERE track_key = ?", (track_key,)).fetchone()
        editable_grid = _grid_editable(features, waveform)
        if row is None:
            corrections = _normalise_corrections({
                "bpm": features.bpm,
                "first_downbeat_sec": features.first_downbeat_sec,
                "camelot_key": features.camelot_key,
                "markers": _default_markers(features),
                "write_grid": False,
                "grid": waveform.get("beat_grid", []),
            }, features)
            now = _now()
            draft_id = str(uuid4())
            connection.execute(
                "INSERT INTO track_drafts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    draft_id, track_key, content_id, str(audio_path), audio_fingerprint,
                    anlz_fingerprint, cue_digest, 1, json.dumps(features_to_dict(features)),
                    json.dumps(corrections), "draft", now, now,
                ),
            )
            connection.commit()
            row = connection.execute("SELECT * FROM track_drafts WHERE id = ?", (draft_id,)).fetchone()
        stored_corrections = json.loads(row["corrections_json"])
        if not editable_grid:
            stored_corrections.update({
                "bpm": features.bpm,
                "first_downbeat_sec": features.first_downbeat_sec,
                "write_grid": False,
                "grid": waveform.get("beat_grid", []),
            })
        corrections = _normalise_corrections(stored_corrections, features)
        existing, corrections = _decorate_cues_and_markers(corrections, raw_cues, _provenance(connection, content_id), managed)
        stale = row["audio_fingerprint"] != audio_fingerprint or row["anlz_fingerprint"] != anlz_fingerprint or row["cue_digest"] != cue_digest
        if not stale:
            connection.execute(
                "UPDATE track_drafts SET base_features_json=?, corrections_json=? WHERE id=?",
                (json.dumps(features_to_dict(features)), json.dumps(corrections), row["id"]),
            )
            connection.commit()
        return {
            "schema_version": EDITOR_SCHEMA_VERSION,
            "track": {
                "content_id": content_id,
                "path": str(audio_path),
                "title": track_info.title if track_info else features.title,
                "artist": track_info.artist if track_info else features.artist,
                "duration_sec": features.duration_sec,
                "sample_rate": features.sample_rate,
                "managed": managed,
                "audio_fingerprint": audio_fingerprint,
            },
            "waveform": waveform,
            "existing_cues": existing,
            "draft": {
                "id": row["id"],
                "revision": int(row["revision"]),
                "status": "stale" if stale else row["status"],
                "updated_at": row["updated_at"],
                "corrections": corrections,
            },
            "grid_editable": editable_grid,
            "can_apply_rekordbox": bool(content_id and waveform.get("beat_grid")),
            "warnings": ["Variable-tempo grid detected. BPM and offset are read-only."] if not editable_grid else [],
        }
    finally:
        connection.close()


def save_draft(payload: dict[str, Any]) -> dict[str, Any]:
    draft_id = str(payload.get("draft_id") or "")
    expected = int(payload.get("revision") or 0)
    connection = _connect()
    try:
        row = connection.execute("SELECT * FROM track_drafts WHERE id = ?", (draft_id,)).fetchone()
        if row is None:
            raise ValueError("draft not found")
        if int(row["revision"]) != expected:
            raise RuntimeError("This draft changed in another editor. Reload it before continuing.")
        features = features_from_dict(json.loads(row["base_features_json"]))
        corrections = _normalise_corrections(dict(payload.get("corrections") or {}), features)
        revision = expected + 1
        now = _now()
        connection.execute(
            "UPDATE track_drafts SET corrections_json = ?, revision = ?, status = 'draft', updated_at = ? WHERE id = ?",
            (json.dumps(corrections), revision, now, draft_id),
        )
        connection.commit()
        return {"id": draft_id, "revision": revision, "status": "draft", "updated_at": now, "corrections": corrections}
    finally:
        connection.close()


def reset_draft(payload: dict[str, Any]) -> dict[str, Any]:
    draft_id = str(payload.get("draft_id") or "")
    expected = int(payload.get("revision") or 0)
    connection = _connect()
    try:
        row = connection.execute("SELECT * FROM track_drafts WHERE id = ?", (draft_id,)).fetchone()
        if row is None:
            raise ValueError("draft not found")
        if int(row["revision"]) != expected:
            raise RuntimeError("This draft changed in another editor. Reload it before resetting.")
        features = features_from_dict(json.loads(row["base_features_json"]))
        previous = json.loads(row["corrections_json"])
        corrections = _normalise_corrections({
            "bpm": features.bpm,
            "first_downbeat_sec": features.first_downbeat_sec,
            "camelot_key": features.camelot_key,
            "markers": _default_markers(features),
            "write_grid": bool(previous.get("write_grid", True)),
            "grid": previous.get("grid", []),
        }, features)
        revision = expected + 1
        now = _now()
        connection.execute(
            "UPDATE track_drafts SET corrections_json=?, revision=?, status='draft', updated_at=? WHERE id=?",
            (json.dumps(corrections), revision, now, draft_id),
        )
        connection.commit()
        return {"id": draft_id, "revision": revision, "status": "draft", "updated_at": now, "corrections": corrections}
    finally:
        connection.close()


def rebase_draft(payload: dict[str, Any]) -> dict[str, Any]:
    draft_id = str(payload.get("draft_id") or "")
    expected = int(payload.get("revision") or 0)
    connection = _connect()
    content_id = ""
    audio_path: Path | None = None
    try:
        row = connection.execute("SELECT * FROM track_drafts WHERE id = ?", (draft_id,)).fetchone()
        if row is None:
            raise ValueError("draft not found")
        if int(row["revision"]) != expected:
            raise RuntimeError("This draft changed in another editor. Reload it before rebasing.")
        content_id = str(row["content_id"] or "")
        track_info = rekordbox_track(content_id) if content_id else None
        audio_path = Path(track_info.folder_path if track_info else row["path"]).expanduser().resolve()
        if not audio_path.exists():
            raise ValueError("The local audio file is required to rebase this draft")
        features = analyze_file(audio_path, output_dir=audio_path.parent)
        waveform = asdict(rekordbox_waveform(content_id, max_points=EDITOR_WAVEFORM_MAX_POINTS)) if content_id else _local_waveform(audio_path, features)
        if content_id:
            features = _authoritative_track_features(features, track_info, waveform)
        editable_grid = _grid_editable(features, waveform)
        previous = json.loads(row["corrections_json"])
        previous_writable = bool(previous.get("write_grid", True))
        previous.update({
            "write_grid": editable_grid and previous_writable,
            "grid": waveform.get("beat_grid", []),
        })
        if not editable_grid or not previous_writable:
            previous.update({"bpm": features.bpm, "first_downbeat_sec": features.first_downbeat_sec})
        corrections = _normalise_corrections(previous, features)
        raw_cues = [dict(cue) for cue in waveform.get("cues", [])]
        revision = expected + 1
        now = _now()
        connection.execute(
            """UPDATE track_drafts SET path=?, audio_fingerprint=?, anlz_fingerprint=?, cue_digest=?,
               revision=?, base_features_json=?, corrections_json=?, status='draft', updated_at=? WHERE id=?""",
            (
                str(audio_path), _audio_fingerprint(audio_path), str(waveform.get("fingerprint", "")) if content_id else "",
                _cue_digest(raw_cues), revision, json.dumps(features_to_dict(features)), json.dumps(corrections), now, draft_id,
            ),
        )
        connection.commit()
    finally:
        connection.close()
    return load_editor(content_id=content_id, path=str(audio_path or ""))


def prepare_audition(payload: dict[str, Any]) -> dict[str, Any]:
    draft_id = str(payload.get("draft_id") or "")
    role = str(payload.get("role") or "")
    connection = _connect()
    try:
        row = connection.execute("SELECT * FROM track_drafts WHERE id = ?", (draft_id,)).fetchone()
        if row is None:
            raise ValueError("draft not found")
        corrections = json.loads(row["corrections_json"])
        marker = next((item for item in corrections.get("markers", []) if item.get("role") == role), None)
        if marker is None or marker.get("end_seconds") is None:
            raise ValueError("Select an intro or exit loop to audition")
        loop_start = float(marker["seconds"])
        loop_end = float(marker["end_seconds"])
        loop_beats = max(1, int(marker.get("loop_beats") or 1))
        beat = (loop_end - loop_start) / loop_beats
        context_start = max(0.0, loop_start - 2 * beat)
        duration = loop_end - context_start + 2 * beat
        audio_path = Path(row["path"])
        key = hashlib.sha256(f"{row['audio_fingerprint']}:{context_start:.3f}:{duration:.3f}".encode()).hexdigest()[:24]
        cache = app_data_dir() / "cache" / "audition"
        cache.mkdir(parents=True, exist_ok=True)
        output = cache / f"{key}.wav"
        if not output.exists():
            from imageio_ffmpeg import get_ffmpeg_exe
            partial = output.with_suffix(".partial.wav")
            command = [
                get_ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y",
                "-ss", f"{context_start:.6f}", "-i", str(audio_path),
                "-t", f"{duration:.6f}", "-vn", "-ac", "2", "-ar", "44100",
                "-c:a", "pcm_s16le", str(partial),
            ]
            result = subprocess.run(command, check=False, capture_output=True, text=True, timeout=45)
            if result.returncode != 0:
                partial.unlink(missing_ok=True)
                raise RuntimeError(result.stderr.strip() or "Could not prepare the loop audition")
            os.replace(partial, output)
        return {
            "path": str(output),
            "context_start_sec": round(context_start, 6),
            "loop_start_sec": round(loop_start - context_start, 6),
            "loop_end_sec": round(loop_end - context_start, 6),
            "duration_sec": round(duration, 6),
        }
    finally:
        connection.close()


def adopt_cues(payload: dict[str, Any]) -> dict[str, Any]:
    draft_id = str(payload.get("draft_id") or "")
    cue_ids = {str(value) for value in payload.get("cue_ids", [])}
    connection = _connect()
    try:
        row = connection.execute("SELECT * FROM track_drafts WHERE id = ?", (draft_id,)).fetchone()
        if row is None or not row["content_id"]:
            raise ValueError("A Rekordbox draft is required")
        if not rekordbox_track(row["content_id"]).soundcloud_dl_managed:
            raise RuntimeError("Only legacy cues on SoundCloud DL managed tracks can be adopted")
        waveform = asdict(rekordbox_waveform(row["content_id"], max_points=100))
        cues = [cue for cue in waveform["cues"] if str(cue["id"]) in cue_ids]
        now = _now()
        adopted = 0
        for cue in cues:
            role = ROLE_BY_NAME.get(str(cue.get("name") or ""))
            if not role:
                continue
            marker_spec = next(item for item in CANONICAL_MARKERS if item[0] == role)
            connection.execute(
                """INSERT INTO cue_provenance
                   (content_id, cue_id, track_key, role, hotcue_slot, draft_revision, cue_digest, adopted, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)
                   ON CONFLICT(content_id, role) DO UPDATE SET cue_id=excluded.cue_id, adopted=1, updated_at=excluded.updated_at""",
                (row["content_id"], str(cue["id"]), row["track_key"], role, marker_spec[4], int(row["revision"]), row["cue_digest"], now),
            )
            adopted += 1
        connection.commit()
        return {"adopted": adopted}
    finally:
        connection.close()


def preview_apply(payload: dict[str, Any]) -> dict[str, Any]:
    row, corrections = _load_draft_for_apply(payload)
    conflicts = [marker["name"] for marker in corrections["markers"] if marker.get("conflict")]
    return {
        "draft_id": row["id"],
        "revision": int(row["revision"]),
        "rekordbox": bool(row["content_id"]),
        "rekordbox_running": rekordbox_running() if row["content_id"] else False,
        "conflicts": conflicts,
        "changes": [
            f"BPM {corrections['bpm']:.2f} and grid offset {corrections['first_downbeat_sec']:.3f}s",
            f"Key {corrections['camelot_key']} ({corrections['musical_key']})",
            f"{len(corrections['markers']) - len(conflicts)} eligible cue and loop markers",
            (
                "Rekordbox PQTZ beat grid"
                if row["content_id"] and corrections.get("write_grid")
                else "Existing variable-tempo PQTZ grid preserved"
                if row["content_id"]
                else "Local analysis cache and BPM/key tags"
            ),
        ],
    }


def _load_draft_for_apply(payload: dict[str, Any]) -> tuple[sqlite3.Row, dict[str, Any]]:
    draft_id = str(payload.get("draft_id") or "")
    revision = int(payload.get("revision") or 0)
    connection = _connect()
    try:
        row = connection.execute("SELECT * FROM track_drafts WHERE id = ?", (draft_id,)).fetchone()
        if row is None:
            raise ValueError("draft not found")
        if int(row["revision"]) != revision:
            raise RuntimeError("The apply preview is stale. Review the latest draft first.")
        return row, json.loads(row["corrections_json"])
    finally:
        connection.close()


def apply_draft(payload: dict[str, Any]) -> dict[str, Any]:
    with _editor_write_lock() as acquired:
        if not acquired:
            raise RuntimeError("Another correction is being applied. Wait for it to finish and try again.")
        return _apply_draft_locked(payload)


def _apply_draft_locked(payload: dict[str, Any]) -> dict[str, Any]:
    row, corrections = _load_draft_for_apply(payload)
    idempotency_key = str(payload.get("idempotency_key") or uuid4())
    connection = _connect()
    _recover_abandoned_transactions(connection)
    transaction_id = str(uuid4())
    now = _now()
    try:
        prior = connection.execute("SELECT * FROM edit_transactions WHERE idempotency_key = ?", (idempotency_key,)).fetchone()
        if prior and prior["state"] == "verified":
            return {"transaction_id": prior["id"], "state": "verified", "backup_dir": prior["backup_path"], "idempotent": True}
        connection.execute(
            """INSERT INTO edit_transactions
               (id, draft_id, idempotency_key, state, backup_path, journal_json, error, created_at, updated_at)
               VALUES (?, ?, ?, 'prepared', '', '[]', '', ?, ?)""",
            (transaction_id, row["id"], idempotency_key, now, now),
        )
        connection.commit()

        audio_path = Path(row["path"])
        if _audio_fingerprint(audio_path) != row["audio_fingerprint"]:
            raise RuntimeError("The audio file changed since this draft was opened. Reload before applying.")
        features = features_from_dict(json.loads(row["base_features_json"]))
        hints = [
            CueHint(
                name=marker["name"], seconds=float(marker["seconds"]), kind=marker["kind"],
                hotcue_slot=marker.get("hotcue_slot"), end_seconds=marker.get("end_seconds"),
                loop_beats=marker.get("loop_beats"),
            )
            for marker in corrections["markers"]
        ]
        effective = replace(
            features,
            bpm=float(corrections["bpm"]),
            first_downbeat_sec=float(corrections["first_downbeat_sec"]),
            musical_key=str(corrections["musical_key"]),
            camelot_key=str(corrections["camelot_key"]),
            cue_hints=hints,
        )

        if row["content_id"]:
            result = _apply_rekordbox(row, corrections, effective, connection, transaction_id)
        else:
            backup = app_data_dir() / "backups" / f"local-{dt.datetime.now().strftime('%Y%m%d-%H%M%S')}"
            backup.mkdir(parents=True, exist_ok=True)
            audio_backup = backup / audio_path.name
            shutil.copy2(audio_path, audio_backup)
            connection.execute(
                "UPDATE edit_transactions SET state='files_written', backup_path=?, journal_json=?, updated_at=? WHERE id=?",
                (str(backup), json.dumps([{"backup": str(audio_backup), "target": str(audio_path)}]), _now(), transaction_id),
            )
            connection.commit()
            _write_editor_tags(audio_path, effective)
            result = {"backup_dir": str(backup), "written_cues": 0}

        cache_target = analysis_cache_path(effective, output_dir=audio_path.parent)
        backup_root = Path(result["backup_dir"])
        cache_backup = backup_root / f"analysis-cache-{cache_target.name}"
        pending = connection.execute("SELECT journal_json FROM edit_transactions WHERE id = ?", (transaction_id,)).fetchone()
        journal = json.loads(pending["journal_json"] or "[]") if pending else []
        if cache_target.exists():
            shutil.copy2(cache_target, cache_backup)
            journal.append({"backup": str(cache_backup), "target": str(cache_target)})
        else:
            journal.append({"backup": "", "target": str(cache_target), "remove": True})
        connection.execute(
            "UPDATE edit_transactions SET journal_json=?, updated_at=? WHERE id=?",
            (json.dumps(journal), _now(), transaction_id),
        )
        connection.commit()
        cache_file = save_analysis_features(effective, output_dir=audio_path.parent)
        new_audio_fingerprint = _audio_fingerprint(audio_path)
        new_anlz = row["anlz_fingerprint"]
        new_cue_digest = row["cue_digest"]
        if row["content_id"]:
            refreshed = asdict(rekordbox_waveform(row["content_id"], max_points=100))
            new_anlz = refreshed["fingerprint"]
            new_cue_digest = _cue_digest(refreshed["cues"])
            _record_cue_provenance(
                connection,
                row,
                corrections["markers"],
                result.get("cue_ids", {}),
                cue_digest=new_cue_digest,
            )
        connection.execute(
            """UPDATE track_drafts SET audio_fingerprint=?, anlz_fingerprint=?, cue_digest=?,
               base_features_json=?, corrections_json=?, status='applied', updated_at=? WHERE id=?""",
            (new_audio_fingerprint, new_anlz, new_cue_digest, json.dumps(features_to_dict(effective)), json.dumps(corrections), _now(), row["id"]),
        )
        connection.execute(
            "UPDATE edit_transactions SET state='verified', backup_path=?, updated_at=? WHERE id=?",
            (result["backup_dir"], _now(), transaction_id),
        )
        connection.commit()
        return {
            "transaction_id": transaction_id,
            "state": "verified",
            "backup_dir": result["backup_dir"],
            "cache_file": str(cache_file),
            "written_cues": result["written_cues"],
        }
    except Exception as exc:
        connection.rollback()
        pending = connection.execute("SELECT journal_json FROM edit_transactions WHERE id = ?", (transaction_id,)).fetchone()
        if pending is not None:
            _restore_journal_entries(pending["journal_json"])
        connection.execute(
            "UPDATE edit_transactions SET state='rolled_back', error=?, updated_at=? WHERE id=?",
            (str(exc), _now(), transaction_id),
        )
        connection.commit()
        raise
    finally:
        connection.close()


def _apply_rekordbox(
    row: sqlite3.Row,
    corrections: dict[str, Any],
    effective: TrackFeatures,
    app_db: sqlite3.Connection,
    transaction_id: str,
) -> dict[str, Any]:
    if rekordbox_running():
        raise RuntimeError("Close Rekordbox before applying reviewed corrections.")
    current = asdict(rekordbox_waveform(row["content_id"], max_points=100))
    if current["fingerprint"] != row["anlz_fingerprint"] or _cue_digest(current["cues"]) != row["cue_digest"]:
        raise RuntimeError("Rekordbox analysis or cues changed since this draft was opened. Reload and review the merged state.")
    backup_dir = backup_rekordbox()
    audio_path = Path(row["path"])
    audio_backup = backup_dir / f"audio-{audio_path.name}"
    shutil.copy2(audio_path, audio_backup)
    db = Rekordbox6Database()
    staged_path: Path | None = None
    dat_path: Path | None = None
    dat_backup: Path | None = None
    committed = False
    try:
        content = db.get_content(ID=row["content_id"])
        if content is None:
            raise ValueError("Rekordbox track no longer exists")
        journal = [
            {"backup": str(audio_backup), "target": str(audio_path)},
        ]
        if corrections.get("write_grid"):
            files = db.read_anlz_files(content)
            dat_item = next(((path, anlz) for path, anlz in files.items() if path.suffix.upper() == ".DAT" and anlz.get_tag("PQTZ") is not None), None)
            if dat_item is None:
                raise RuntimeError("The track has no writable PQTZ beat grid")
            dat_path, anlz = dat_item
            dat_backup = backup_dir / f"anlz-{row['content_id']}-{dat_path.name}"
            shutil.copy2(dat_path, dat_backup)
            journal.append({"backup": str(dat_backup), "target": str(dat_path)})
        for name in ("master.db", "master.db-wal", "master.db-shm"):
            source = backup_dir / name
            if source.exists():
                journal.append({"backup": str(source), "target": str(REKORDBOX_DIR / name)})
            else:
                journal.append({"backup": "", "target": str(REKORDBOX_DIR / name), "remove": True})
        app_db.execute(
            "UPDATE edit_transactions SET state='files_written', backup_path=?, journal_json=?, updated_at=? WHERE id=?",
            (str(backup_dir), json.dumps(journal), _now(), transaction_id),
        )
        app_db.commit()
        if corrections.get("write_grid") and dat_path is not None:
            staged_path = dat_path.with_name(f".{dat_path.name}.{transaction_id}.tmp")
            _stage_pqtz(anlz, corrections["grid"], staged_path)
            _verify_pqtz(staged_path, corrections["grid"])

        provenance = _provenance(app_db, row["content_id"])
        role_to_cue = _write_editor_cues(db, content, corrections["markers"], provenance)
        content.BPM = int(round(effective.bpm * 100))
        key = db.get_key(ScaleName=effective.musical_key).first()
        if key is None:
            raise RuntimeError(f"Rekordbox key is unavailable: {effective.musical_key}")
        content.KeyID = key.ID
        _write_editor_tags(audio_path, effective)
        content.FileSize = audio_path.stat().st_size
        if staged_path is not None and dat_path is not None:
            os.replace(staged_path, dat_path)
            staged_path = None
        db.commit()
        committed = True
        app_db.execute(
            "UPDATE edit_transactions SET state='db_committed', updated_at=? WHERE id=?",
            (_now(), transaction_id),
        )
        app_db.commit()
    except Exception:
        db.session.rollback()
        if dat_backup and dat_path and dat_backup.exists():
            shutil.copy2(dat_backup, dat_path)
        audio_backup = backup_dir / f"audio-{audio_path.name}"
        if audio_backup.exists():
            shutil.copy2(audio_backup, audio_path)
        raise
    finally:
        if staged_path:
            staged_path.unlink(missing_ok=True)
        db.close()

    if not committed:
        raise RuntimeError("Rekordbox transaction did not commit")
    verified = asdict(rekordbox_waveform(row["content_id"], max_points=100))
    actual_grid = verified["beat_grid"]
    expected_grid = corrections["grid"]
    if corrections.get("write_grid") and (len(actual_grid) != len(expected_grid) or any(
        abs(float(actual["time_sec"]) - float(expected["time_sec"])) > 0.001
        or abs(float(actual["bpm"]) - float(expected["bpm"])) > 0.01
        or int(actual["beat_number"]) != int(expected["beat_number"])
        for actual, expected in zip(actual_grid, expected_grid)
    )):
        _restore_rekordbox_backup(backup_dir, dat_backup, dat_path, audio_path)
        raise RuntimeError("PQTZ verification failed; the Rekordbox database and analysis files were restored")

    return {
        "backup_dir": str(backup_dir),
        "written_cues": len(role_to_cue),
        "cue_ids": role_to_cue,
    }


def _record_cue_provenance(
    app_db: sqlite3.Connection,
    row: sqlite3.Row | dict[str, Any],
    markers: list[dict[str, Any]],
    cue_by_role: dict[str, str],
    *,
    cue_digest: str,
) -> None:
    now = _now()
    for marker in markers:
        cue_id = cue_by_role.get(marker["role"])
        if not cue_id:
            continue
        app_db.execute(
            """INSERT INTO cue_provenance
               (content_id, cue_id, track_key, role, hotcue_slot, draft_revision, cue_digest, adopted, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?)
               ON CONFLICT(content_id, role) DO UPDATE SET cue_id=excluded.cue_id,
               hotcue_slot=excluded.hotcue_slot, draft_revision=excluded.draft_revision,
               cue_digest=excluded.cue_digest, adopted=0, updated_at=excluded.updated_at""",
            (
                row["content_id"], cue_id, row["track_key"], marker["role"],
                marker.get("hotcue_slot"), int(row["revision"]), cue_digest, now,
            ),
        )


def _stage_pqtz(anlz: AnlzFile, grid: list[dict[str, Any]], destination: Path) -> None:
    tag = anlz.get_tag("PQTZ")
    if tag is None or not tag.content.entries:
        raise RuntimeError("PQTZ beat grid is empty")
    template = tag.content.entries[0]
    entries = []
    for beat in grid:
        entry = copy.deepcopy(template)
        entry.update({
            "beat": int(beat["beat_number"]),
            "tempo": int(round(float(beat["bpm"]) * 100)),
            "time": int(round(float(beat["time_sec"]) * 1000)),
        })
        entries.append(entry)
    tag.content.entries.clear()
    tag.content.entries.extend(entries)
    tag.content.entry_count = len(entries)
    anlz.save(destination)


def _write_editor_tags(path: Path, features: TrackFeatures) -> None:
    if path.suffix.lower() != ".mp3":
        return
    from mutagen.easyid3 import EasyID3
    from mutagen.id3 import ID3NoHeaderError

    try:
        EasyID3.RegisterTextKey("initialkey", "TKEY")
    except ValueError:
        pass
    try:
        tags = EasyID3(str(path))
    except ID3NoHeaderError:
        tags = EasyID3()
    tags["bpm"] = str(round(features.bpm, 2))
    tags["initialkey"] = features.camelot_key
    tags.save(str(path))


def _verify_pqtz(path: Path, expected: list[dict[str, Any]]) -> None:
    parsed = AnlzFile.parse_file(path)
    tag = parsed.get_tag("PQTZ")
    if tag is None or len(tag.content.entries) != len(expected):
        raise RuntimeError("Staged PQTZ entry count did not round-trip")
    for entry, beat in zip(tag.content.entries, expected):
        if int(entry.beat) != int(beat["beat_number"]):
            raise RuntimeError("Staged PQTZ beat number did not round-trip")
        if int(entry.tempo) != int(round(float(beat["bpm"]) * 100)):
            raise RuntimeError("Staged PQTZ tempo did not round-trip")
        if int(entry.time) != int(round(float(beat["time_sec"]) * 1000)):
            raise RuntimeError("Staged PQTZ timing did not round-trip")


def _write_editor_cues(db, content, markers: list[dict[str, Any]], provenance: dict[str, sqlite3.Row]) -> dict[str, str]:
    existing = list(db.get_cue(ContentID=content.ID).all())
    by_id = {str(cue.ID): cue for cue in existing}
    by_kind = {int(cue.Kind): cue for cue in existing if int(cue.Kind or 0) > 0}
    role_to_cue: dict[str, str] = {}
    for marker in markers:
        role = marker["role"]
        kind = _marker_kind(marker)
        owned = provenance.get(role)
        cue = by_id.get(str(owned["cue_id"])) if owned else None
        if kind > 0 and kind in by_kind and by_kind[kind] is not cue:
            continue
        if kind == 0 and cue is None and any(
            int(existing_cue.Kind or 0) == 0
            and abs(int(existing_cue.InMsec or 0) - int(round(float(marker["seconds"]) * 1000))) <= 500
            for existing_cue in existing
        ):
            continue
        if cue is None:
            cue = tables.DjmdCue.create(
                ID=str(db.generate_unused_id(tables.DjmdCue, is_28_bit=False)),
                ContentID=str(content.ID),
                ContentUUID=str(content.UUID),
            )
            db.add(cue)
        in_msec = max(0, int(round(float(marker["seconds"]) * 1000)))
        is_loop = marker["kind"] == "loop"
        cue.InMsec = in_msec
        cue.OutMsec = max(in_msec + 1, int(round(float(marker["end_seconds"]) * 1000))) if is_loop else -1
        cue.Kind = kind
        cue.CueMicrosec = 0 if is_loop else None
        cue.Color = 255 if is_loop else -1
        cue.ColorTableIndex = 0 if is_loop else None
        cue.ActiveLoop = 0 if is_loop else None
        cue.Comment = marker["name"]
        cue.BeatLoopSize = _beat_loop_size(CueHint(marker["name"], marker["seconds"], "loop", marker.get("hotcue_slot"), marker.get("end_seconds"), marker.get("loop_beats"))) if is_loop else None
        if kind > 0:
            by_kind[kind] = cue
        role_to_cue[role] = str(cue.ID)
    if role_to_cue:
        content.HotCueAutoLoad = "on"
        content.CueUpdated = str(int(content.CueUpdated or 0) + 1)
    return role_to_cue


def _restore_rekordbox_backup(backup_dir: Path, dat_backup: Path | None, dat_path: Path | None, audio_path: Path) -> None:
    if dat_backup and dat_path and dat_backup.exists():
        shutil.copy2(dat_backup, dat_path)
    audio_backup = backup_dir / f"audio-{audio_path.name}"
    if audio_backup.exists():
        shutil.copy2(audio_backup, audio_path)
    for name in ("master.db", "master.db-wal", "master.db-shm"):
        source = backup_dir / name
        target = REKORDBOX_DIR / name
        if source.exists():
            shutil.copy2(source, target)
