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


def test_plan_resolves_metadata_for_bare_api_track_entries(monkeypatch):
    class MetadataYDL:
        def __init__(self, opts):
            assert opts["ignore_no_formats_error"] is True
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def extract_info(self, url, download=False):
            assert download is False
            if "/sets/" in url:
                return {"title": "Set", "entries": [{"id": "2297553080", "url": "https://api-v2.soundcloud.com/tracks/2297553080"}]}
            return {"id": "2297553080", "title": "Apathy", "artists": ["Brunello", "Hilel Lev"]}
    monkeypatch.setattr(sc, "_youtube_dl_cls", lambda: MetadataYDL)
    [entry] = sc.collect_download_plan(["https://soundcloud.com/u/sets/set"]).entries
    assert entry.title == "Apathy"
    assert entry.artist == "Brunello, Hilel Lev"
    assert sc._klickaud_destination(Path("/tmp"), entry).name == "Apathy [2297553080].mp3"


def test_plan_metadata_is_parallel_bounded_and_reports_progress(monkeypatch):
    import threading

    barrier = threading.Barrier(4)
    lock = threading.Lock()
    active = 0
    peak = 0
    calls = []
    class MetadataYDL:
        def __init__(self, opts):
            assert opts["socket_timeout"] == 10
            assert opts["extractor_retries"] == 0
            assert opts["extractor_args"]["soundcloud"]["formats"] == ["none"]
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def extract_info(self, url, download=False):
            nonlocal active, peak
            assert download is False
            if "/sets/" in url:
                return {"title": "Set", "entries": [
                    {"id": str(index), "url": f"https://api-v2.soundcloud.com/tracks/{index}"}
                    for index in [0, 1, 2, 3, 0]
                ]}
            index = url.rsplit("/", 1)[-1]
            with lock:
                calls.append(index)
                active += 1
                peak = max(peak, active)
            try:
                barrier.wait(timeout=2)
                if index == "3":
                    raise TimeoutError("Unavailable metadata")
                return {"title": f"Track {index}", "artist": "Artist"}
            finally:
                with lock:
                    active -= 1
    monkeypatch.setattr(sc, "_youtube_dl_cls", lambda: MetadataYDL)
    progress = []
    plan = sc.collect_download_plan(["https://soundcloud.com/u/sets/set"], status=progress.append)
    assert peak == 4
    assert sorted(calls) == ["0", "1", "2", "3"]
    assert [entry.id for entry in plan.entries] == ["0", "1", "2", "3"]
    assert [entry.title for entry in plan.entries] == ["Track 0", "Track 1", "Track 2", None]
    assert progress == [f"Reading track details: {index}/4" for index in range(5)]


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


def test_download_retries_deleted_audio_despite_archive(monkeypatch, tmp_path):
    archive = tmp_path / ".archive"
    archive.write_text("soundcloud 111111\nsoundcloud 222222\nsoundcloud 333333\n")
    retained = tmp_path / "Keep [222222].mp3"
    retained.write_bytes(b"keep")
    downloaded = []

    class ArchivedYDL:
        def __init__(self, opts):
            self.archive = Path(opts["download_archive"])

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def extract_info(self, url, download=False):
            track_id = url.rsplit("/", 1)[-1]
            line = f"soundcloud {track_id}"
            if line in self.archive.read_text().splitlines():
                return None
            downloaded.append(track_id)
            (tmp_path / f"Restored [{track_id}].mp3").write_bytes(b"downloaded")
            with self.archive.open("a") as handle:
                handle.write(line + "\n")
            return {"id": track_id}

    monkeypatch.setattr(sc, "_youtube_dl_cls", lambda: ArchivedYDL)
    monkeypatch.setattr(sc, "resolve_ffmpeg_location", lambda _: "/tmp/ffmpeg")
    monkeypatch.setattr(sc, "download_klickaud_mp3", lambda *a, **k: pytest.fail("No fallback needed"))
    entries = [sc.DownloadEntry(url=f"https://soundcloud.com/u/{track_id}", id=track_id) for track_id in ["111111", "222222"]]
    results = sc.download_entries(entries, output_dir=tmp_path, archive=archive, workers=1)

    assert downloaded == ["111111"]
    assert all(result.ok and result.output_paths for result in results)
    assert retained.read_bytes() == b"keep"
    assert set(archive.read_text().splitlines()) == {"soundcloud 111111", "soundcloud 222222", "soundcloud 333333"}


