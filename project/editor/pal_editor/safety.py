"""Safety checks for offline save editing."""

from __future__ import annotations

import csv
import subprocess
import sys
from dataclasses import dataclass
from typing import Iterable


GAME_PROCESS_NAMES = (
    "Palworld-Win64-Shipping.exe",
    "Palworld.exe",
    "PalServer-Win64-Shipping.exe",
    "PalServer-Win64-Test-Cmd.exe",
)


@dataclass(frozen=True)
class GameSafetyStatus:
    """Whether a known Palworld process is currently running."""

    running_processes: tuple[str, ...] = ()

    @property
    def game_open(self) -> bool:
        return bool(self.running_processes)

    @property
    def safe_for_offline_editing(self) -> bool:
        return not self.game_open


def running_process_names(
    process_names: Iterable[str] = GAME_PROCESS_NAMES,
) -> tuple[str, ...]:
    """Return matching process names using Windows' built-in task list."""

    if sys.platform != "win32":
        return ()
    wanted = {str(name).casefold() for name in process_names}
    try:
        result = subprocess.run(
            ["tasklist", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except OSError:
        return ()

    matches: list[str] = []
    for row in csv.reader(result.stdout.splitlines()):
        if row and row[0].casefold() in wanted and row[0] not in matches:
            matches.append(row[0])
    return tuple(matches)


def get_game_safety_status() -> GameSafetyStatus:
    return GameSafetyStatus(running_process_names())


__all__ = [
    "GAME_PROCESS_NAMES",
    "GameSafetyStatus",
    "get_game_safety_status",
    "running_process_names",
]
