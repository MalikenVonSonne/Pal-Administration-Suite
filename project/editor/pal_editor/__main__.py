"""Read-only Palworld save inspector used as the first editor prototype."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from palsav.io import load_sav


def _unwrap(value: Any) -> Any:
    """Unwrap the common property/value wrappers emitted by palsav."""
    if isinstance(value, dict):
        if set(value) == {"Value"}:
            return _unwrap(value["Value"])
        if "value" in value and set(value).issubset(
            {"id", "value", "type", "struct_type", "struct_id", "array_type"}
        ):
            return _unwrap(value["value"])
        if value.get("type") in {"EnumProperty", "NameProperty"}:
            return value.get("value")
    return value


def _prop(properties: dict[str, Any], name: str, default: Any = None) -> Any:
    if name not in properties:
        return default
    return _unwrap(properties[name])


def _array(properties: dict[str, Any], name: str) -> list[Any]:
    value = properties.get(name)
    if not isinstance(value, dict):
        return []
    value = value.get("value", {})
    if isinstance(value, dict) and "values" in value:
        return [_unwrap(item) for item in value["values"]]
    return value if isinstance(value, list) else []


def _slot(properties: dict[str, Any]) -> dict[str, Any] | None:
    slot = properties.get("SlotId")
    if not isinstance(slot, dict):
        return None
    value = slot.get("value", {})
    if not isinstance(value, dict):
        return None
    container = value.get("ContainerId", {}).get("value", {})
    container_id = container.get("ID", {}).get("value") if isinstance(container, dict) else None
    return {"container_id": container_id, "slot_index": _unwrap(value.get("SlotIndex"))}


def _character(entry: dict[str, Any]) -> dict[str, Any] | None:
    key = entry.get("key", {})
    raw = entry.get("value", {}).get("RawData", {})
    obj = raw.get("value", {}).get("object", {}) if isinstance(raw, dict) else {}
    save_parameter = obj.get("SaveParameter", {})
    properties = save_parameter.get("value", {}) if isinstance(save_parameter, dict) else {}
    if not isinstance(properties, dict):
        return None

    species = _prop(properties, "CharacterID")
    player_uid = _prop(key, "PlayerUId")
    is_player = _prop(properties, "IsPlayer", False)
    if not species and not is_player:
        return None

    level = _prop(properties, "Level")
    # Unreal save data commonly omits default-valued byte properties.  A Pal
    # record with no serialized Level therefore starts at the game default,
    # level 1, rather than having an unknown level.
    if level is None and species:
        level = 1

    record: dict[str, Any] = {
        "instance_id": _prop(key, "InstanceId"),
        "player_uid": player_uid,
        "species": species,
        "nickname": _prop(properties, "NickName", ""),
        "level": level,
        "rank": _prop(properties, "Rank"),
        "xp": _prop(properties, "Exp"),
        "gender": _prop(properties, "Gender"),
        "hp": _prop(properties, "Hp"),
        "iv_hp": _prop(properties, "Talent_HP"),
        "iv_attack": _prop(properties, "Talent_Shot"),
        "iv_defense": _prop(properties, "Talent_Defense"),
        "passives": _array(properties, "PassiveSkillList"),
        "active_skills": _array(properties, "EquipWaza"),
        "owner_uid": _prop(properties, "OwnerPlayerUId"),
        "slot": _slot(properties),
        "is_player": bool(is_player),
        "raw_property_names": sorted(properties),
    }
    return record


def inspect(path: Path) -> dict[str, Any]:
    save = load_sav(path).dump()
    world = save["properties"]["worldSaveData"]["value"]
    entries = world["CharacterSaveParameterMap"]["value"]
    characters = [record for entry in entries if (record := _character(entry))]
    pals = [record for record in characters if record["species"] and not record["is_player"]]
    player_characters = [record for record in characters if record["is_player"]]
    owned_pals = [record for record in pals if record["owner_uid"]]

    return {
        "path": str(path),
        "save_class": save["header"].get("save_game_class_name"),
        "engine_version": ".".join(
            str(save["header"].get(key, "?"))
            for key in ("engine_version_major", "engine_version_minor", "engine_version_patch")
        ),
        "character_entries": len(entries),
        "player_characters": player_characters,
        "pals": pals,
        "owned_pals": owned_pals,
        "counts": {
            "characters": len(characters),
            "pals": len(pals),
            "owned_pals": len(owned_pals),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect a Palworld Level.sav without modifying it")
    parser.add_argument("save", type=Path)
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    parser.add_argument("--player-uid", help="only show owned Pals for this player")
    args = parser.parse_args()

    report = inspect(args.save)
    if args.player_uid:
        report["owned_pals"] = [
            pal for pal in report["owned_pals"] if pal["player_uid"] == args.player_uid
        ]
    if args.json:
        json.dump(report, sys.stdout, ensure_ascii=False, indent=2, default=str)
        print()
        return

    print(f"Save: {report['path']}")
    print(f"Class: {report['save_class']} | Engine: {report['engine_version']}")
    print(f"Characters: {report['counts']['characters']} | Pals: {report['counts']['pals']} | Owned: {len(report['owned_pals'])}")
    print("\nOwned Pals:")
    for index, pal in enumerate(report["owned_pals"], 1):
        print(
            f"{index:>2}. {pal['species']:<24} "
            f"Lv {str(pal['level']):<3} "
            f"{pal['gender'] or '':<28} "
            f"IV {pal['iv_hp']}/{pal['iv_attack']}/{pal['iv_defense']}"
        )


if __name__ == "__main__":
    main()
