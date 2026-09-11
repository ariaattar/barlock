from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.audio_features import TrackFeatures
from app import soundcloud_bridge as bridge
from app import soundcloud_cli as cli
from app.soundcloud_common import features_to_dict
from app.soundcloud_config import AppConfig
from app.soundcloud_downloader import DownloadEntry, DownloadPlan, DownloadResult
from app.sync_state import SyncState


@pytest.mark.parametrize("source_present", [True, False])
def test_sync_keeps_missing_tracks_and_history(monkeypatch, tmp_path, source_present):
    url = "https://soundcloud.com/me/likes"
    missing = tmp_path / "Taken down [111111].mp3"
    current = tmp_path / "Still liked [222222].mp3"
    missing.write_bytes(b"saved missing audio")
    current.write_bytes(b"saved current audio")
    state = SyncState(url=url, title="Likes", target_dir=str(tmp_path), track_ids=["111111"], rekordbox_playlist_id="pl-1")
    entries = [DownloadEntry(url="https://soundcloud.com/u/still-liked", id="222222", title="Still liked")] if source_present else []
    plan = DownloadPlan(entries=entries, title="Likes")
    monkeypatch.setattr(bridge, "load_state", lambda _: state)
    monkeypatch.setattr(bridge, "save_state", lambda value: None)
    monkeypatch.setattr(bridge, "load_config", lambda: AppConfig(output_dir=str(tmp_path)))
    monkeypatch.setattr(bridge, "collect_download_plan", lambda _, **kwargs: plan)
    monkeypatch.setattr(bridge, "_load_payload", lambda _: {"url": url, "push": True, "write_tags": False})
    monkeypatch.setattr(bridge, "rekordbox_running", lambda: False)
    monkeypatch.setattr(bridge, "download_entries", lambda *a, **k: pytest.fail("Existing files must not be redownloaded"))
    monkeypatch.setattr(bridge, "_analyze_paths_parallel", lambda paths, **k: [_features(p) for p in paths])
    pushed = []
    # No remove_paths parameter: sync must never send automatic removals.
    def push(features, *, playlist_name, playlist_id, create_playlist, cue_mode):
        pushed.extend(features)
        return SimpleNamespace(playlist_name=playlist_name, playlist_id=playlist_id,
            added_to_collection=0, already_in_collection=1, added_to_playlist=0,
            already_in_playlist=1, removed_from_playlist=0, added_cues=0,
            added_loops=0, backup_dir=tmp_path)
    monkeypatch.setattr(bridge, "push_tracks_to_playlist", push)
    emitted = []
    monkeypatch.setattr(bridge, "_emit", lambda event, **payload: emitted.append((event, payload)))

    assert bridge._cmd_sync(SimpleNamespace(payload="-")) == 0
    assert missing.read_bytes() == b"saved missing audio"
    assert current.read_bytes() == b"saved current audio"
    assert "111111" in state.track_ids
    assert state.track_ids == (["111111", "222222"] if source_present else ["111111"])
    assert len(pushed) == int(source_present)
    done = next(payload for event, payload in emitted if event == "done")
    assert done["removed"] == 0
    assert done["retained"] == 1

    # If the track is re-liked/restored, it is still known rather than new.
    plan.entries.append(DownloadEntry(url="https://soundcloud.com/u/restored", id="111111", title="Taken down"))
    plans = []
    monkeypatch.setattr(bridge, "_print_json", plans.append)
    assert bridge._cmd_sync_plan(SimpleNamespace(url=url)) == 0
    assert plans[0]["added"] == []


