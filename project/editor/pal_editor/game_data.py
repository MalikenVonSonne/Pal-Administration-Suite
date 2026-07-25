"""Game-data catalog loading with explicit provenance."""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_DATA_DIR = Path.home() / "Documents" / "PalAdmin" / "data"


def _humanize_identifier(value: str) -> str:
    """Turn an untranslated internal identifier into readable fallback text."""

    text = value.split("::")[-1]
    text = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", text)
    text = text.replace("_", " ")
    text = re.sub(r"\s+", " ", text).strip()
    replacements = (
        ("Base Camp Pal SAN Down", "Base Camp Morale Down"),
        ("Base Camp Pal SAN Up", "Base Camp Morale Up"),
        (" SAN Down", " Morale Down"),
        (" SAN Up", " Morale Up"),
        ("Unique ", "Partner "),
        ("Collect Item Wool", "Wool Maker"),
        ("Collect Item Berries", "Berry Gatherer"),
    )
    for old, new in replacements:
        if text.startswith(old) or old in text:
            text = text.replace(old, new, 1)
    return text or value


def _friendly_name(value: dict[str, Any], internal_name: str) -> str:
    name = str(value.get("Name") or "").strip()
    if name and name.casefold() != internal_name.casefold():
        return name
    return _humanize_identifier(internal_name)


