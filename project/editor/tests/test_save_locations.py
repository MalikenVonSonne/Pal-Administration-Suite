from __future__ import annotations

from pathlib import Path

from pal_editor.save_locations import default_save_games_dir, find_latest_level_save, find_level_saves
from pal_editor.__main__ import _character


def test_default_save_directory_uses_localappdata(monkeypatch) -> None:
    monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\Test\AppData\Local")

    assert default_save_games_dir() == Path(
        r"C:\Users\Test\AppData\Local\Pal\Saved\SaveGames"
    )


def test_latest_save_ignores_backup_worlds(tmp_path: Path) -> None:
    normal = tmp_path / "765" / "world" / "Level.sav"
    backup = tmp_path / "765" / "world" / "backup" / "world" / "2026" / "Level.sav"
    normal.parent.mkdir(parents=True)
    backup.parent.mkdir(parents=True)
    normal.write_bytes(b"normal")
    backup.write_bytes(b"backup")

    assert find_level_saves(tmp_path) == (normal,)
    assert find_latest_level_save(tmp_path) == normal


def test_missing_pal_level_uses_unreal_default_level_one() -> None:
    entry = {
        "key": {"InstanceId": "pal-1", "PlayerUId": "player-1"},
        "value": {
            "RawData": {
                "value": {
                    "object": {
                        "SaveParameter": {
                            "value": {
                                "CharacterID": {"value": "Sheepball"},
                                "Exp": {"value": 13},
                            }
                        }
                    }
                }
            }
        },
    }

    assert _character(entry)["level"] == 1
