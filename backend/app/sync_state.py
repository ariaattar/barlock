from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

SYNC_DIR = Path.home() / ".config" / "soundcloud-dl" / "sync"
MUSIC_ROOT = Path.home() / "Music" / "SoundCloud"


@dataclass
class SyncState:
    url: str
    title: str = ""
    target_dir: str = ""
    rekordbox_playlist: str = ""
    rekordbox_playlist_id: str = ""
    track_ids: list[str] = field(default_factory=list)
    last_synced_at: str = ""

    @property
    def target_path(self) -> Path:
        return Path(self.target_dir).expanduser() if self.target_dir else MUSIC_ROOT / safe_slug(self.title or "soundcloud")


def state_key(url: str) -> str:
    normalized = url.strip().rstrip("/")
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:16]


def state_path_for(url: str) -> Path:
    SYNC_DIR.mkdir(parents=True, exist_ok=True)
    return SYNC_DIR / f"{state_key(url)}.json"


def load_state(url: str) -> SyncState:
    path = state_path_for(url)
    if not path.exists():
        return SyncState(url=url)
    try:
        raw = json.loads(path.read_text())
    except json.JSONDecodeError:
        return SyncState(url=url)
    if not isinstance(raw, dict):
        return SyncState(url=url)
    # Tolerate missing keys via dataclass defaults.
    valid_keys = {"url", "title", "target_dir", "rekordbox_playlist", "rekordbox_playlist_id", "track_ids", "last_synced_at"}
    data = {k: v for k, v in raw.items() if k in valid_keys}
    data.setdefault("url", url)
    return SyncState(**data)


def save_state(state: SyncState) -> Path:
    path = state_path_for(state.url)
    path.write_text(json.dumps(asdict(state), indent=2) + "\n")
    return path


def derive_target_dir(title: str) -> Path:
    return MUSIC_ROOT / safe_slug(title or "soundcloud")


def safe_slug(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", (value or "").strip()).strip("-._").lower()
    return slug[:80] or "soundcloud"


@dataclass(frozen=True)
class SyncDiff:
    added_ids: list[str]
    removed_ids: list[str]
    unchanged_ids: list[str]


def diff_track_ids(previous: list[str], current: list[str]) -> SyncDiff:
    prev_set = set(previous)
    curr_set = set(current)
    added = [tid for tid in current if tid not in prev_set]
    removed = [tid for tid in previous if tid not in curr_set]
    unchanged = [tid for tid in current if tid in prev_set]
    return SyncDiff(added_ids=added, removed_ids=removed, unchanged_ids=unchanged)
