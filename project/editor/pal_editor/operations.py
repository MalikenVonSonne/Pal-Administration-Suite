"""Safe, copy-out save editing primitives."""

from __future__ import annotations

import copy
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from palsav.gvas import GvasFile
from palsav.io import load_sav, save_sav

from .domain import PalTemplate
from .validation import validate_template


class SaveEditError(RuntimeError):
    """Raised when a save edit would be ambiguous or unsafe."""


@dataclass(frozen=True)
class EditResult:
    instance_id: str
    changed_fields: tuple[str, ...]
    output_path: str | None = None
    backup_path: str | None = None


@dataclass(frozen=True)
class BatchEdit:
    """One identity-keyed edit in a multi-Pal operation."""

    instance_id: str
    template: PalTemplate
    display_context: str = ""


@dataclass(frozen=True)
class BatchEditResult:
    """Results for one parsed and serialized batch."""

    results: tuple[EditResult, ...]
    output_path: str | None = None
    backup_path: str | None = None

    @property
    def changed_fields(self) -> tuple[str, ...]:
        names: list[str] = []
        for result in self.results:
            for name in result.changed_fields:
                if name not in names:
                    names.append(name)
        return tuple(names)


def _entry_for_instance(document: dict[str, Any], instance_id: str) -> dict[str, Any]:
    world = document["properties"]["worldSaveData"]["value"]
    entries = world["CharacterSaveParameterMap"]["value"]
    for entry in entries:
        key = entry.get("key", {})
        actual = key.get("InstanceId", {}).get("value")
        if str(actual) == str(instance_id):
            return entry
    raise SaveEditError(f"Pal instance was not found: {instance_id}")


def _properties_for_entry(entry: dict[str, Any]) -> dict[str, Any]:
    raw = entry.get("value", {}).get("RawData", {})
    obj = raw.get("value", {}).get("object", {})
    save_parameter = obj.get("SaveParameter", {})
    properties = save_parameter.get("value", {})
    if not isinstance(properties, dict):
        raise SaveEditError("Pal entry does not contain editable SaveParameter data")
    return properties


def _set_scalar(properties: dict[str, Any], name: str, value: Any, changed: list[str]) -> None:
    if name not in properties:
        # Palworld omits default-valued Level properties from some untouched
        # records.  Once a user edits that Pal, materialize the same ByteProperty
        # shape used by neighboring records instead of treating it as corrupt.
        if name == "Level":
            properties[name] = {
                "id": None,
                "value": {"type": "None", "value": value},
                "type": "ByteProperty",
            }
            changed.append(name)
            return
        raise SaveEditError(f"Field is not present in this Pal record: {name}")
    prop = properties[name]
    current = prop.get("value")
    if isinstance(current, dict) and "Value" in current:
        if current["Value"].get("value") == value:
            return
        current["Value"]["value"] = value
    elif isinstance(current, dict) and "value" in current and "type" in current:
        if current.get("value") == value:
            return
        current["value"] = value
    else:
        if current == value:
            return
        prop["value"] = value
    changed.append(name)


def _set_array(properties: dict[str, Any], name: str, values: list[str], changed: list[str]) -> None:
    if name not in properties:
        raise SaveEditError(f"Field is not present in this Pal record: {name}")
    prop = properties[name]
    current = prop.get("value")
    if not isinstance(current, dict) or "values" not in current:
        raise SaveEditError(f"Field is not an editable array: {name}")
    if current["values"] == values:
        return
    current["values"] = list(values)
    changed.append(name)


def _set_nickname(properties: dict[str, Any], value: str, changed: list[str]) -> None:
    """NickName is often omitted from untouched Pal records; add it only when used."""
    if "NickName" not in properties:
        if value:
            properties["NickName"] = {"id": None, "value": value, "type": "StrProperty"}
            changed.append("NickName")
        return
    _set_scalar(properties, "NickName", value, changed)


