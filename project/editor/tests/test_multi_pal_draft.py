from __future__ import annotations

import copy
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox

from pal_editor.direct_save import DirectSaveCoordinator, DirectSaveRequest, validate_edit_batch
from pal_editor.domain import PalInstance, PalTemplate
from pal_editor.gui import PalEditorWindow
from pal_editor.ledger import OperationLedger
from pal_editor.operations import BatchEdit, BatchEditResult, EditResult, apply_templates
from pal_editor.safe_save import fingerprint_file


APP = QApplication.instance() or QApplication([])


def _template(species: str, nickname: str = "") -> PalTemplate:
    return PalTemplate(
        species=species,
        nickname=nickname,
        level=2,
        rank=1,
        iv_hp=10,
        iv_attack=20,
        iv_defense=30,
    )


def _window() -> PalEditorWindow:
    window = PalEditorWindow()
    window.instances = [
        PalInstance(template=_template("PinkCat", "A"), instance_id="pal-a"),
        PalInstance(template=_template("PinkCat", "B"), instance_id="pal-b"),
        PalInstance(template=_template("SheepBall"), instance_id="pal-c"),
    ]
    window.ledger = OperationLedger(Path("multi-source.sav"))
    window._refresh_roster_list(preferred_index=0)
    APP.processEvents()
    return window


def _document() -> dict:
    entries = []
    for identity, species, level in (("pal-a", "PinkCat", 2), ("pal-b", "PinkCat", 3)):
        entries.append(
            {
                "key": {"InstanceId": {"value": identity}},
                "value": {
                    "RawData": {
                        "value": {
                            "object": {
                                "SaveParameter": {
                                    "value": {
                                        "CharacterID": {"value": species},
                                        "Level": {"value": {"type": "None", "value": level}},
                                        "Talent_HP": {"value": {"type": "None", "value": 10}},
                                        "Talent_Shot": {"value": {"type": "None", "value": 20}},
                                        "Talent_Defense": {"value": {"type": "None", "value": 30}},
                                    }
                                }
                            }
                        }
                    }
                },
            }
        )
    return {
        "properties": {
            "worldSaveData": {
                "value": {"CharacterSaveParameterMap": {"value": entries}}
            }
        }
    }


def test_identity_keyed_ledger_keeps_duplicate_species_distinct() -> None:
    ledger = OperationLedger("source.sav")
    ledger.record_pal_draft("pal-a", {"nickname": "A"}, {"nickname": "A1"}, display_context="PinkCat (A)")
    ledger.record_pal_draft("pal-b", {"nickname": "B"}, {"nickname": "B1"}, display_context="PinkCat (B)")

    assert ledger.dirty
    assert ledger.pending_pal_count == 2
    assert ledger.total_changed_field_count == 2
    assert [entry.instance_id for entry in ledger.pending_entries] == ["pal-a", "pal-b"]
    assert ledger.draft_for("pal-a").after_fields == {"nickname": "A1"}

    ledger.record_pal_draft("pal-a", {"nickname": "A"}, {"nickname": "A"})
    assert ledger.pending_pal_count == 1
    assert ledger.draft_for("pal-b") is not None


def test_selection_switch_preserves_and_restores_each_pal_draft(monkeypatch) -> None:
    window = _window()
    monkeypatch.setattr(
        "pal_editor.gui.QMessageBox.question",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("selection must not prompt")),
    )

    window.nickname_edit.setText("A edited")
    APP.processEvents()
    window.pal_list.setCurrentRow(1)
    APP.processEvents()
    assert window.current_index == 1
    assert window.nickname_edit.text() == "B"
    assert window.ledger.pending_pal_count == 1

    window.nickname_edit.setText("B edited")
    APP.processEvents()
    window.pal_list.setCurrentRow(0)
    APP.processEvents()
    assert window.current_index == 0
    assert window.nickname_edit.text() == "A edited"
    assert window.ledger.pending_pal_count == 2

    window.pal_list.setCurrentRow(2)
    APP.processEvents()
    assert "pending" in window.source_draft_label.text().lower()
    assert "2 changes" in window.source_draft_label.text()
    window.ledger.clear_drafts()
    window.close()


def test_missing_identity_refuses_switch_and_restores_selection() -> None:
    window = _window()
    window.instances[1] = PalInstance(template=_template("PinkCat", "B"), instance_id="")
    window.pal_list.setCurrentRow(1)
    APP.processEvents()

    assert window.current_index == 0
    assert window.pal_list.item(window.pal_list.currentRow()).data(256) == 0
    window.ledger.clear_drafts()
    window.close()


def test_batch_serializer_targets_duplicate_species_by_identity() -> None:
    document = _document()
    edits = (
        BatchEdit("pal-a", PalTemplate(species="PinkCat", nickname="Edited A")),
        BatchEdit("pal-b", PalTemplate(species="PinkCat", nickname="Edited B")),
    )
    results = apply_templates(document, edits)

    assert len(results) == 2
    entries = document["properties"]["worldSaveData"]["value"]["CharacterSaveParameterMap"]["value"]
    assert [entry["key"]["InstanceId"]["value"] for entry in entries] == ["pal-a", "pal-b"]
    assert [entry["value"]["RawData"]["value"]["object"]["SaveParameter"]["value"].get("NickName", {}).get("value") for entry in entries] == ["Edited A", "Edited B"]


