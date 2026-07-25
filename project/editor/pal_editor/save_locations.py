"""Safe discovery helpers for Palworld save locations."""

from __future__ import annotations

import os
from pathlib import Path


def default_save_games_dir() -> Path:
    """Return Palworld's standard local save root for the current user."""

    local_app_data = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    return local_app_data / "Pal" / "Saved" / "SaveGames"


def find_level_saves(root: Path) -> tuple[Path, ...]:
    """Find normal world Level.sav files, excluding Palworld backups."""

    root = root.expanduser()
    if not root.is_dir():
        return ()
    candidates = (
        path
        for path in root.rglob("Level.sav")
        if "backup" not in {part.casefold() for part in path.parts}
    )
    return tuple(sorted(candidates, key=lambda path: path.stat().st_mtime, reverse=True))


def find_latest_level_save(root: Path | None = None) -> Path | None:
    """Return the most recently modified normal world save, if present."""

    saves = find_level_saves(root or default_save_games_dir())
    return saves[0] if saves else None


__all__ = [
    "default_save_games_dir",
    "find_latest_level_save",
    "find_level_saves",
]