@pytest.mark.parametrize("scenario", ["deleted_folder", "deleted_track", "new_destination"])
def test_sync_redownloads_missing_tracked_files(monkeypatch, tmp_path, scenario):
    original = tmp_path / "original"
    target = tmp_path / "new" if scenario == "new_destination" else original
    if scenario != "deleted_folder":
        original.mkdir()
        (original / "Keep [222222].mp3").write_bytes(b"existing audio")
    if scenario == "new_destination":
        (original / "Restore [111111].mp3").write_bytes(b"audio in old destination")
    entries = [
        DownloadEntry(url="https://soundcloud.com/u/restore", id="111111", title="Restore"),
        DownloadEntry(url="https://soundcloud.com/u/keep", id="222222", title="Keep"),
    ]
    state = SyncState(url="https://soundcloud.com/u/sets/list", title="List",
                      target_dir=str(original), track_ids=["111111", "222222", "333333"])
    monkeypatch.setattr(bridge, "load_state", lambda _: state)
    monkeypatch.setattr(bridge, "save_state", lambda _: None)
    monkeypatch.setattr(bridge, "load_config", lambda: AppConfig())
    monkeypatch.setattr(bridge, "collect_download_plan", lambda _, **kwargs: DownloadPlan(entries, title="List"))
    plans = []
    monkeypatch.setattr(bridge, "_print_json", plans.append)
    monkeypatch.setattr(bridge, "_emit", lambda *a, **k: None)
    bridge._cmd_sync_plan(SimpleNamespace(url=state.url))
    expected_plan = ["111111", "222222"] if scenario == "deleted_folder" else ["111111"] if scenario == "deleted_track" else []
    assert [entry["id"] for entry in plans[0]["added"]] == expected_plan
    assert plans[0]["removed_ids"] == ["333333"]

    downloaded = []
    def download(requested, *, output_dir, **kwargs):
        results = []
        for entry in requested:
            downloaded.append(entry.id)
            path = output_dir / f"{entry.title} [{entry.id}].mp3"
            path.write_bytes(b"new audio")
            results.append(DownloadResult(entry=entry, ok=True, output_paths=(path,)))
        return results
    monkeypatch.setattr(bridge, "download_entries", download)
    monkeypatch.setattr(bridge, "_load_payload", lambda _: {
        "url": state.url, "target_dir": str(target), "push": True, "write_tags": False,
    })
    monkeypatch.setattr(bridge, "_analyze_paths_parallel", lambda paths, **k: [_features(path) for path in paths])
    monkeypatch.setattr(bridge, "rekordbox_running", lambda: False)
    pushed = []
    def push(features, **kwargs):
        pushed.extend(features)
        return SimpleNamespace(playlist_name="List", playlist_id="playlist",
            added_to_collection=0, already_in_collection=2, added_to_playlist=0,
            already_in_playlist=2, removed_from_playlist=0, added_cues=0,
            added_loops=0, backup_dir=tmp_path)
    monkeypatch.setattr(bridge, "push_tracks_to_playlist", push)

    assert bridge._cmd_sync(SimpleNamespace(payload="-")) == 0
    assert downloaded == (["111111"] if scenario == "deleted_track" else ["111111", "222222"])
    assert len(pushed) == 2
    assert all(Path(feature.path).parent == target for feature in pushed)
    assert "333333" in state.track_ids
    if scenario == "deleted_track":
        assert (original / "Keep [222222].mp3").read_bytes() == b"existing audio"
    bridge._cmd_sync_plan(SimpleNamespace(url=state.url))
    assert plans[-1]["added"] == []


@pytest.mark.parametrize("old_title, expected_title", [("2297553080", "Apathy"), ("My manual title", "My manual title")])
def test_sync_repairs_old_id_titles_without_redownloading(monkeypatch, tmp_path, old_title, expected_title):
    from dataclasses import replace
    path = tmp_path / "2297553080 [2297553080].mp3"
    path.write_bytes(b"saved")
    entry = DownloadEntry(url="https://api-v2.soundcloud.com/tracks/2297553080", id="2297553080", title="Apathy", artist="Brunello, Hilel Lev")
    state = SyncState(url="https://soundcloud.com/u/sets/set", target_dir=str(tmp_path), track_ids=[entry.id])
    monkeypatch.setattr(bridge, "load_state", lambda _: state)
    monkeypatch.setattr(bridge, "save_state", lambda _: None)
    monkeypatch.setattr(bridge, "load_config", lambda: AppConfig())
    monkeypatch.setattr(bridge, "collect_download_plan", lambda _, **kwargs: DownloadPlan([entry], title="Set"))
    monkeypatch.setattr(bridge, "_load_payload", lambda _: {"url": state.url, "push": False})
    monkeypatch.setattr(bridge, "download_entries", lambda *a, **k: pytest.fail("No audio download needed"))
    monkeypatch.setattr(bridge, "_analyze_paths_parallel", lambda *a, **k: [replace(_features(path), source_id=entry.id, title=old_title, artist="")])
    written = []
    monkeypatch.setattr(bridge, "write_id3_tags", lambda path, features: written.append(features))
    monkeypatch.setattr(bridge, "_emit", lambda *a, **k: None)
    assert bridge._cmd_sync(SimpleNamespace(payload="-")) == 0
    assert written[0].title == expected_title
    assert written[0].artist == entry.artist


