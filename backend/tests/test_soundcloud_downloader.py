from __future__ import annotations

from pathlib import Path

import pytest

from app import soundcloud_downloader as sc


def test_validate_soundcloud_url_rejects_other_hosts():
    with pytest.raises(ValueError):
        sc.validate_soundcloud_url("https://example.com/not-soundcloud")


def test_collect_download_entries_expands_and_dedupes(monkeypatch):
    class FakeYDL:
        def __init__(self, opts):
            self.opts = opts

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def extract_info(self, url, download=False):
            assert not download
            return {
                "_type": "playlist",
                "entries": [
                    {
                        "_type": "url_transparent",
                        "id": "1",
                        "url": "https://soundcloud.com/user/one",
                        "title": "One",
                    },
                    {
                        "_type": "url_transparent",
                        "id": "2",
                        "url": "https://soundcloud.com/user/two",
                        "title": "Two",
                    },
                    {
                        "_type": "url_transparent",
                        "id": "1",
                        "url": "https://soundcloud.com/user/one",
                        "title": "One duplicate",
                    },
                ],
            }

    monkeypatch.setattr(sc, "_youtube_dl_cls", lambda: FakeYDL)

    entries = sc.collect_download_entries(["https://soundcloud.com/me/sets/set"])

    assert [entry.id for entry in entries] == ["1", "2"]
    assert [entry.url for entry in entries] == [
        "https://soundcloud.com/user/one",
        "https://soundcloud.com/user/two",
    ]


def test_collect_download_plan_reads_playlist_title(monkeypatch):
    class FakeYDL:
        def __init__(self, opts):
            self.opts = opts

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def extract_info(self, url, download=False):
            assert not download
            return {
                "_type": "playlist",
                "title": "set-2",
                "entries": [
                    {
                        "id": "1",
                        "url": "https://soundcloud.com/user/one",
                        "title": "One",
                    },
                ],
            }

    monkeypatch.setattr(sc, "_youtube_dl_cls", lambda: FakeYDL)

    plan = sc.collect_download_plan(["https://soundcloud.com/me/sets/set-2"])

    assert plan.title == "set-2"
    assert [entry.id for entry in plan.entries] == ["1"]


def test_download_entries_writes_expected_mp3_and_uses_quality(monkeypatch, tmp_path):
    seen_opts: list[dict] = []

    class FakeYDL:
        def __init__(self, opts):
            self.opts = opts
            seen_opts.append(opts)

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def extract_info(self, url, download=False):
            assert download
            track_id = url.rsplit("/", 1)[-1]
            Path(tmp_path / f"Song [{track_id}].mp3").write_bytes(b"mp3")
            return {"id": track_id, "title": "Song"}

    monkeypatch.setattr(sc, "_youtube_dl_cls", lambda: FakeYDL)
    monkeypatch.setattr(sc, "resolve_ffmpeg_location", lambda explicit=None: "/tmp/ffmpeg")

    results = sc.download_entries(
        [sc.DownloadEntry(url="https://soundcloud.com/u/abc", id="abc", title="Song")],
        output_dir=tmp_path,
        workers=1,
        quality="320",
        fragments=4,
        status=None,
    )

    assert results == [
        sc.DownloadResult(
            entry=sc.DownloadEntry(url="https://soundcloud.com/u/abc", id="abc", title="Song"),
            ok=True,
            output_paths=(tmp_path / "Song [abc].mp3",),
        )
    ]
    assert seen_opts[0]["postprocessors"][0]["preferredquality"] == "320"
    assert seen_opts[0]["concurrent_fragment_downloads"] == 4


def test_paths_for_entries_finds_existing_downloads_by_soundcloud_id(tmp_path):
    path = tmp_path / "Song [12345].mp3"
    path.write_bytes(b"mp3")

    assert sc.paths_for_entries(
        tmp_path,
        [sc.DownloadEntry(url="https://soundcloud.com/u/song", id="12345", title="Song")],
    ) == [path]
