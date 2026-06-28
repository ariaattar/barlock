from __future__ import annotations

import argparse
import re
import shutil
import sys
from dataclasses import replace
from pathlib import Path

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.prompt import Confirm, IntPrompt, Prompt
from rich.table import Table

from .audio_features import TrackFeatures, analyze_file, audio_files, write_id3_tags
from .rekordbox_sync import (
    PlaylistInfo,
    doctor,
    list_playlists,
    push_tracks_to_playlist,
    rekordbox_running,
    write_m3u,
    write_rekordbox_xml,
)
from .soundcloud_config import AppConfig, CONFIG_PATH, load_config, save_config, update_output_paths
from .soundcloud_downloader import (
    DEFAULT_FRAGMENTS,
    DEFAULT_QUALITY,
    DEFAULT_WORKERS,
    DownloadEntry,
    DownloadResult,
    SoundCloudDownloadError,
    collect_download_plan,
    download_entries,
    paths_for_entries,
)

console = Console()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="soundcloud-dl",
        description="Interactive SoundCloud downloader, analyzer, and Rekordbox importer.",
    )
    parser.add_argument("urls", nargs="*", help="SoundCloud URL(s); omitted URLs open the menu")
    parser.add_argument("-o", "--output-dir", type=Path)
    parser.add_argument("-w", "--workers", type=int, default=None)
    parser.add_argument("--fragments", type=int, default=None)
    parser.add_argument("--quality", default=None)
    parser.add_argument("--ffmpeg")
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("-i", "--interactive", action="store_true")
    parser.add_argument("-y", "--yes", action="store_true")
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    raw_args = list(sys.argv[1:] if argv is None else argv)
    config = load_config()

    try:
        if raw_args[:1] == ["sync-likes"]:
            args = _build_sync_parser().parse_args(raw_args[1:])
            return TerminalApp(config).sync_likes(
                dry_run=args.dry_run,
                limit=args.limit,
                analyze=not args.no_analyze,
                write_tags=not args.no_tags,
                rekordbox=not args.no_rekordbox,
                prompt_options=False,
            )
        if raw_args[:1] == ["analyze"]:
            args = _build_analyze_parser().parse_args(raw_args[1:])
            return TerminalApp(config).analyze_folder(
                args.folder,
                write_tags=args.write_tags,
                prompt_options=False,
            )
        if raw_args[:1] == ["doctor"]:
            return TerminalApp(config).run_doctor()

        parser = build_parser()
        args = parser.parse_args(raw_args)
        if args.interactive or not args.urls:
            return TerminalApp(config).run()
        return _download_main(args, parser, config)
    except KeyboardInterrupt:
        console.print("\n[yellow]Cancelled[/yellow]")
        return 130


def _build_sync_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="soundcloud-dl sync-likes")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--no-analyze", action="store_true")
    parser.add_argument("--no-tags", action="store_true")
    parser.add_argument("--no-rekordbox", action="store_true")
    return parser


def _build_analyze_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="soundcloud-dl analyze")
    parser.add_argument("folder", nargs="?", type=Path)
    parser.add_argument("--write-tags", action="store_true")
    return parser


