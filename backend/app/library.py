"""Library browsing — Rekordbox master.db + local folders.

Exposes the user's actual Rekordbox playlists (read-only) plus a generic
folder-listing fallback for browsing audio files that aren't in Rekordbox.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger("mixer.library")

AUDIO_EXTS = {".mp3", ".wav", ".aiff", ".aif", ".flac", ".m4a", ".aac", ".ogg", ".opus"}


def list_folder(path: Path) -> dict[str, Any]:
    """Return immediate children of `path`: subdirectories and audio files."""
    if not path.exists() or not path.is_dir():
        return {"path": str(path), "exists": False, "entries": []}
    entries: list[dict] = []
    try:
        children = sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    except PermissionError:
        return {"path": str(path), "exists": True, "entries": [], "error": "permission denied"}
    for p in children:
        if p.name.startswith("."):
            continue
        if p.is_dir():
            entries.append({"type": "dir", "name": p.name, "path": str(p)})
        elif p.suffix.lower() in AUDIO_EXTS:
            try:
                size = p.stat().st_size
            except OSError:
                size = 0
            entries.append(
                {"type": "file", "name": p.name, "path": str(p), "size": size}
            )
    return {"path": str(path), "exists": True, "entries": entries}


def default_roots() -> list[dict]:
    """Default folders to show in the sidebar."""
    home = Path.home()
    candidates = [
        ("Music", home / "Music"),
        ("Downloads", home / "Downloads"),
        ("Desktop", home / "Desktop"),
        ("Documents", home / "Documents"),
        ("PioneerDJ", home / "Music" / "PioneerDJ"),
    ]
    return [
        {"name": name, "path": str(p)}
        for name, p in candidates
        if p.exists() and p.is_dir()
    ]


def _open_db():
    from pyrekordbox import Rekordbox6Database

    return Rekordbox6Database()


def rekordbox_available() -> bool:
    try:
        _open_db()
        return True
    except Exception as e:
        logger.info("rekordbox db unavailable: %s", e)
        return False


def rekordbox_playlists() -> list[dict]:
    """Hierarchical playlists. Folders contain `children`, leaves do not."""
    try:
        db = _open_db()
    except Exception as e:
        logger.warning("could not open rekordbox db: %s", e)
        return []

    all_pls = list(db.get_playlist())
    by_id = {p.ID: p for p in all_pls}
    children_map: dict[Any, list] = {}
    for p in all_pls:
        children_map.setdefault(p.ParentID, []).append(p)

    def serialize(p) -> dict:
        node = {
            "id": str(p.ID),
            "name": p.Name,
            "is_folder": bool(p.is_folder),
        }
        if p.is_folder:
            kids = sorted(children_map.get(p.ID, []), key=lambda c: (not c.is_folder, c.Name.lower()))
            node["children"] = [serialize(c) for c in kids]
        else:
            try:
                node["song_count"] = len(p.Songs)
            except Exception:
                node["song_count"] = 0
        return node

    # Root playlists have ParentID = "root" (or similar sentinel value).
    # Identify them as those whose parent is not in by_id.
    roots = sorted(
        [p for p in all_pls if p.ParentID not in by_id],
        key=lambda p: (not p.is_folder, p.Name.lower()),
    )
    return [serialize(p) for p in roots]


def rekordbox_playlist_songs(playlist_id: str) -> list[dict]:
    try:
        db = _open_db()
    except Exception as e:
        logger.warning("could not open rekordbox db: %s", e)
        return []

    try:
        pl = db.get_playlist(ID=playlist_id)
    except Exception as e:
        logger.warning("get_playlist(%s) failed: %s", playlist_id, e)
        return []
    if pl is None:
        return []

    out: list[dict] = []
    for s in pl.Songs:
        c = s.Content
        path = c.FolderPath or ""
        out.append(
            {
                "rekordbox_id": str(c.ID),
                "title": c.Title or Path(path).stem,
                "artist": c.ArtistName or "",
                "bpm": float(c.BPM) / 100.0 if c.BPM else None,  # stored as bpm*100
                "duration_sec": float(c.Length) if c.Length else None,
                "path": path,
                "exists": bool(path) and Path(path).exists(),
            }
        )
    return out
