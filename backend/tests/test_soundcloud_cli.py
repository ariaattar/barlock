from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from app.audio_features import TrackFeatures
from app import soundcloud_bridge as bridge
from app import soundcloud_cli as cli
from app.soundcloud_common import features_to_dict
from app.soundcloud_config import AppConfig
from app.soundcloud_downloader import DownloadEntry, DownloadPlan


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

    def fake_push(features, *, playlist_name, create_playlist, playlist_id=None):
        pushed["tracks"] = [item.title for item in features]
        pushed["playlist_name"] = playlist_name
        pushed["create_playlist"] = create_playlist
        pushed["playlist_id"] = playlist_id
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
    }
    assert emitted[-1][0] == "done"


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
    assert [Path(f.path).name for f in result] == [p.name for p in paths]


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
