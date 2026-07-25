from __future__ import annotations

import json
from pathlib import Path

import pytest

from pal_editor.runtime_monitor import (
    RuntimeSnapshotError,
    canonical_identity_key,
    load_runtime_snapshot,
)


def _write_snapshot(path: Path, records: list[dict]) -> None:
    path.write_text(
        json.dumps(
            {
                "schema": "paladmin-runtime-snapshot",
                "schema_version": 1,
                "written_at": 123,
                "snapshot": {
                    "ok": True,
                    "scanned": len(records),
                    "included": len(records),
                    "filtered": 0,
                    "records": records,
                },
            }
        ),
        encoding="utf-8",
    )


def test_runtime_snapshot_loads_identity_backed_records(tmp_path: Path) -> None:
    path = tmp_path / "runtime_snapshot.json"
    _write_snapshot(
        path,
        [
            {
                "identity_key": "pal-alpha",
                "identity_source": "handle.instance_id",
                "character_id": "PinkCat",
                "level": 12,
                "owner_player_uid": "owner-alpha",
                "player_uid": "player-alpha",
                "equip_waza": ["PAL_Waza_Alpha"],
                "passive_skill_list": ["PAL_Passive_Lucky"],
            }
        ],
    )

    snapshot = load_runtime_snapshot(path)

    assert snapshot is not None
    assert snapshot.included == 1
    assert snapshot.find_record("pal-alpha").species == "PinkCat"
    assert snapshot.find_record("pal-alpha").owner_player_uid == "owner-alpha"
    assert snapshot.find_record("missing") is None


def test_runtime_snapshot_ignores_records_without_canonical_identity(tmp_path: Path) -> None:
    path = tmp_path / "runtime_snapshot.json"
    _write_snapshot(path, [{"character_id": "PinkCat"}])

    snapshot = load_runtime_snapshot(path)

    assert snapshot is not None
    assert snapshot.records == ()


def test_runtime_snapshot_rejects_unknown_schema(tmp_path: Path) -> None:
    path = tmp_path / "runtime_snapshot.json"
    path.write_text(json.dumps({"schema": "other", "schema_version": 1}), encoding="utf-8")

    with pytest.raises(RuntimeSnapshotError):
        load_runtime_snapshot(path)


def test_runtime_identity_matches_save_uuid() -> None:
    assert canonical_identity_key(
        "-212225381-1203757064--1267538811-163816218"
    ) == "f359b29b-47bf-e008-b472-e48509c3a31a"


def test_runtime_record_preserves_player_marker(tmp_path: Path) -> None:
    path = tmp_path / "runtime_snapshot.json"
    _write_snapshot(
        path,
        [{
            "identity_key": "player",
            "character_id": "Female_Soldier01",
            "is_player": True,
        }],
    )

    snapshot = load_runtime_snapshot(path)

    assert snapshot is not None
    assert snapshot.records[0].is_player is True