def test_batch_validation_reports_unselected_invalid_pal() -> None:
    report = validate_edit_batch(
        (
            BatchEdit("pal-a", _template("PinkCat", "A"), "PinkCat A"),
            BatchEdit("pal-b", PalTemplate(species="", level=2), "PinkCat B"),
        )
    )

    assert not report.valid
    assert any("PinkCat B" in issue.message and issue.field == "species" for issue in report.errors)


def test_batch_direct_save_uses_one_transaction_and_backup(tmp_path: Path) -> None:
    source = tmp_path / "Level.sav"
    source.write_bytes(b"original")
    calls: list[tuple[str, ...]] = []

    def serializer(source_path: Path, output_path: Path, edits: tuple[BatchEdit, ...]):
        calls.append(tuple(edit.instance_id for edit in edits))
        output_path.write_bytes(b"edited-batch")
        from types import SimpleNamespace

        return SimpleNamespace(
            results=tuple(
                SimpleNamespace(instance_id=edit.instance_id, changed_fields=("NickName",))
                for edit in edits
            )
        )

    from pal_editor.backup_store import BackupStore

    request = DirectSaveRequest(
        source_path=source,
        baseline=fingerprint_file(source),
        edits=(
            BatchEdit("pal-a", _template("PinkCat", "A")),
            BatchEdit("pal-b", _template("PinkCat", "B")),
        ),
        backup_store=BackupStore(tmp_path / "backups"),
    )
    result = DirectSaveCoordinator(
        serializer=serializer,
        output_validator=lambda path, edit_result, request: True,
    ).run(request)

    assert result.success
    assert calls == [("pal-a", "pal-b")]
    assert result.backup_path is not None and result.backup_path.exists()
    assert source.read_bytes() == b"edited-batch"


def test_review_lists_every_pal_and_global_revert_clears_all(monkeypatch) -> None:
    window = _window()
    window.nickname_edit.setText("A edited")
    APP.processEvents()
    window.pal_list.setCurrentRow(1)
    APP.processEvents()
    window.nickname_edit.setText("B edited")
    APP.processEvents()

    messages: list[str] = []
    monkeypatch.setattr(
        window,
        "_show_text_dialog",
        lambda _title, text, **_kwargs: messages.append(text),
    )
    window.preview_changes()
    assert messages and "PinkCat (A)" in messages[0] and "PinkCat (B)" in messages[0]
    assert "A edited" in messages[0] and "B edited" in messages[0]

    monkeypatch.setattr("pal_editor.gui.QMessageBox.exec", lambda _box: 0)
    monkeypatch.setattr(
        "pal_editor.gui.QMessageBox.clickedButton",
        lambda box: next(button for button in box.buttons() if button.text() == "Revert All Changes"),
    )
    window.revert_draft()
    assert not window.ledger.dirty
    assert window.nickname_edit.text() == "B"
    window.close()


def test_save_copy_keeps_complete_batch_dirty(tmp_path: Path, monkeypatch) -> None:
    window = _window()
    source = tmp_path / "Level.sav"
    source.write_bytes(b"source")
    window.source_path = source
    window.source_baseline = fingerprint_file(source)
    window.current_index = 0
    window.nickname_edit.setText("A edited")
    APP.processEvents()
    window.pal_list.setCurrentRow(1)
    APP.processEvents()
    window.nickname_edit.setText("B edited")
    APP.processEvents()

    output = tmp_path / "edited-copy.sav"
    monkeypatch.setattr(
        "pal_editor.gui.QFileDialog.getSaveFileName",
        lambda *args, **kwargs: (str(output), "Palworld save (*.sav)"),
    )
    captured: list[tuple[BatchEdit, ...]] = []

    def copy_batch(source_path, output_path, edits, *, backup_path=None):
        captured.append(tuple(edits))
        output_path.write_bytes(b"copy")
        return BatchEditResult(
            results=tuple(
                EditResult(edit.instance_id, ("NickName",)) for edit in edits
            ),
            output_path=str(output_path),
        )

    monkeypatch.setattr("pal_editor.gui.edit_save_copy_batch", copy_batch)
    monkeypatch.setattr(window, "_verify_batch_export", lambda *args: "Verified")
    dialogs: list[str] = []
    monkeypatch.setattr(
        window,
        "_show_text_dialog",
        lambda _title, text, **_kwargs: dialogs.append(text),
    )
    monkeypatch.setattr(window, "_require_game_closed", lambda _action: True)
    window.save_copy()

    assert [edit.instance_id for edit in captured[0]] == ["pal-a", "pal-b"]
    assert window.ledger.dirty
    assert window.ledger.pending_pal_count == 2
    assert window.action_save.isEnabled()
    assert dialogs and "The edited copy was created and verified." in dialogs[0]
    assert "Source safety copy:" not in dialogs[0]
    assert "Edited Pals: 2" in dialogs[0]
    window.ledger.clear_drafts()
    window.close()
