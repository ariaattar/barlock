from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

CONFIG_DIR = Path.home() / ".config" / "soundcloud-dl"
CONFIG_PATH = CONFIG_DIR / "config.json"
DEFAULT_OUTPUT_DIR = Path.home() / "Downloads" / "SoundCloud.app_Set"


@dataclass
class AppConfig:
    soundcloud_username: str = "ariaattar"
    output_dir: str = str(DEFAULT_OUTPUT_DIR)
    workers: int = 8
    fragments: int = 8
    quality: str = "320"
    archive_path: str = str(DEFAULT_OUTPUT_DIR / ".soundcloud-archive.txt")
    rekordbox_playlist: str = "SoundCloud Likes"
    analyze_after_download: bool = True
    write_tags: bool = True
    create_import_files: bool = True
    direct_rekordbox_push: bool = True
    extract_vocal_stems: bool = False
    last_urls: list[str] = field(default_factory=list)

    @property
    def output_path(self) -> Path:
        return Path(self.output_dir).expanduser()

    @property
    def archive_file(self) -> Path:
        return Path(self.archive_path).expanduser()

    @property
    def likes_url(self) -> str:
        username = self.soundcloud_username.strip().strip("/")
        return f"https://soundcloud.com/{username}/likes"


def load_config() -> AppConfig:
    if not CONFIG_PATH.exists():
        cfg = AppConfig()
        save_config(cfg)
        return cfg

    try:
        raw = json.loads(CONFIG_PATH.read_text())
    except json.JSONDecodeError:
        backup = CONFIG_PATH.with_suffix(".broken.json")
        CONFIG_PATH.replace(backup)
        cfg = AppConfig()
        save_config(cfg)
        return cfg

    defaults = asdict(AppConfig())
    data: dict[str, Any] = {**defaults, **{k: v for k, v in raw.items() if k in defaults}}
    return AppConfig(**data)


def save_config(config: AppConfig) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(asdict(config), indent=2) + "\n")


def update_output_paths(config: AppConfig, output_dir: Path) -> AppConfig:
    config.output_dir = str(output_dir.expanduser())
    config.archive_path = str(output_dir.expanduser() / ".soundcloud-archive.txt")
    return config