def test_download_outcome_exposes_successful_fallback(tmp_path):
    path = tmp_path / "Recovered [123].mp3"
    result = DownloadResult(
        entry=DownloadEntry(
            url="https://soundcloud.com/user/recovered",
            id="123",
            title="Recovered",
        ),
        ok=True,
        output_paths=(path,),
        download_method="klickaud",
        fallback_attempted=True,
        primary_error="DRM protected",
    )

    assert bridge._download_outcome_to_dict(result) == {
        "id": "123",
        "title": "Recovered",
        "url": "https://soundcloud.com/user/recovered",
        "outcome": "fallback_succeeded",
        "download_method": "klickaud",
        "fallback_attempted": True,
        "primary_error": "DRM protected",
        "error": "",
        "paths": [str(path)],
    }


def test_main_with_url_still_supports_dry_run(monkeypatch, capsys, tmp_path):
    cfg = AppConfig(output_dir=str(tmp_path), archive_path=str(tmp_path / ".archive"))
    monkeypatch.setattr(cli, "load_config", lambda: cfg)
    monkeypatch.setattr(
        cli,
        "collect_download_plan",
        lambda urls, verbose=False: DownloadPlan(
            [DownloadEntry(url=urls[0], id="1", title="Track")]
        ),
    )

    assert cli.main(["--dry-run", "https://soundcloud.com/user/track"]) == 0

    output = capsys.readouterr().out
    assert "found 1 track(s)" in output
    assert "Track" in output


def test_sync_likes_subcommand_uses_configured_likes(monkeypatch, tmp_path):
    cfg = AppConfig(
        soundcloud_username="me",
        output_dir=str(tmp_path),
        archive_path=str(tmp_path / ".archive"),
    )
    seen: dict[str, object] = {}

    def fake_collect(urls, verbose=False):
        seen["urls"] = urls
        return DownloadPlan([DownloadEntry(url="https://soundcloud.com/u/t", id="1", title="Track")])

    monkeypatch.setattr(cli, "load_config", lambda: cfg)
    monkeypatch.setattr(cli, "collect_download_plan", fake_collect)

    assert cli.main(["sync-likes", "--dry-run", "--limit", "1"]) == 0

    assert seen["urls"] == ["https://soundcloud.com/me/likes"]


def test_sync_likes_subcommand_accepts_username(monkeypatch, tmp_path):
    cfg = AppConfig(output_dir=str(tmp_path), archive_path=str(tmp_path / ".archive"))
    seen: dict[str, object] = {}

    def fake_collect(urls, verbose=False):
        seen["urls"] = urls
        return DownloadPlan([DownloadEntry(url="https://soundcloud.com/u/t", id="1", title="Track")])

    monkeypatch.setattr(cli, "load_config", lambda: cfg)
    monkeypatch.setattr(cli, "collect_download_plan", fake_collect)

    assert cli.main(["sync-likes", "another-dj", "--dry-run"]) == 0
    assert seen["urls"] == ["https://soundcloud.com/another-dj/likes"]


def test_sync_likes_requires_username_when_not_configured(monkeypatch, capsys, tmp_path):
    cfg = AppConfig(output_dir=str(tmp_path), archive_path=str(tmp_path / ".archive"))
    monkeypatch.setattr(cli, "load_config", lambda: cfg)

    assert cli.main(["sync-likes", "--dry-run"]) == 2
    assert "enter a SoundCloud username" in capsys.readouterr().err