def _project_root() -> Path:
    """Resolve source or bundled data without depending on the developer's PC."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS"))
    current = Path(__file__).resolve()
    for parent in (current.parent, *current.parents):
        if (parent / "data" / "catalogs" / "palworld-1.0-db.json").exists():
            return parent
    return current.parents[3]


CATALOG_DIR = _project_root() / "data" / "catalogs"
PALWORLD_1_0_DB_PATH = CATALOG_DIR / "palworld-1.0-db.json"
PALCALC_DB_PATH = CATALOG_DIR / "palcalc-db.json"


@dataclass(frozen=True)
class CatalogEntry:
    code: str
    label: str
    description: str = ""
    metadata: dict[str, Any] | None = None


@dataclass(frozen=True)
class GameDataCatalog:
    pals: tuple[CatalogEntry, ...]
    attacks: tuple[CatalogEntry, ...]
    passives: tuple[CatalogEntry, ...]
    source_dir: str
    source_kind: str
    warnings: tuple[str, ...] = ()

    @property
    def source_label(self) -> str:
        return f"{self.source_kind}: {self.source_dir}"

    @property
    def standard_passives(self) -> tuple[CatalogEntry, ...]:
        """Return actual player-facing passive traits when metadata supports it."""

        standard = tuple(
            entry
            for entry in self.passives
            if isinstance(entry.metadata, dict) and entry.metadata.get("IsStandardPassiveSkill")
        )
        return standard or self.passives

    @classmethod
    def load(cls, data_dir: Path | None = None, locale: str = "en-GB") -> "GameDataCatalog":
        if data_dir is None:
            if PALWORLD_1_0_DB_PATH.exists():
                return cls._load_palcalc(
                    PALWORLD_1_0_DB_PATH,
                    source_kind="Palworld 1.0 Pak-generated catalog",
                )
            if getattr(sys, "frozen", False):
                raise FileNotFoundError(
                    "The bundled Palworld 1.0 catalog is missing or incomplete. "
                    "Re-extract the complete PalAdmin folder and try again."
                )
            if PALCALC_DB_PATH.exists():
                return cls._load_palcalc(PALCALC_DB_PATH)
        directory = data_dir or Path(os.environ.get("PAL_EDITOR_DATA_DIR", DEFAULT_DATA_DIR))
        warnings: list[str] = []

        def read(relative: str, default: Any) -> Any:
            path = directory / relative
            try:
                with path.open("r", encoding="utf-8") as handle:
                    return json.load(handle)
            except (FileNotFoundError, json.JSONDecodeError) as exc:
                warnings.append(f"Could not load {path}: {exc}")
                return default

        localized_pals = read(f"{locale}/pals.json", {})
        pal_data = read("pals.json", {})
        pal_values = pal_data.get("values", []) if isinstance(pal_data, dict) else []
        pal_entries = []
        seen: set[str] = set()
        for value in pal_values:
            if not isinstance(value, dict) or not value.get("CodeName"):
                continue
            code = str(value["CodeName"])
            if code in seen:
                continue
            seen.add(code)
            pal_entries.append(
                CatalogEntry(
                    code=code,
                    label=str(localized_pals.get(code, code)),
                    metadata=value,
                )
            )

        localized_attacks = read(f"{locale}/attacks.json", {})
        attack_data = read("attacks.json", {})
        attack_entries = tuple(
            CatalogEntry(
                code=str(code),
                label=str(localized_attacks.get(code) or code),
                metadata=value if isinstance(value, dict) else None,
            )
            for code, value in attack_data.items()
            if code
        )

        localized_passives = read(f"{locale}/passives.json", {})
        passive_entries = tuple(
            CatalogEntry(
                code=str(code),
                label=str(value.get("Name", code)) if isinstance(value, dict) else str(code),
                description=str(value.get("Description", "")) if isinstance(value, dict) else "",
                metadata=value if isinstance(value, dict) else None,
            )
            for code, value in localized_passives.items()
            if code
        )

        return cls(
            pals=tuple(sorted(pal_entries, key=lambda entry: entry.label.casefold())),
            attacks=tuple(sorted(attack_entries, key=lambda entry: entry.label.casefold())),
            passives=tuple(sorted(passive_entries, key=lambda entry: entry.label.casefold())),
            source_dir=str(directory),
            source_kind="local reference catalog (provisional)",
            warnings=tuple(warnings),
        )

    @classmethod
    def _load_palcalc(
        cls,
        path: Path,
        source_kind: str = "PalCalc generated reference (provisional)",
    ) -> "GameDataCatalog":
        with path.open("r", encoding="utf-8") as handle:
            database = json.load(handle)

        pals = tuple(
            sorted(
                (
                    CatalogEntry(
                        code=str(value["InternalName"]),
                        label=str(value.get("Name") or value["InternalName"]),
                        metadata=value,
                    )
                    for value in database.get("Pals", [])
                    if isinstance(value, dict) and value.get("InternalName")
                ),
                key=lambda entry: entry.label.casefold(),
            )
        )
        attacks = tuple(
            sorted(
                (
                    CatalogEntry(
                        code=f"EPalWazaID::{value['InternalName']}",
                        label=_friendly_name(value, str(value["InternalName"])),
                        description=cls._active_skill_description(value),
                        metadata=value,
                    )
                    for value in database.get("ActiveSkills", [])
                    if isinstance(value, dict) and value.get("InternalName")
                ),
                key=lambda entry: entry.label.casefold(),
            )
        )
        passives = tuple(
            sorted(
                (
                    CatalogEntry(
                        code=str(value["InternalName"]),
                        label=_friendly_name(value, str(value["InternalName"])),
                        description=str(value.get("Description") or ""),
                        metadata=value,
                    )
                    for value in database.get("PassiveSkills", [])
                    if isinstance(value, dict) and value.get("InternalName")
                ),
                key=lambda entry: entry.label.casefold(),
            )
        )
        version = database.get("Version", "unknown")
        return cls(
            pals=pals,
            attacks=attacks,
            passives=passives,
            source_dir=str(path),
            source_kind=source_kind,
            warnings=(
                ()
                if source_kind == "Palworld 1.0 Pak-generated catalog"
                else (f"PalCalc DB version {version}; verify against installed Pak build before authorizing edits.",)
            ),
        )

    @staticmethod
    def _active_skill_description(value: dict[str, Any]) -> str:
        """Build concise tooltip text from the 1.0 active-skill metadata."""
        parts: list[str] = []
        if value.get("ElementInternalName"):
            parts.append(f"Element: {value['ElementInternalName']}")
        if value.get("Power") is not None:
            parts.append(f"Power: {value['Power']}")
        if value.get("CooldownSeconds") is not None:
            parts.append(f"Cooldown: {value['CooldownSeconds']}s")
        if value.get("CanInherit") is not None:
            parts.append("Inheritable" if value["CanInherit"] else "Not inheritable")
        if value.get("HasSkillFruit"):
            parts.append("Skill fruit available")
        return " · ".join(parts)

    def entry(self, entries: tuple[CatalogEntry, ...], code: str | None) -> CatalogEntry | None:
        return next((entry for entry in entries if entry.code == code), None)
