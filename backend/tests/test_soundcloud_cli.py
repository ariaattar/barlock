from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.audio_features import TrackFeatures
from app import soundcloud_cli as cli
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


def test_selector_fallback_accepts_number(monkeypatch, tmp_path):
    app = cli.TerminalApp(AppConfig(output_dir=str(tmp_path)))
    monkeypatch.setattr(app, "_interactive_selector_available", lambda: False)
    monkeypatch.setattr(cli.Prompt, "ask", lambda *args, **kwargs: "2")

    choice = app._select_option("Choose", [("One", "one"), ("Two", "two")])

    assert choice == "two"


def test_selector_fallback_supports_back(monkeypatch, tmp_path):
    app = cli.TerminalApp(AppConfig(output_dir=str(tmp_path)))
    monkeypatch.setattr(app, "_interactive_selector_available", lambda: False)
    monkeypatch.setattr(cli.Prompt, "ask", lambda *args, **kwargs: "b")

    with pytest.raises(cli.BackRequested):
        app._select_option("Choose", [("One", "one")])


def test_push_features_can_close_rekordbox_then_continue(monkeypatch, tmp_path):
    app = cli.TerminalApp(AppConfig(output_dir=str(tmp_path)))
    running = {"value": True}
    pushed: dict[str, object] = {}

    def fake_close_rekordbox():
        running["value"] = False
        return True

    def fake_push(features, *, playlist_name, create_playlist, playlist_id=None):
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

    monkeypatch.setattr(cli, "rekordbox_running", lambda: running["value"])
    monkeypatch.setattr(cli, "close_rekordbox", fake_close_rekordbox)
    monkeypatch.setattr(app, "_prompt_confirm", lambda *args, **kwargs: True)
    monkeypatch.setattr(app, "_choose_playlist", lambda default_playlist_name=None: ("Set", True, None))
    monkeypatch.setattr(cli, "push_tracks_to_playlist", fake_push)

    app._push_features([_features(tmp_path / "track.mp3")], default_playlist_name="Set", output_dir=tmp_path)

    assert pushed == {"playlist_name": "Set", "create_playlist": True, "playlist_id": None}


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