def test_analyze_subcommand_accepts_empty_folder(monkeypatch, tmp_path, capsys):
    cfg = AppConfig(output_dir=str(tmp_path), archive_path=str(tmp_path / ".archive"))
    monkeypatch.setattr(cli, "load_config", lambda: cfg)

    assert cli.main(["analyze", str(tmp_path)]) == 1

    assert "No audio files" in capsys.readouterr().out


def test_bare_output_folder_name_resolves_under_downloads():
    assert cli._resolve_download_path("set-2", Path("/tmp/default")) == (
        Path.home() / "Downloads" / "set-2"
    )


def test_empty_output_folder_uses_default(tmp_path):
    assert cli._resolve_download_path("", tmp_path) == tmp_path


def test_archive_is_local_to_output_folder(tmp_path):
    assert cli._archive_for_output(tmp_path) == tmp_path / ".soundcloud-archive.txt"


def test_archive_is_source_specific_and_seeded_from_legacy(tmp_path):
    legacy = tmp_path / ".soundcloud-archive.txt"
    legacy.write_text("soundcloud 123\n")

    archive = cli._archive_for_output(tmp_path, "Set 2")

    assert archive == tmp_path / ".soundcloud-archives" / "set-2.txt"
    assert archive.read_text() == "soundcloud 123\n"


def test_no_arg_python_cli_points_to_opentui_launcher(monkeypatch, capsys, tmp_path):
    cfg = AppConfig(output_dir=str(tmp_path), archive_path=str(tmp_path / ".archive"))
    monkeypatch.setattr(cli, "load_config", lambda: cfg)

    assert cli.main([]) == 0

    assert "OpenTUI" in capsys.readouterr().out


def test_bridge_push_uses_payload_features(monkeypatch, tmp_path):
    pushed: dict[str, object] = {}
    emitted: list[tuple[str, dict[str, object]]] = []

    def fake_push(features, *, playlist_name, create_playlist, playlist_id=None, cue_mode="off"):
        pushed["tracks"] = [item.title for item in features]
        pushed["playlist_name"] = playlist_name
        pushed["create_playlist"] = create_playlist
        pushed["playlist_id"] = playlist_id
        pushed["cue_mode"] = cue_mode
        return SimpleNamespace(
            playlist_name=playlist_name,
            playlist_id="pl-1",
            added_to_collection=1,
            already_in_collection=0,
            added_to_playlist=1,
            already_in_playlist=0,
            removed_from_playlist=0,
            added_cues=0,
            skipped_cues=0,
            added_loops=0,
            skipped_loops=0,
            backup_dir=tmp_path / "backup",
        )

    monkeypatch.setattr(
        bridge,
        "_load_payload",
        lambda _payload: {
            "features": [features_to_dict(_features(tmp_path / "track.mp3"))],
            "playlist_name": "Set",
            "create_playlist": True,
        },
    )
    monkeypatch.setattr(bridge, "push_tracks_to_playlist", fake_push)
    monkeypatch.setattr(bridge, "_emit", lambda event, **payload: emitted.append((event, payload)))

    assert bridge._cmd_push(SimpleNamespace(payload="-")) == 0

    assert pushed == {
        "tracks": ["Track"],
        "playlist_name": "Set",
        "create_playlist": True,
        "playlist_id": None,
        "cue_mode": "off",
    }
    assert emitted[-1][0] == "done"


def test_bridge_main_emits_clean_json_for_unexpected_errors(monkeypatch, capsys):
    def fail(_args):
        raise Exception("db exploded")

    monkeypatch.setattr(bridge, "_cmd_config", fail)

    assert bridge.main(["config"]) == 1

    output = capsys.readouterr()
    payload = json.loads(output.out)
    assert payload["event"] == "error"
    assert payload["message"] == "Exception: db exploded"
    assert "Traceback" not in output.err


