from __future__ import annotations

import json
from pathlib import Path

import pytest

from app import sync_state


@pytest.fixture(autouse=True)
def isolated_sync_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(sync_state, "SYNC_DIR", tmp_path / "sync")
    monkeypatch.setattr(sync_state, "MUSIC_ROOT", tmp_path / "music")
    yield


def test_state_key_is_url_normalized():
    a = sync_state.state_key("https://soundcloud.com/me/sets/test")
    b = sync_state.state_key("https://soundcloud.com/me/sets/test/")
    assert a == b


def test_state_round_trip():
    url = "https://soundcloud.com/me/sets/test"
    state = sync_state.load_state(url)
    assert state.url == url
    assert state.title == ""

    state.title = "test"
    state.target_dir = "/tmp/foo"
    state.track_ids = ["1", "2", "3"]
    state.protected_ids = ["4"]
    state.rekordbox_playlist = "test"
    sync_state.save_state(state)

    reloaded = sync_state.load_state(url)
    assert reloaded.title == "test"
    assert reloaded.track_ids == ["1", "2", "3"]
    assert reloaded.protected_ids == ["4"]
    assert reloaded.rekordbox_playlist == "test"


def test_state_tolerates_unknown_keys():
    url = "https://soundcloud.com/me/sets/test"
    path = sync_state.state_path_for(url)
    path.write_text(json.dumps({"url": url, "title": "t", "future_field": "ignored", "track_ids": ["1"]}))

    state = sync_state.load_state(url)
    assert state.title == "t"
    assert state.track_ids == ["1"]


def test_diff_tracks_added_removed_unchanged():
    diff = sync_state.diff_track_ids(["a", "b", "c"], ["b", "c", "d", "e"])
    assert diff.added_ids == ["d", "e"]
    assert diff.removed_ids == ["a"]
    assert diff.unchanged_ids == ["b", "c"]


def test_first_sync_returns_all_as_added():
    diff = sync_state.diff_track_ids([], ["a", "b"])
    assert diff.added_ids == ["a", "b"]
    assert diff.removed_ids == []
    assert diff.unchanged_ids == []


def test_derive_target_dir_uses_music_root_and_slug():
    target = sync_state.derive_target_dir("My Sweet Playlist! ☆")
    assert target.parent == sync_state.MUSIC_ROOT
    assert target.name == "my-sweet-playlist"


def test_safe_slug_handles_unicode_and_punctuation():
    assert sync_state.safe_slug("Set #2 / Hot Mix") == "set-2-hot-mix"
    assert sync_state.safe_slug("") == "soundcloud"
