"""Configuration loader for the MTalk notifier."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import List


@dataclass(frozen=True)
class Target:
    """One monitored target inside MTalk (a chat room or an account)."""

    kind: str  # "room" or "account"
    name: str  # exact name shown in MTalk UI
    display: str  # human-friendly name shown in the popup

    @property
    def key(self) -> str:
        return f"{self.kind}:{self.name}"


@dataclass(frozen=True)
class PopupConfig:
    width: int = 340
    height: int = 200
    always_on_top: bool = True


@dataclass(frozen=True)
class AppConfig:
    targets: List[Target]
    window_title_hints: List[str]
    poll_interval_seconds: float
    sound_file: Path
    log_file: Path
    popup: PopupConfig
    prefer_events: bool = True

    root_dir: Path = field(default_factory=Path)

    @classmethod
    def load(cls, path: str | Path) -> "AppConfig":
        cfg_path = Path(path).resolve()
        root = cfg_path.parent
        data = json.loads(cfg_path.read_text(encoding="utf-8"))

        targets = [
            Target(
                kind=str(t.get("kind", "room")).lower(),
                name=str(t["name"]),
                display=str(t.get("display", t["name"])),
            )
            for t in data.get("targets", [])
        ]

        popup_data = data.get("popup", {}) or {}
        popup = PopupConfig(
            width=int(popup_data.get("width", 340)),
            height=int(popup_data.get("height", 200)),
            always_on_top=bool(popup_data.get("always_on_top", True)),
        )

        sound_file = Path(data.get("sound_file", "sounds/warning.mp3"))
        if not sound_file.is_absolute():
            sound_file = (root / sound_file).resolve()

        log_file = Path(data.get("log_file", "logs/mtalk_notifier.log"))
        if not log_file.is_absolute():
            log_file = (root / log_file).resolve()

        return cls(
            targets=targets,
            window_title_hints=[str(h) for h in data.get("window_title_hints", ["MTalk"])],
            poll_interval_seconds=float(data.get("poll_interval_seconds", 1.0)),
            sound_file=sound_file,
            log_file=log_file,
            popup=popup,
            prefer_events=bool(data.get("prefer_events", True)),
            root_dir=root,
        )