def test_bridge_sync_resolves_plain_target_folder_under_downloads(monkeypatch, tmp_path):
    state = SyncState(url="https://soundcloud.com/user/sets/demo")
    saved: list[SyncState] = []
    emitted: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(Path, "home", classmethod(lambda _cls: tmp_path))
    monkeypatch.setattr(bridge, "_load_payload", lambda _: {
        "url": state.url,
        "target_dir": "demo-set",
        "analyze": False,
        "write_tags": False,
        "push": False,
    })
    monkeypatch.setattr(bridge, "load_config", lambda: AppConfig())
    monkeypatch.setattr(bridge, "load_state", lambda _url: state)
    monkeypatch.setattr(bridge, "save_state", lambda value: saved.append(value))
    monkeypatch.setattr(bridge, "collect_download_plan", lambda _urls, **kwargs: DownloadPlan([], title="Demo Set"))
    monkeypatch.setattr(bridge, "_emit", lambda event, **payload: emitted.append((event, payload)))

    assert bridge._cmd_sync(SimpleNamespace(payload="-")) == 0

    expected = tmp_path / "Downloads" / "demo-set"
    assert expected.is_dir()
    assert saved[0].target_dir == str(expected)
    assert emitted[-1][1]["target_dir"] == str(expected)


def test_sync_plan_retries_protected_tracks_without_local_audio(monkeypatch, tmp_path):
    state = SyncState(
        url="https://soundcloud.com/user/likes",
        track_ids=["1"],
        protected_ids=["2"],
        target_dir=str(tmp_path),
    )
    (tmp_path / "One [1].mp3").write_bytes(b"saved audio")
    output: dict[str, object] = {}
    monkeypatch.setattr(bridge, "load_state", lambda _url: state)
    monkeypatch.setattr(
        bridge,
        "collect_download_plan",
        lambda _urls, **kwargs: DownloadPlan(
            [
                DownloadEntry(url="https://soundcloud.com/u/one", id="1", title="One"),
                DownloadEntry(url="https://soundcloud.com/u/two", id="2", title="Two"),
            ],
            title="Likes",
        ),
    )
    monkeypatch.setattr(bridge, "_emit", lambda *args, **kwargs: None)
    monkeypatch.setattr(bridge, "_print_json", lambda payload: output.update(payload))

    assert bridge._cmd_sync_plan(SimpleNamespace(url=state.url)) == 0

    assert output["added"] == [
        {
            "url": "https://soundcloud.com/u/two",
            "id": "2",
            "title": "Two",
            "label": "Two",
        }
    ]
    assert output["unchanged_count"] == 1
    assert output["is_first_sync"] is False


def test_sync_persists_protected_downloads_for_future_deltas(monkeypatch, tmp_path):
    url = "https://soundcloud.com/user/likes"
    state = SyncState(url=url)
    entry = DownloadEntry(url="https://soundcloud.com/u/protected", id="2376567626", title="Protected")
    saved: list[SyncState] = []
    monkeypatch.setattr(bridge, "_load_payload", lambda _payload: {
        "url": url,
        "target_dir": str(tmp_path),
        "analyze": False,
        "write_tags": False,
        "push": False,
    })
    monkeypatch.setattr(bridge, "load_config", lambda: AppConfig())
    monkeypatch.setattr(bridge, "load_state", lambda _url: state)
    monkeypatch.setattr(bridge, "save_state", lambda value: saved.append(value))
    monkeypatch.setattr(bridge, "collect_download_plan", lambda _urls, **kwargs: DownloadPlan([entry], title="Likes"))
    monkeypatch.setattr(
        bridge,
        "download_entries",
        lambda *args, **kwargs: [DownloadResult(entry=entry, ok=False, error="DRM protected", reason="protected")],
    )
    monkeypatch.setattr(bridge, "_emit", lambda *args, **kwargs: None)

    assert bridge._cmd_sync(SimpleNamespace(payload="-")) == 0

    assert saved[0].track_ids == []
    assert saved[0].protected_ids == ["2376567626"]


