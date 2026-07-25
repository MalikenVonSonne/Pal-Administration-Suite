"""Shared Pal data structures for the offline editor and runtime mod."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


def _text(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


@dataclass
class PalTemplate:
    """Portable Pal definition without live UUID, owner, or slot data."""

    species: str
    nickname: str = ""
    gender: str | None = None
    level: int | None = None
    xp: int | None = None
    hp: int | None = None
    fullness: float | None = None
    iv_hp: int | None = None
    iv_attack: int | None = None
    iv_defense: int | None = None
    active_skills: list[str] = field(default_factory=list)
    passive_skills: list[str] = field(default_factory=list)
    rank: int | None = None
    appearance_flags: list[str] = field(default_factory=list)
    souls: dict[str, int] = field(default_factory=dict)
    work_suitability: dict[str, int] = field(default_factory=dict)
    source_build: str | None = None
    validation_mode: str = "legal"

    @classmethod
    def from_record(cls, record: dict[str, Any], *, source_build: str | None = None) -> "PalTemplate":
        """Create a portable template from the inspector's normalized record."""
        return cls(
            species=str(record.get("species") or ""),
            nickname=str(record.get("nickname") or ""),
            gender=_text(record.get("gender")),
            level=record.get("level"),
            xp=record.get("xp"),
            hp=record.get("hp"),
            fullness=record.get("fullness"),
            iv_hp=record.get("iv_hp"),
            iv_attack=record.get("iv_attack"),
            iv_defense=record.get("iv_defense"),
            active_skills=[str(value) for value in record.get("active_skills", [])],
            passive_skills=[str(value) for value in record.get("passives", [])],
            rank=record.get("rank"),
            appearance_flags=[str(value) for value in record.get("appearance_flags", [])],
            souls={str(key): int(value) for key, value in (record.get("souls") or {}).items()},
            work_suitability={
                str(key): int(value)
                for key, value in (record.get("work_suitability") or {}).items()
            },
            source_build=source_build,
        )

    def to_dict(self) -> dict[str, Any]:
        """Return the portable JSON-compatible form."""
        return asdict(self)


@dataclass
class PalInstance:
    """A Pal template plus the location/ownership needed for save editing."""

    template: PalTemplate
    instance_id: str | None = None
    owner_uid: str | None = None
    player_uid: str | None = None
    container_id: str | None = None
    slot_index: int | None = None
    source_build: str | None = None
    raw_property_names: list[str] = field(default_factory=list)

    @classmethod
    def from_record(cls, record: dict[str, Any], *, source_build: str | None = None) -> "PalInstance":
        slot = record.get("slot") or {}
        return cls(
            template=PalTemplate.from_record(record, source_build=source_build),
            instance_id=_text(record.get("instance_id")),
            owner_uid=_text(record.get("owner_uid")),
            player_uid=_text(record.get("player_uid")),
            container_id=_text(slot.get("container_id")),
            slot_index=slot.get("slot_index"),
            source_build=source_build,
            raw_property_names=list(record.get("raw_property_names", [])),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "template": self.template.to_dict(),
            "instance_id": self.instance_id,
            "owner_uid": self.owner_uid,
            "player_uid": self.player_uid,
            "container_id": self.container_id,
            "slot_index": self.slot_index,
            "source_build": self.source_build,
            "raw_property_names": list(self.raw_property_names),
        }
