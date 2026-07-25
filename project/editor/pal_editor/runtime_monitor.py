"""Read-only bridge model for the live Pal Admin runtime snapshot."""

from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


SNAPSHOT_SCHEMA = "paladmin-runtime-snapshot"
SNAPSHOT_VERSION = 1


def default_snapshot_path() -> Path:
    """Return the shared local bridge path used by the UE4SS runtime mod."""

    root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    return root / "PalAdmin" / "runtime_snapshot.json"


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def _optional_text(value: Any) -> str | None:
    text = _text(value).strip()
    return text or None


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _text_tuple(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(_text(item) for item in value if item is not None)


def canonical_identity_key(value: Any) -> str:
    """Normalize UE4SS signed FGuid components and save UUID text alike.

    The runtime bridge serializes FGuid as four signed 32-bit decimal
    components, while the save parser exposes the same bytes as a UUID.  A
    canonical form lets the Live Roster match live actors to the loaded save.
    Unknown identity text is retained case-insensitively rather than rejected.
    """

    text = _text(value).strip()
    if not text:
        return ""
    try:
        return str(uuid.UUID(text)).casefold()
    except (ValueError, AttributeError):
        pass

    match = re.fullmatch(r"(-?\d+)-(-?\d+)-(-?\d+)-(-?\d+)", text)
    if match:
        try:
            words = [int(part) & 0xFFFFFFFF for part in match.groups()]
        except ValueError:
            words = []
        if len(words) == 4:
            return str(uuid.UUID(hex="".join(f"{word:08x}" for word in words))).casefold()
    return text.casefold()


@dataclass(frozen=True, slots=True)
class RuntimePalRecord:
    """A UI-safe copy of one live Pal record."""

    identity_key: str
    identity_source: str
    species: str
    nickname: str
    level: int | None
    rank: int | None
    instance_id: str | None
    player_uid: str | None
    owner_player_uid: str | None
    is_player: bool
    debug_name: str | None
    character_class: str | None
    active_skills: tuple[str, ...]
    passive_skills: tuple[str, ...]

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "RuntimePalRecord | None":
        identity_key = _optional_text(data.get("identity_key"))
        if not identity_key:
            return None
        return cls(
            identity_key=identity_key,
            identity_source=_text(data.get("identity_source")),
            species=_text(data.get("character_id")),
            nickname=_text(data.get("nickname")),
            level=_optional_int(data.get("level")),
            rank=_optional_int(data.get("rank")),
            instance_id=_optional_text(data.get("instance_id")),
            player_uid=_optional_text(data.get("player_uid")),
            owner_player_uid=_optional_text(data.get("owner_player_uid")),
            is_player=bool(data.get("is_player")),
            debug_name=_optional_text(data.get("debug_name")),
            character_class=_optional_text(data.get("character_class")),
            active_skills=_text_tuple(data.get("equip_waza")),
            passive_skills=_text_tuple(data.get("passive_skill_list")),
        )


@dataclass(frozen=True, slots=True)
class RuntimeSnapshot:
    """Parsed, immutable snapshot data suitable for a desktop view."""

    path: Path
    schema_version: int
    written_at: int | None
    ok: bool
    error: str | None
    scanned: int
    included: int
    filtered: int
    records: tuple[RuntimePalRecord, ...]
    file_mtime: float

    @property
    def identity_map(self) -> dict[str, RuntimePalRecord]:
        return {record.identity_key: record for record in self.records}

    def find_record(self, identity_key: str | None) -> RuntimePalRecord | None:
        if not identity_key:
            return None
        return self.identity_map.get(identity_key)


class RuntimeSnapshotError(ValueError):
    """The bridge file exists but is not a recognized Pal Admin snapshot."""


def load_runtime_snapshot(path: Path | None = None) -> RuntimeSnapshot | None:
    """Load a bridge snapshot, returning ``None`` when it does not exist."""

    snapshot_path = (path or default_snapshot_path()).expanduser()
    if not snapshot_path.exists():
        return None
    try:
        stat = snapshot_path.stat()
        with snapshot_path.open("r", encoding="utf-8") as handle:
            envelope = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeSnapshotError(f"Could not read runtime snapshot: {exc}") from exc

    if not isinstance(envelope, dict) or envelope.get("schema") != SNAPSHOT_SCHEMA:
        raise RuntimeSnapshotError("Runtime snapshot schema is not recognized")
    if envelope.get("schema_version") != SNAPSHOT_VERSION:
        raise RuntimeSnapshotError(
            f"Unsupported runtime snapshot version: {envelope.get('schema_version')!r}"
        )
    snapshot_data = envelope.get("snapshot")
    if not isinstance(snapshot_data, dict):
        raise RuntimeSnapshotError("Runtime snapshot payload is missing")

    records: list[RuntimePalRecord] = []
    for raw_record in snapshot_data.get("records", []):
        if isinstance(raw_record, dict):
            record = RuntimePalRecord.from_mapping(raw_record)
            if record is not None:
                records.append(record)

    return RuntimeSnapshot(
        path=snapshot_path,
        schema_version=SNAPSHOT_VERSION,
        written_at=_optional_int(envelope.get("written_at")),
        ok=bool(snapshot_data.get("ok")),
        error=_optional_text(snapshot_data.get("error")),
        scanned=_optional_int(snapshot_data.get("scanned")) or 0,
        included=_optional_int(snapshot_data.get("included")) or 0,
        filtered=_optional_int(snapshot_data.get("filtered")) or 0,
        records=tuple(records),
        file_mtime=stat.st_mtime,
    )


__all__ = [
    "RuntimePalRecord",
    "RuntimeSnapshot",
    "RuntimeSnapshotError",
    "SNAPSHOT_SCHEMA",
    "SNAPSHOT_VERSION",
    "canonical_identity_key",
    "default_snapshot_path",
    "load_runtime_snapshot",
]