def test_parallel_analyze_preserves_order_and_uses_pool(monkeypatch, tmp_path):
    """With >= 3 paths, the helper dispatches through ProcessPoolExecutor and
    returns results in the original input order."""
    from app import soundcloud_bridge

    paths = [tmp_path / f"{i}.mp3" for i in range(5)]
    for p in paths:
        p.write_bytes(b"")

    received_workers: dict[str, int] = {}

    class FakeFuture:
        def __init__(self, result):
            self._result = result
        def result(self):
            return self._result

    class FakeExecutor:
        def __init__(self, max_workers):
            received_workers["max_workers"] = max_workers
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def submit(self, fn, item):
            # Return a feature whose source_id encodes the input order so we can
            # assert reordering happens correctly when futures arrive out-of-order.
            path = Path(item[0])
            return FakeFuture(_features(path))

    def fake_as_completed(futures):
        # Yield in reverse to prove ordering is restored.
        return reversed(list(futures))

    monkeypatch.setattr(soundcloud_bridge, "ProcessPoolExecutor", FakeExecutor)
    monkeypatch.setattr(soundcloud_bridge, "as_completed", fake_as_completed)
    monkeypatch.setattr(soundcloud_bridge, "_emit", lambda *a, **k: None)

    result = soundcloud_bridge._analyze_paths_parallel(
        paths,
        output_dir=tmp_path,
        extract_vocal_stems=False,
        workers=3,
    )

    assert [Path(f.path).name for f in result] == [p.name for p in paths]
    assert received_workers["max_workers"] == 3


def test_parallel_analyze_falls_back_to_serial_for_small_batches(monkeypatch, tmp_path):
    """A 2-track batch should never spawn a pool — overhead exceeds savings."""
    from app import soundcloud_bridge

    paths = [tmp_path / "a.mp3", tmp_path / "b.mp3"]
    for p in paths:
        p.write_bytes(b"")

    called: dict[str, bool] = {"pool": False}

    class ShouldNotRun:
        def __init__(self, *args, **kwargs):
            called["pool"] = True
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def submit(self, fn, item): raise AssertionError("should not submit")

    monkeypatch.setattr(soundcloud_bridge, "ProcessPoolExecutor", ShouldNotRun)
    monkeypatch.setattr(soundcloud_bridge, "_emit", lambda *a, **k: None)
    monkeypatch.setattr(
        soundcloud_bridge,
        "analyze_file",
        lambda path, **kwargs: _features(path),
    )

    result = soundcloud_bridge._analyze_paths_parallel(
        paths,
        output_dir=tmp_path,
        extract_vocal_stems=False,
        workers=4,
    )

    assert called["pool"] is False
    assert [Path(feature.path).name for feature in result] == [path.name for path in paths]


def test_packaged_parallel_analyze_uses_bounded_threads(monkeypatch, tmp_path):
    from app import soundcloud_bridge

    paths = [tmp_path / f"{i}.mp3" for i in range(4)]
    for path in paths:
        path.write_bytes(b"")

    received_workers: dict[str, int] = {}

    class FakeFuture:
        def __init__(self, result):
            self._result = result

        def result(self):
            return self._result

    class FakeExecutor:
        def __init__(self, max_workers):
            received_workers["max_workers"] = max_workers

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def submit(self, fn, item):
            return FakeFuture(_features(Path(item[0])))

    class ShouldNotRun:
        def __init__(self, *args, **kwargs):
            raise AssertionError("packaged analysis must not spawn processes")

    monkeypatch.setattr(soundcloud_bridge, "_packaged_runtime", lambda: True)
    monkeypatch.setattr(soundcloud_bridge, "ThreadPoolExecutor", FakeExecutor)
    monkeypatch.setattr(soundcloud_bridge, "ProcessPoolExecutor", ShouldNotRun)
    monkeypatch.setattr(soundcloud_bridge, "as_completed", lambda futures: list(futures))
    monkeypatch.setattr(soundcloud_bridge, "_emit", lambda *args, **kwargs: None)

    result = soundcloud_bridge._analyze_paths_parallel(
        paths,
        output_dir=tmp_path,
        extract_vocal_stems=False,
        workers=4,
    )

    assert [Path(feature.path).name for feature in result] == [path.name for path in paths]
    assert received_workers["max_workers"] == 2