class TerminalApp:
    def __init__(self, config: AppConfig) -> None:
        self.config = config

    def run(self) -> int:
        while True:
            self._header()
            table = Table(box=box.SIMPLE, show_header=False)
            table.add_column("Key", style="cyan", width=4)
            table.add_column("Action")
            table.add_row("1", "Download SoundCloud URL or playlist")
            table.add_row("2", f"Sync likes for @{self.config.soundcloud_username}")
            table.add_row("3", "Analyze/tag local download folder")
            table.add_row("4", "Push analyzed tracks to Rekordbox playlist")
            table.add_row("5", "Rekordbox doctor")
            table.add_row("6", "Settings")
            table.add_row("q", "Quit")
            console.print(table)

            choice = Prompt.ask("Choose", default="1").strip().lower()
            if choice in {"q", "quit", "exit"}:
                return 0
            if choice == "1":
                self.download_urls_flow()
            elif choice == "2":
                self.sync_likes()
            elif choice == "3":
                self.analyze_folder(None, write_tags=None)
            elif choice == "4":
                self.push_folder_flow()
            elif choice == "5":
                self.run_doctor()
            elif choice == "6":
                self.settings_flow()
            else:
                console.print("[red]Unknown choice[/red]")

    def download_urls_flow(self) -> int:
        urls = self._prompt_urls()
        if not urls:
            return 0
        return self._download_and_process(urls, source_name="SoundCloud Download")

    def sync_likes(
        self,
        *,
        dry_run: bool = False,
        limit: int | None = None,
        analyze: bool | None = None,
        write_tags: bool | None = None,
        rekordbox: bool | None = None,
        prompt_options: bool = True,
    ) -> int:
        console.print(Panel.fit(f"Syncing likes: [cyan]{self.config.likes_url}[/cyan]"))
        return self._download_and_process(
            [self.config.likes_url],
            source_name="SoundCloud Likes",
            archive=None,
            dry_run=dry_run,
            limit=limit,
            analyze=analyze,
            write_tags=write_tags,
            rekordbox=rekordbox,
            prompt_options=prompt_options,
        )

    def analyze_folder(
        self,
        folder: Path | None,
        *,
        write_tags: bool | None = None,
        prompt_options: bool = True,
    ) -> int:
        target = folder or self._prompt_path("Folder to analyze", self.config.output_path)
        files = audio_files(target)
        if not files:
            console.print(f"[yellow]No audio files found in {target}[/yellow]")
            return 1
        console.print(f"Found [cyan]{len(files)}[/cyan] audio file(s)")
        features = self._analyze_files(files)
        if write_tags is None and prompt_options:
            write_tags = Confirm.ask("Write BPM/key/title tags to MP3 files?", default=self.config.write_tags)
        if write_tags:
            self._write_tags(features)
        if prompt_options and Confirm.ask("Generate Rekordbox import files?", default=self.config.create_import_files):
            self._write_import_files(features, target, source_name=target.name)
        if prompt_options and Confirm.ask("Push these tracks to a Rekordbox playlist?", default=False):
            self._push_features(features, default_playlist_name=target.name, output_dir=target)
        return 0

    def push_folder_flow(self) -> int:
        folder = self._prompt_path("Folder to push", self.config.output_path)
        files = audio_files(folder)
        if not files:
            console.print(f"[yellow]No audio files found in {folder}[/yellow]")
            return 1
        features = self._analyze_files(files)
        self._push_features(features, default_playlist_name=folder.name, output_dir=folder)
        return 0

    def run_doctor(self) -> int:
        data = doctor(self.config.output_path)
        table = Table(title="Rekordbox Doctor", box=box.SIMPLE)
        table.add_column("Check")
        table.add_column("Result", style="cyan")
        table.add_row("Rekordbox running", "yes" if data["rekordbox_running"] else "no")
        table.add_row("Collection tracks", str(data["collection_tracks"]))
        table.add_row("Local tracks", str(data["local_tracks"]))
        table.add_row("Missing files", str(len(data["missing_files"])))
        table.add_row("Downloaded files not in collection", str(len(data["unimported_output_files"])))
        console.print(table)
        self._print_sample("Missing files", data["missing_files"])
        self._print_sample("Downloaded but not imported", data["unimported_output_files"])
        return 0

    def settings_flow(self) -> int:
        self._header("Settings")
        self.config.soundcloud_username = Prompt.ask(
            "SoundCloud username",
            default=self.config.soundcloud_username,
        ).strip()
        output_dir = self._prompt_path("Default output folder", self.config.output_path)
        update_output_paths(self.config, output_dir)
        self.config.workers = IntPrompt.ask("Parallel track downloads", default=self.config.workers)
        self.config.fragments = IntPrompt.ask("Parallel fragments per track", default=self.config.fragments)
        self.config.quality = Prompt.ask("MP3 bitrate", default=self.config.quality)
        self.config.rekordbox_playlist = Prompt.ask(
            "Default Rekordbox playlist",
            default=self.config.rekordbox_playlist,
        )
        self.config.analyze_after_download = Confirm.ask(
            "Analyze after download by default?",
            default=self.config.analyze_after_download,
        )
        self.config.write_tags = Confirm.ask("Write tags by default?", default=self.config.write_tags)
        self.config.create_import_files = Confirm.ask(
            "Generate M3U/XML import files by default?",
            default=self.config.create_import_files,
        )
        self.config.direct_rekordbox_push = Confirm.ask(
            "Offer direct Rekordbox playlist push by default?",
            default=self.config.direct_rekordbox_push,
        )
        save_config(self.config)
        console.print(f"[green]Saved[/green] {CONFIG_PATH}")
        return 0

    def _download_and_process(
        self,
        urls: list[str],
        *,
        source_name: str,
        archive: Path | None = None,
        dry_run: bool = False,
        limit: int | None = None,
        analyze: bool | None = None,
        write_tags: bool | None = None,
        rekordbox: bool | None = None,
        prompt_options: bool = True,
    ) -> int:
        if prompt_options:
            output_dir = self._prompt_path("Output folder", self.config.output_path)
            workers = IntPrompt.ask("Parallel track downloads", default=self.config.workers)
            fragments = IntPrompt.ask("Parallel fragments per track", default=self.config.fragments)
            quality = Prompt.ask("MP3 bitrate", default=self.config.quality)
        else:
            output_dir = self.config.output_path
            workers = self.config.workers
            fragments = self.config.fragments
            quality = self.config.quality

        with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), console=console) as progress:
            task = progress.add_task("Expanding SoundCloud URL(s)...", total=None)
            plan = collect_download_plan(urls)
            progress.remove_task(task)
        entries = plan.entries
        if limit:
            entries = entries[:limit]
        source_name = plan.title or source_name
        archive = archive or _archive_for_output(output_dir, source_name)
        self._print_entries(entries)
        if dry_run or (prompt_options and Confirm.ask("Dry run only?", default=False)):
            return 0
        if prompt_options and not Confirm.ask(f"Download {len(entries)} track(s) to {output_dir}?", default=True):
            return 0

        results = download_entries(
            entries,
            output_dir=output_dir,
            workers=workers,
            quality=quality,
            fragments=fragments,
            archive=archive,
            status=lambda message: console.print(message),
        )
        self._write_failed_report(results, output_dir)
        downloaded_paths = self._paths_from_results(results)
        paths = paths_for_entries(output_dir, entries)
        if downloaded_paths and not paths:
            paths = downloaded_paths
        if not paths:
            console.print(
                "[yellow]No matching local MP3 files found after download. The source archive may have skipped everything.[/yellow]"
            )
            console.print(f"[yellow]Archive:[/yellow] {archive}")
            return 0
        if not downloaded_paths:
            console.print("[yellow]No new downloads; using existing local files for analysis and Rekordbox sync.[/yellow]")

        features: list[TrackFeatures] = []
        should_analyze = analyze if analyze is not None else (
            Confirm.ask("Analyze BPM/key/energy now?", default=self.config.analyze_after_download)
            if prompt_options
            else self.config.analyze_after_download
        )
        if should_analyze:
            features = self._analyze_files(paths, entries=entries, output_dir=output_dir)
        should_write_tags = write_tags if write_tags is not None else (
            Confirm.ask("Write ID3 tags?", default=self.config.write_tags)
            if prompt_options
            else self.config.write_tags
        )
        if features and should_write_tags:
            self._write_tags(features)
        should_import_files = (
            Confirm.ask("Generate Rekordbox M3U/XML import files?", default=self.config.create_import_files)
            if prompt_options
            else self.config.create_import_files
        )
        if features and should_import_files:
            self._write_import_files(features, output_dir, source_name=source_name)
        should_push = rekordbox if rekordbox is not None else (
            Confirm.ask(
                "Push directly to a Rekordbox playlist?",
                default=self.config.direct_rekordbox_push,
            )
            if prompt_options
            else self.config.direct_rekordbox_push
        )
        if features and should_push:
            self._push_features(features, default_playlist_name=source_name, output_dir=output_dir)
        return 0

    def _push_features(
        self,
        features: list[TrackFeatures],
        *,
        default_playlist_name: str | None = None,
        output_dir: Path | None = None,
    ) -> None:
        if rekordbox_running():
            console.print("[yellow]Rekordbox is open. Close it before direct DB push.[/yellow]")
            if Confirm.ask("Generate import files instead?", default=True):
                self._write_import_files(
                    features,
                    output_dir or self.config.output_path,
                    source_name=default_playlist_name or "SoundCloud",
                )
            return

        playlist_name, create, playlist_id = self._choose_playlist(default_playlist_name)
        if not Confirm.ask(f"Push {len(features)} track(s) to '{playlist_name}'?", default=True):
            return
        result = push_tracks_to_playlist(
            features,
            playlist_name=playlist_name,
            playlist_id=playlist_id,
            create_playlist=create,
        )
        table = Table(title="Rekordbox Push Complete", box=box.SIMPLE)
        table.add_column("Field")
        table.add_column("Value", style="cyan")
        table.add_row("Playlist", result.playlist_name)
        table.add_row("Added to collection", str(result.added_to_collection))
        table.add_row("Already in collection", str(result.already_in_collection))
        table.add_row("Added to playlist", str(result.added_to_playlist))
        table.add_row("Already in playlist", str(result.already_in_playlist))
        table.add_row("Auto cues added", str(result.added_cues))
        table.add_row("Auto cues skipped", str(result.skipped_cues))
        table.add_row("Auto loops added", str(result.added_loops))
        table.add_row("Auto loops skipped", str(result.skipped_loops))
        table.add_row("Backup", str(result.backup_dir))
        console.print(table)

    def _choose_playlist(self, default_playlist_name: str | None = None) -> tuple[str, bool, str | None]:
        default_name = (default_playlist_name or self.config.rekordbox_playlist).strip() or self.config.rekordbox_playlist
        console.print("1. Use existing playlist")
        console.print("2. Create new playlist")
        choice = Prompt.ask("Destination", choices=["1", "2"], default="1")
        if choice == "2":
            return Prompt.ask("New playlist name", default=default_name), True, None

        playlists = [pl for pl in list_playlists() if not pl.is_folder]
        search = Prompt.ask("Search existing playlists", default=default_name).strip().lower()
        matches = [pl for pl in playlists if search in pl.path.lower()] if search else playlists
        if not matches:
            console.print("[yellow]No matching playlists. Creating a new one instead.[/yellow]")
            return Prompt.ask("New playlist name", default=default_name), True, None
        self._print_playlists(matches[:20])
        index = IntPrompt.ask("Select playlist number", default=1)
        index = max(1, min(index, len(matches[:20])))
        selected = matches[index - 1]
        return selected.name, False, selected.id

    def _analyze_files(
        self,
        paths: list[Path],
        *,
        entries: list[DownloadEntry] | None = None,
        output_dir: Path | None = None,
    ) -> list[TrackFeatures]:
        url_by_id = {entry.id: entry.url for entry in entries or [] if entry.id}
        features: list[TrackFeatures] = []
        with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), console=console) as progress:
            task = progress.add_task("Analyzing tracks...", total=None)
            for path in paths:
                item = analyze_file(path, output_dir=output_dir or path.parent)
                source_url = url_by_id.get(item.source_id, item.source_url)
                if source_url:
                    item = replace(item, source_url=source_url)
                features.append(item)
            progress.remove_task(task)
        self._print_analysis(features)
        return features

    def _write_tags(self, features: list[TrackFeatures]) -> None:
        with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), console=console) as progress:
            task = progress.add_task("Writing ID3 tags...", total=None)
            for item in features:
                write_id3_tags(Path(item.path), item)
            progress.remove_task(task)
        console.print("[green]Tags written[/green]")

    def _write_import_files(
        self,
        features: list[TrackFeatures],
        output_dir: Path,
        *,
        source_name: str = "SoundCloud",
    ) -> None:
        safe_name = "".join(ch if ch.isalnum() or ch in {" ", "-", "_"} else "-" for ch in source_name).strip()
        m3u = write_m3u(features, output_dir / f"{safe_name}.m3u8")
        xml = write_rekordbox_xml(features, output_dir / "rekordbox-import.xml")
        console.print(f"[green]Wrote[/green] {m3u}")
        console.print(f"[green]Wrote[/green] {xml}")

    def _prompt_urls(self) -> list[str]:
        urls: list[str] = []
        console.print("Paste SoundCloud track, playlist, or likes links. Blank line starts.")
        while True:
            line = Prompt.ask("URL", default="").strip()
            if not line:
                return urls
            urls.extend(line.split())

    def _prompt_path(self, label: str, default: Path) -> Path:
        value = Prompt.ask(label, default=str(default.expanduser())).strip()
        return _resolve_download_path(value, default)

    def _header(self, title: str = "SoundCloud DL") -> None:
        console.print(
            Panel.fit(
                f"[bold]SoundCloud DL[/bold]\n"
                f"Output: [cyan]{self.config.output_path}[/cyan]\n"
                f"Rekordbox playlist: [cyan]{self.config.rekordbox_playlist}[/cyan]",
                title=title,
                border_style="cyan",
            )
        )

    def _print_entries(self, entries: list[DownloadEntry]) -> None:
        table = Table(title=f"Found {len(entries)} track(s)", box=box.SIMPLE)
        table.add_column("#", justify="right", width=4)
        table.add_column("Track")
        for index, entry in enumerate(entries[:25], start=1):
            table.add_row(str(index), entry.label)
        if len(entries) > 25:
            table.add_row("...", f"{len(entries) - 25} more")
        console.print(table)

    def _print_analysis(self, features: list[TrackFeatures]) -> None:
        table = Table(title="Analysis", box=box.SIMPLE)
        table.add_column("Track")
        table.add_column("BPM", justify="right")
        table.add_column("Key")
        table.add_column("Camelot")
        table.add_column("Energy", justify="right")
        for item in features[:20]:
            title = f"{item.artist} - {item.title}" if item.artist else item.title
            table.add_row(title[:55], f"{item.bpm:.1f}", item.musical_key, item.camelot_key, str(item.energy))
        if len(features) > 20:
            table.add_row("...", "", "", "", f"{len(features) - 20} more")
        console.print(table)

    def _print_playlists(self, playlists: list[PlaylistInfo]) -> None:
        table = Table(title="Playlists", box=box.SIMPLE)
        table.add_column("#", justify="right", width=4)
        table.add_column("Playlist")
        table.add_column("Tracks", justify="right")
        for index, playlist in enumerate(playlists, start=1):
            table.add_row(str(index), playlist.path, str(playlist.song_count))
        console.print(table)

    def _print_sample(self, title: str, values: list[str]) -> None:
        if not values:
            return
        console.print(f"[bold]{title}[/bold]")
        for value in values[:10]:
            console.print(f"- {value}")
        if len(values) > 10:
            console.print(f"... and {len(values) - 10} more")

    def _paths_from_results(self, results: list[DownloadResult]) -> list[Path]:
        paths: list[Path] = []
        for result in results:
            if result.ok:
                paths.extend(result.output_paths)
        return sorted(set(paths))

    def _write_failed_report(self, results: list[DownloadResult], output_dir: Path) -> None:
        failed = [result for result in results if not result.ok]
        if not failed:
            return
        path = output_dir / "failed-downloads.txt"
        lines = [f"{result.entry.url}\t{result.error}" for result in failed]
        path.write_text("\n".join(lines) + "\n")
        console.print(f"[yellow]Failed {len(failed)} track(s). Report: {path}[/yellow]")