def test_download_entries_uses_klickaud_fallback_and_records_archive(monkeypatch, tmp_path):
    class FailingYDL:
        def __init__(self, opts):
            self.opts = opts

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def extract_info(self, url, download=False):
            raise RuntimeError("DRM protected")

    calls: list[tuple[str, Path]] = []

    def fake_klickaud(url, destination, *, status=None):
        calls.append((url, destination))
        if status:
            status("Preparing audio")
        destination.write_bytes(b"fallback mp3")
        return destination

    monkeypatch.setattr(sc, "_youtube_dl_cls", lambda: FailingYDL)
    monkeypatch.setattr(sc, "resolve_ffmpeg_location", lambda explicit=None: "/tmp/ffmpeg")
    monkeypatch.setattr(sc, "download_klickaud_mp3", fake_klickaud)
    statuses: list[str] = []
    archive = tmp_path / ".downloads.archive.txt"
    entry = sc.DownloadEntry(
        url="https://soundcloud.com/u/protected",
        id="12345",
        title="Protected / Track",
        artist="Original artist",
    )

    results = sc.download_entries(
        [entry],
        output_dir=tmp_path,
        workers=1,
        archive=archive,
        status=statuses.append,
    )

    expected = tmp_path / "Protected - Track [12345].mp3"
    assert results == [
        sc.DownloadResult(
            entry=entry,
            ok=True,
            output_paths=(expected,),
            download_method="klickaud",
            fallback_attempted=True,
            primary_error="DRM protected",
        )
    ]
    assert calls == [(entry.url, expected)]
    from mutagen.easyid3 import EasyID3
    assert expected.read_bytes().endswith(b"fallback mp3")
    assert EasyID3(expected)["title"] == ["Protected / Track"]
    assert EasyID3(expected)["artist"] == ["Original artist"]
    assert archive.read_text() == "soundcloud 12345\n"
    assert any(f"trying KlickAud fallback for {entry.url}" in message for message in statuses)
    assert any(f"KlickAud: Preparing audio for {entry.url}" in message for message in statuses)
    assert any(f"KlickAud fallback succeeded for {entry.url}" in message for message in statuses)


def test_download_entries_reports_primary_and_fallback_errors(monkeypatch, tmp_path):
    class FailingYDL:
        def __init__(self, opts):
            self.opts = opts

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def extract_info(self, url, download=False):
            raise RuntimeError("HTTP Error 503")

    def fail_klickaud(*args, **kwargs):
        raise RuntimeError("worker unavailable")

    monkeypatch.setattr(sc, "_youtube_dl_cls", lambda: FailingYDL)
    monkeypatch.setattr(sc, "resolve_ffmpeg_location", lambda explicit=None: "/tmp/ffmpeg")
    monkeypatch.setattr(sc, "download_klickaud_mp3", fail_klickaud)
    entry = sc.DownloadEntry(url="https://soundcloud.com/u/song", id="123")

    [result] = sc.download_entries([entry], output_dir=tmp_path, workers=1)

    assert not result.ok
    assert result.reason == "download"
    assert result.download_method == "none"
    assert result.fallback_attempted
    assert result.primary_error == "HTTP Error 503"
    assert result.error == (
        "yt-dlp: HTTP Error 503; KlickAud fallback: worker unavailable"
    )


def test_paths_for_entries_finds_existing_downloads_by_soundcloud_id(tmp_path):
    path = tmp_path / "Song [12345].mp3"
    path.write_bytes(b"mp3")

    assert sc.paths_for_entries(
        tmp_path,
        [sc.DownloadEntry(url="https://soundcloud.com/u/song", id="12345", title="Song")],
    ) == [path]


def test_download_failure_reason_classifies_drm_as_protected():
    assert sc.download_failure_reason("ERROR: This video is DRM protected") == "protected"
    assert sc.download_failure_reason("HTTP Error 503") == "download"


def test_import_local_replacement_copies_mp3_with_soundcloud_id(tmp_path):
    source = tmp_path / "owned-copy.mp3"
    source.write_bytes(b"licensed audio")
    output = tmp_path / "managed"

    destination = sc.import_local_replacement(
        source,
        output_dir=output,
        track_id="2376567626",
        title="Love Forever / Kuuda",
    )

    assert destination == output / "Love Forever - Kuuda [2376567626].mp3"
    assert destination.read_bytes() == b"licensed audio"
    assert sc.paths_for_entries(
        output,
        [sc.DownloadEntry(url="https://soundcloud.com/test", id="2376567626")],
    ) == [destination]