def test_reanalyze_playlist_filters_by_content_ids_and_skips_missing(monkeypatch, tmp_path):
    """The reanalyze handler honors the content_ids filter, drops files that
    don't exist on disk, and pushes the rest back to the same playlist."""
    from app import soundcloud_bridge
    from app.rekordbox_sync import PlaylistTrack

    existing = tmp_path / "present.mp3"
    existing.write_bytes(b"")
    missing = tmp_path / "gone.mp3"
    other = tmp_path / "not-selected.mp3"
    other.write_bytes(b"")

    fake_tracks = [
        PlaylistTrack("c1", "Present", "", str(existing), True, True),
        PlaylistTrack("c2", "Gone", "", str(missing), False, True),
        PlaylistTrack("c3", "Other", "", str(other), True, True),
    ]
    monkeypatch.setattr(soundcloud_bridge, "list_playlist_tracks", lambda pid: fake_tracks)
    monkeypatch.setattr(soundcloud_bridge, "rekordbox_running", lambda: False)
    monkeypatch.setattr(
        soundcloud_bridge,
        "list_playlists",
        lambda: [SimpleNamespace(id="pl-1", name="Set", path="Set", is_folder=False, song_count=3)],
    )

    analyzed_paths: list[Path] = []

    def fake_parallel(paths, **kwargs):
        analyzed_paths.extend(paths)
        return [_features(p) for p in paths]

    pushed: dict[str, object] = {}

    def fake_push(
        features,
        *,
        playlist_name,
        create_playlist,
        playlist_id=None,
        remove_paths=None,
        cue_mode="off",
    ):
        pushed["features"] = [Path(f.path).name for f in features]
        pushed["playlist_id"] = playlist_id
        pushed["create_playlist"] = create_playlist
        pushed["cue_mode"] = cue_mode
        return SimpleNamespace(
            playlist_name=playlist_name,
            playlist_id=playlist_id or "pl-1",
            added_to_collection=0,
            already_in_collection=len(features),
            added_to_playlist=0,
            already_in_playlist=len(features),
            removed_from_playlist=0,
            added_cues=3,
            skipped_cues=0,
            added_loops=2,
            skipped_loops=0,
            backup_dir=tmp_path / "backup",
        )

    monkeypatch.setattr(soundcloud_bridge, "_analyze_paths_parallel", fake_parallel)
    monkeypatch.setattr(soundcloud_bridge, "push_tracks_to_playlist", fake_push)
    monkeypatch.setattr(soundcloud_bridge, "write_id3_tags", lambda *a, **k: None)

    emitted: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(soundcloud_bridge, "_emit", lambda event, **payload: emitted.append((event, payload)))
    monkeypatch.setattr(
        soundcloud_bridge,
        "_load_payload",
        lambda _: {"playlist_id": "pl-1", "content_ids": ["c1", "c2"], "force_no_cache": True},
    )

    rc = soundcloud_bridge._cmd_reanalyze_playlist(SimpleNamespace(payload="-"))

    assert rc == 0
    # c3 was not requested; c2 is missing on disk; only c1 should be analyzed.
    assert [p.name for p in analyzed_paths] == ["present.mp3"]
    assert pushed["features"] == ["present.mp3"]
    assert pushed["playlist_id"] == "pl-1"
    assert pushed["cue_mode"] == "off"
    assert pushed["create_playlist"] is False
    final_event = next((p for ev, p in emitted if ev == "done"), None)
    assert final_event is not None
    assert final_event["analyzed"] == 1
    assert final_event["skipped_missing"] == 1
    assert final_event["added_cues"] == 3
    assert any(m["content_id"] == "c2" for m in final_event["missing"])


def _features(path: Path) -> TrackFeatures:
    return TrackFeatures(
        path=str(path),
        title="Track",
        artist="Artist",
        duration_sec=180.0,
        sample_rate=44100,
        bpm=128.0,
        musical_key="Dm",
        camelot_key="7A",
        key_confidence=0.8,
        loudness_dbfs=-10.0,
        peak_dbfs=-0.1,
        energy=8,
        first_downbeat_sec=0.5,
        cue_hints=[],
    )