def _download_main(args: argparse.Namespace, parser: argparse.ArgumentParser, config: AppConfig) -> int:
    workers = args.workers or config.workers or DEFAULT_WORKERS
    fragments = args.fragments or config.fragments or DEFAULT_FRAGMENTS
    quality = args.quality or config.quality or DEFAULT_QUALITY
    output_dir = _resolve_download_path(str(args.output_dir), config.output_path) if args.output_dir else config.output_path
    archive = args.archive

    if workers < 1:
        parser.error("--workers must be at least 1")
    if fragments < 1:
        parser.error("--fragments must be at least 1")
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be at least 1")

    try:
        plan = collect_download_plan(args.urls, verbose=args.verbose)
    except (SoundCloudDownloadError, ValueError) as exc:
        parser.exit(1, f"error: {exc}\n")

    entries = plan.entries
    if args.limit:
        entries = entries[: args.limit]

    console.print(f"found {len(entries)} track(s)")
    if args.dry_run:
        for index, entry in enumerate(entries, start=1):
            console.print(f"{index:>3}. {entry.label}")
            if entry.title:
                console.print(f"     {entry.url}")
        return 0

    try:
        results = download_entries(
            entries,
            output_dir=output_dir,
            workers=workers,
            quality=quality,
            fragments=fragments,
            ffmpeg=args.ffmpeg,
            archive=archive or _archive_for_output(output_dir, plan.title),
            verbose=args.verbose,
            status=lambda message: console.print(message),
        )
    except (SoundCloudDownloadError, ValueError) as exc:
        parser.exit(1, f"error: {exc}\n")

    failed = [result for result in results if not result.ok]
    if failed:
        console.print(f"[yellow]failed {len(failed)} of {len(results)} track(s)[/yellow]")
        for result in failed:
            console.print(f"- {result.entry.label}: {result.error}")
        return 1

    console.print(f"[green]downloaded {len(results)} track(s) to {output_dir.expanduser().resolve()}[/green]")
    return 0


def _resolve_download_path(value: str, default: Path) -> Path:
    if not value:
        return default.expanduser()
    path = Path(value).expanduser()
    if path.is_absolute():
        return path
    if len(path.parts) == 1:
        return Path.home() / "Downloads" / path
    return (Path.home() / "Downloads" / path).expanduser()


def _archive_for_output(output_dir: Path, source_name: str | None = None) -> Path:
    output_dir = output_dir.expanduser()
    if not source_name:
        return output_dir / ".soundcloud-archive.txt"

    archive_dir = output_dir / ".soundcloud-archives"
    archive_path = archive_dir / f"{_safe_slug(source_name)}.txt"
    legacy_path = output_dir / ".soundcloud-archive.txt"
    if not archive_path.exists() and legacy_path.exists():
        archive_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(legacy_path, archive_path)
    return archive_path


def _safe_slug(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip()).strip("-._").lower()
    return slug[:80] or "soundcloud"


if __name__ == "__main__":
    raise SystemExit(main())
