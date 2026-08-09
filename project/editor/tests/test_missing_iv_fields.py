import copy
import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from pal_editor.domain import PalInstance, PalTemplate
from pal_editor.gui import PalEditorWindow
from pal_editor.ledger import OperationLedger
from pal_editor.operations import BatchEdit, SaveEditError, apply_template, apply_templates


APP = QApplication.instance() or QApplication([])


def _properties(document: dict) -> dict:
    return (
        document["properties"]["worldSaveData"]["value"]
        ["CharacterSaveParameterMap"]["value"][0]["value"]["RawData"]["value"]
        ["object"]["SaveParameter"]["value"]
    )


def _entry(identity: str, *, missing: tuple[str, ...] = ()) -> dict:
    properties = {
        "CharacterID": {"value": "PinkCat"},
        "Level": {"value": {"type": "None", "value": 2}},
        "Talent_HP": {"value": {"type": "None", "value": 0}},
        "Talent_Shot": {"value": {"type": "None", "value": 0}},
        "Talent_Defense": {"value": {"type": "None", "value": 0}},
    }
    for field_name in missing:
        properties.pop(field_name)
    return {
        "key": {"InstanceId": {"value": identity}},
        "value": {
            "RawData": {
                "value": {
                    "object": {"SaveParameter": {"value": properties}}
                }
            }
        },
    }


def _document(*, missing: tuple[str, ...] = ()) -> dict:
    return {
        "properties": {
            "worldSaveData": {
                "value": {"CharacterSaveParameterMap": {"value": [_entry("pal-1", missing=missing)]}}
            }
        }
    }


def test_unrelated_edit_preserves_absent_optional_ivs() -> None:
    document = _document(missing=("Talent_HP", "Talent_Shot", "Talent_Defense"))

    result = apply_template(
        document,
        "pal-1",
        PalTemplate(species="PinkCat", nickname="Edited"),
    )

    assert result.changed_fields == ("NickName",)
    properties = _properties(document)
    assert "Talent_HP" not in properties
    assert "Talent_Shot" not in properties
    assert "Talent_Defense" not in properties


def test_present_zero_ivs_remain_existing_values() -> None:
    document = _document()

    result = apply_template(
        document,
        "pal-1",
        PalTemplate(species="PinkCat", iv_hp=0, iv_attack=0, iv_defense=0),
    )

    assert result.changed_fields == ()
    properties = _properties(document)
    assert all(field_name in properties for field_name in (
        "Talent_HP",
        "Talent_Shot",
        "Talent_Defense",
    ))
    assert [properties[field_name]["value"]["value"] for field_name in (
        "Talent_HP",
        "Talent_Shot",
        "Talent_Defense",
    )] == [0, 0, 0]


@pytest.mark.parametrize("field_name, template_field, label", [
    ("Talent_HP", "iv_hp", "HP IV"),
    ("Talent_Shot", "iv_attack", "Attack IV"),
    ("Talent_Defense", "iv_defense", "Defense IV"),
])
def test_explicit_edit_of_absent_iv_is_rejected_without_mutation(
    field_name: str,
    template_field: str,
    label: str,
) -> None:
    document = _document(missing=(field_name,))
    before = copy.deepcopy(document)
    template = PalTemplate(species="PinkCat", **{template_field: 25})

    with pytest.raises(SaveEditError, match=rf"{label}.*{field_name}"):
        apply_template(document, "pal-1", template)

    assert document == before


def test_multi_pal_unrelated_edits_do_not_fail_for_missing_iv() -> None:
    document = {
        "properties": {
            "worldSaveData": {
                "value": {
                    "CharacterSaveParameterMap": {
                        "value": [
                            _entry("pal-1", missing=("Talent_Shot",)),
                            _entry("pal-2"),
                        ]
                    }
                }
            }
        }
    }

    results = apply_templates(
        document,
        (
            BatchEdit("pal-1", PalTemplate(species="PinkCat", nickname="First")),
            BatchEdit("pal-2", PalTemplate(species="PinkCat", nickname="Second")),
        ),
    )

    assert [result.changed_fields for result in results] == [("NickName",), ("NickName",)]
    entries = document["properties"]["worldSaveData"]["value"]["CharacterSaveParameterMap"]["value"]
    first_properties = entries[0]["value"]["RawData"]["value"]["object"]["SaveParameter"]["value"]
    second_properties = entries[1]["value"]["RawData"]["value"]["object"]["SaveParameter"]["value"]
    assert "Talent_Shot" not in first_properties
    assert second_properties["Talent_Shot"]["value"]["value"] == 0


def _window_with_missing_attack_iv() -> PalEditorWindow:
    window = PalEditorWindow()
    window.instances = [
        PalInstance(
            template=PalTemplate(
                species="PinkCat",
                nickname="Original",
                level=2,
                iv_hp=0,
                iv_attack=None,
                iv_defense=0,
            ),
            instance_id="pal-1",
            raw_property_names=["CharacterID", "Level", "Talent_HP", "Talent_Defense"],
        )
    ]
    window.ledger = OperationLedger(Path("missing-iv-source.sav"))
    window._refresh_roster_list(preferred_index=0)
    APP.processEvents()
    return window


def test_gui_does_not_turn_absent_iv_into_implicit_zero_write() -> None:
    window = _window_with_missing_attack_iv()

    assert window.form_template().iv_attack is None
    window.nickname_edit.setText("Edited")
    APP.processEvents()

    entry = window.ledger.draft_for("pal-1")
    assert entry is not None
    assert "iv_attack" not in entry.after_fields
    edits = window._pending_batch()
    assert edits[0].template.iv_attack is None

    window.ledger.clear_drafts()
    window.close()


def test_gui_explicit_absent_iv_edit_is_rejected_at_save_boundary() -> None:
    window = _window_with_missing_attack_iv()
    window.iv_attack_spin.setValue(25)
    APP.processEvents()

    with pytest.raises(SaveEditError, match="Attack IV.*Talent_Shot"):
        window._pending_batch()

    window.ledger.clear_drafts()
    window.close()