def _apply_template_unvalidated(
    document: dict[str, Any],
    instance_id: str,
    template: PalTemplate,
) -> EditResult:
    """Apply one already-validated template without reparsing the document."""
    entry = _entry_for_instance(document, instance_id)
    properties = _properties_for_entry(entry)
    changed: list[str] = []

    if template.species:
        _set_scalar(properties, "CharacterID", template.species, changed)
    if template.nickname or "NickName" in properties:
        _set_nickname(properties, template.nickname, changed)
    if template.gender is not None:
        _set_scalar(properties, "Gender", template.gender, changed)
    if template.level is not None:
        _set_scalar(properties, "Level", template.level, changed)
    if template.rank is not None:
        _set_scalar(properties, "Rank", template.rank, changed)
    if template.xp is not None:
        _set_scalar(properties, "Exp", template.xp, changed)
    if template.hp is not None:
        _set_scalar(properties, "Hp", template.hp, changed)
    if template.iv_hp is not None:
        _set_scalar(properties, "Talent_HP", template.iv_hp, changed)
    if template.iv_attack is not None:
        _set_scalar(properties, "Talent_Shot", template.iv_attack, changed)
    if template.iv_defense is not None:
        _set_scalar(properties, "Talent_Defense", template.iv_defense, changed)
    if template.active_skills or "EquipWaza" in properties:
        _set_array(properties, "EquipWaza", template.active_skills, changed)
    if template.passive_skills or "PassiveSkillList" in properties:
        _set_array(properties, "PassiveSkillList", template.passive_skills, changed)

    return EditResult(instance_id=str(instance_id), changed_fields=tuple(dict.fromkeys(changed)))


def apply_templates(
    document: dict[str, Any],
    edits: tuple[BatchEdit, ...] | list[BatchEdit],
) -> tuple[EditResult, ...]:
    """Apply a complete identity-keyed batch to one parsed document."""

    normalized = tuple(edits)
    identities = [str(edit.instance_id).strip() for edit in normalized]
    if any(not identity for identity in identities):
        raise SaveEditError("Every batch edit requires a stable instance identity")
    if len(set(identities)) != len(identities):
        raise SaveEditError("A batch cannot contain duplicate Pal identities")
    for edit in normalized:
        report = validate_template(edit.template, mode=edit.template.validation_mode)  # type: ignore[arg-type]
        if not report.valid:
            context = edit.display_context or edit.instance_id
            messages = "; ".join(issue.message for issue in report.errors)
            raise SaveEditError(f"{context}: template failed validation: {messages}")
        _entry_for_instance(document, edit.instance_id)
    return tuple(
        _apply_template_unvalidated(document, edit.instance_id, edit.template)
        for edit in normalized
    )


def apply_template(document: dict[str, Any], instance_id: str, template: PalTemplate) -> EditResult:
    """Backward-compatible one-Pal wrapper around :func:`apply_templates`."""

    return apply_templates(document, (BatchEdit(str(instance_id), template),))[0]


def edit_save_copy_batch(
    source_path: Path,
    output_path: Path,
    edits: tuple[BatchEdit, ...] | list[BatchEdit],
    *,
    backup_path: Path | None = None,
) -> BatchEditResult:
    """Edit all pending Pals into one separate output file; never overwrite source."""

    source = source_path.resolve()
    output = output_path.resolve()
    if source == output:
        raise SaveEditError("Source and output must be different files")
    backup = backup_path.resolve() if backup_path is not None else None
    if backup == source:
        raise SaveEditError("Backup must be a separate file from the source")

    document = copy.deepcopy(load_sav(str(source)).dump())
    results = apply_templates(document, tuple(edits))
    output.parent.mkdir(parents=True, exist_ok=True)
    if backup is not None:
        backup.parent.mkdir(parents=True, exist_ok=True)
        try:
            with source.open("rb") as source_handle, backup.open("xb") as backup_handle:
                shutil.copyfileobj(source_handle, backup_handle)
        except FileExistsError as exc:
            raise SaveEditError(f"Backup already exists and was not overwritten: {backup}") from exc

    save_sav(GvasFile.load(document), str(output))
    return BatchEditResult(
        results=results,
        output_path=str(output),
        backup_path=str(backup) if backup else None,
    )


def edit_save_copy(
    source_path: Path,
    output_path: Path,
    instance_id: str,
    template: PalTemplate,
    *,
    backup_path: Path | None = None,
) -> EditResult:
    """Backward-compatible one-Pal wrapper around the batch copy path."""

    batch = edit_save_copy_batch(
        source_path,
        output_path,
        (BatchEdit(str(instance_id), template),),
        backup_path=backup_path,
    )
    return batch.results[0]
