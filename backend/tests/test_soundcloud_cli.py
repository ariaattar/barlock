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
            added_to_collection=1,
            already_in_collection=0,
            added_to_playlist=1,
            already_in_playlist=0,
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
