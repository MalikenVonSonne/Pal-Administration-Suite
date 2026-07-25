import os
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QApplication, QMessageBox

from pal_editor.gui import DirtyDraftDecision, PalEditorWindow
from pal_editor.ledger import OperationLedger
from pal_editor.safety import GameSafetyStatus
from pal_editor.safe_save import fingerprint_file


_APP = QApplication.instance() or QApplication([])


def _process_events() -> None:
    _APP.processEvents()


def _dirty_window(tmp_path: Path) -> tuple[PalEditorWindow, Path]:
    from pal_editor.domain import PalInstance, PalTemplate

    window = PalEditorWindow()
    window.instances = [
        PalInstance(
            template=PalTemplate(
                species="PinkCat",
                level=2,
                iv_hp=65,
                iv_attack=21,
                iv_defense=16,
            ),
            instance_id="phase4-test-instance",
        )
    ]
    window._refresh_roster_list(preferred_index=0)
    _process_events()
    source = tmp_path / "Level.sav"
    source.write_bytes(b"phase4-source")
    window.source_path = source
    window.source_baseline = fingerprint_file(source)
    window.ledger = OperationLedger(source)
    window.current_index = 0
    window.nickname_edit.setText("Phase 4 draft")
    _process_events()
    window._update_action_states()
    assert window.ledger.dirty
    return window, source


def _operation_inputs(monkeypatch, window: PalEditorWindow, target: Path, operation: str):
    if operation == "open_source":
        monkeypatch.setattr(
            "pal_editor.gui.QFileDialog.getOpenFileName",
            lambda *args, **kwargs: (str(target), "Palworld saves (*.sav)"),
        )
        return window.open_save
    if operation == "open_latest":
        monkeypatch.setattr(
            "pal_editor.gui.find_latest_level_save",
            lambda _directory: target,
        )
        return window.open_latest_detected_save
    if operation == "reload":
        window.source_path = target
        return window.reload
    raise AssertionError(operation)


@pytest.mark.parametrize("operation", ["open_source", "open_latest", "reload"])
def test_dirty_cancel_stops_source_replacement(monkeypatch, tmp_path, operation):
    window, source = _dirty_window(tmp_path)
    target = tmp_path / "other.sav"
    target.write_bytes(b"other")
    called = []
    monkeypatch.setattr(
        window,
        "_prompt_dirty_draft",
        lambda *args, **kwargs: DirtyDraftDecision.CANCEL,
    )
    monkeypatch.setattr(window, "load_path", lambda path: called.append(path) or True)

    result = _operation_inputs(monkeypatch, window, target, operation)()

    assert result is False
    assert called == []
    expected_source = target if operation == "reload" else source
    assert window.source_path == expected_source
    assert window.ledger.dirty
    window.close()


def test_dirty_cancel_stops_native_close_and_exit_action(monkeypatch, tmp_path):
    window, _source = _dirty_window(tmp_path)
    prompt_calls = []
    monkeypatch.setattr(
        window,
        "_prompt_dirty_draft",
        lambda *args, **kwargs: prompt_calls.append(args[0]) or DirtyDraftDecision.CANCEL,
    )

    native_event = QCloseEvent()
    window.closeEvent(native_event)
    assert native_event.isAccepted() is False
    assert prompt_calls == ["exit"]

    window.action_exit.trigger()
    assert prompt_calls == ["exit", "exit"]
    assert window.isVisible() is False or window.isVisible() is True
    window.close()


@pytest.mark.parametrize("operation", ["open_source", "open_latest", "reload"])
def test_dirty_discard_never_saves_and_continues_once(monkeypatch, tmp_path, operation):
    window, _source = _dirty_window(tmp_path)
    target = tmp_path / "other.sav"
    target.write_bytes(b"other")
    called = []
    monkeypatch.setattr(
        window,
        "_prompt_dirty_draft",
        lambda *args, **kwargs: DirtyDraftDecision.DISCARD,
    )
    monkeypatch.setattr(window, "save", lambda: (_ for _ in ()).throw(AssertionError("save")))
    monkeypatch.setattr(window, "load_path", lambda path: called.append(path) or True)

    assert _operation_inputs(monkeypatch, window, target, operation)() is True
    assert called == [target]
    window.close()


def test_dirty_discard_failed_load_preserves_source_and_draft(monkeypatch, tmp_path):
    window, source = _dirty_window(tmp_path)
    original_baseline = window.source_baseline
    target = tmp_path / "invalid.sav"
    target.write_bytes(b"not a Palworld save")
    monkeypatch.setattr(
        "pal_editor.gui.QFileDialog.getOpenFileName",
        lambda *args, **kwargs: (str(target), ""),
    )
    monkeypatch.setattr(
        window,
        "_prompt_dirty_draft",
        lambda *args, **kwargs: DirtyDraftDecision.DISCARD,
    )
    monkeypatch.setattr("pal_editor.gui.QMessageBox.critical", lambda *args, **kwargs: None)
    monkeypatch.setattr("pal_editor.gui.QMessageBox.exec", lambda _box: 0)

    assert window.open_save() is False
    assert window.source_path == source
    assert window.source_baseline == original_baseline
    assert window.ledger.dirty
    window.close()


def test_dirty_save_continues_only_after_success(monkeypatch, tmp_path):
    window, _source = _dirty_window(tmp_path)
    continued = []
    monkeypatch.setattr(
        window,
        "_prompt_dirty_draft",
        lambda *args, **kwargs: DirtyDraftDecision.SAVE,
    )
    monkeypatch.setattr(window, "save", lambda: True)

    assert window._guard_pending_operation("reload", lambda: continued.append(True) or True)
    assert continued == [True]
    window.close()


def test_dirty_save_failure_aborts_without_continuation(monkeypatch, tmp_path):
    window, _source = _dirty_window(tmp_path)
    continued = []
    monkeypatch.setattr(
        window,
        "_prompt_dirty_draft",
        lambda *args, **kwargs: DirtyDraftDecision.SAVE,
    )
    monkeypatch.setattr(window, "save", lambda: False)

    assert window._guard_pending_operation("reload", lambda: continued.append(True) or True) is False
    assert continued == []
    assert window.ledger.dirty
    window.close()


def test_dirty_save_with_pruning_warning_is_still_allowed(monkeypatch, tmp_path):
    window, _source = _dirty_window(tmp_path)
    continued = []
    monkeypatch.setattr(
        window,
        "_prompt_dirty_draft",
        lambda *args, **kwargs: DirtyDraftDecision.SAVE,
    )
    warning_result = SimpleNamespace(success=True, pruning_warning="retention warning")
    monkeypatch.setattr(window, "save", lambda: warning_result.success)

    assert window._guard_pending_operation("reload", lambda: continued.append(True) or True)
    assert continued == [True]
    window.close()


def test_active_transaction_blocks_all_five_operations(monkeypatch, tmp_path):
    window, _source = _dirty_window(tmp_path)
    window._direct_save_active = True
    target = tmp_path / "other.sav"
    target.write_bytes(b"other")
    prompt_calls = []
    warnings = []
    monkeypatch.setattr(
        window,
        "_prompt_dirty_draft",
        lambda *args, **kwargs: prompt_calls.append(args[0]) or DirtyDraftDecision.DISCARD,
    )
    monkeypatch.setattr(
        "pal_editor.gui.QMessageBox.warning",
        lambda _parent, title, text, *args, **kwargs: warnings.append((title, text)),
    )
    monkeypatch.setattr(window, "load_path", lambda path: (_ for _ in ()).throw(AssertionError("load")))
    monkeypatch.setattr(
        "pal_editor.gui.find_latest_level_save",
        lambda _directory: target,
    )
    monkeypatch.setattr(
        "pal_editor.gui.QFileDialog.getOpenFileName",
        lambda *args, **kwargs: (str(target), ""),
    )

    assert window.open_save() is False
    assert window.open_latest_detected_save() is False
    assert window.reload() is False
    native_event = QCloseEvent()
    window.closeEvent(native_event)
    window.action_exit.trigger()

    assert prompt_calls == []
    assert len(warnings) == 5
    assert all(title == "Save in progress" for title, _text in warnings)
    assert window.ledger.dirty
    window.close()


def test_no_candidate_does_not_prompt(monkeypatch, tmp_path):
    window, _source = _dirty_window(tmp_path)
    prompts = []
    monkeypatch.setattr(
        window,
        "_prompt_dirty_draft",
        lambda *args, **kwargs: prompts.append(True) or DirtyDraftDecision.CANCEL,
    )
    monkeypatch.setattr(
        "pal_editor.gui.QFileDialog.getOpenFileName",
        lambda *args, **kwargs: ("", ""),
    )
    monkeypatch.setattr("pal_editor.gui.find_latest_level_save", lambda _directory: None)

    assert window.open_save() is False
    assert window.open_latest_detected_save() is False
    assert prompts == []
    window.close()


def test_prompt_has_native_buttons_and_cancel_default(monkeypatch, tmp_path):
    window, _source = _dirty_window(tmp_path)
    boxes = []
    monkeypatch.setattr("pal_editor.gui.QMessageBox.exec", lambda box: boxes.append(box))

    decision = window._prompt_dirty_draft("open_source", save_available=True)
    assert decision is DirtyDraftDecision.CANCEL
    assert len(boxes) == 1
    box = boxes[0]
    assert box.windowTitle() == "Unsaved changes"
    assert {button.text() for button in box.buttons()} == {
        "Save",
        "Discard Changes",
        "Cancel",
    }
    assert box.defaultButton().text() == "Cancel"
    assert box.escapeButton().text() == "Cancel"

    boxes.clear()
    decision = window._prompt_dirty_draft("reload", save_available=False)
    assert decision is DirtyDraftDecision.CANCEL
    assert {button.text() for button in boxes[0].buttons()} == {
        "Discard Changes",
        "Cancel",
    }
    assert "Palworld is running" in boxes[0].text()
    window.close()


def test_running_game_dirty_prompt_omits_save(monkeypatch, tmp_path):
    window, _source = _dirty_window(tmp_path)
    captured = []
    locked = GameSafetyStatus(("Palworld-Win64-Shipping.exe",))

    def refresh_locked():
        window.safety_status = locked
        return locked

    monkeypatch.setattr(window, "_refresh_safety_status", refresh_locked)
    monkeypatch.setattr(
        window,
        "_prompt_dirty_draft",
        lambda operation, *, save_available: captured.append(save_available)
        or DirtyDraftDecision.CANCEL,
    )

    assert window._guard_pending_operation("reload", lambda: True) is False
    assert captured == [False]
    window.close()


def test_clean_operation_proceeds_without_prompt(monkeypatch, tmp_path):
    window, source = _dirty_window(tmp_path)
    window.ledger.mark_clean()
    target = tmp_path / "other.sav"
    target.write_bytes(b"other")
    called = []
    monkeypatch.setattr(
        window,
        "_prompt_dirty_draft",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("prompt")),
    )
    monkeypatch.setattr(window, "load_path", lambda path: called.append(path) or True)

    assert _operation_inputs(monkeypatch, window, target, "open_source")() is True
    assert called == [target]
    assert window.source_path == source
    window.close()


def test_action_exit_and_native_close_share_close_event(monkeypatch):
    window = PalEditorWindow()
    calls = []
    original = window.closeEvent

    def capture(event):
        calls.append(event)
        original(event)

    monkeypatch.setattr(window, "closeEvent", capture)
    window.action_exit.trigger()
    assert len(calls) == 1

    second = QCloseEvent()
    capture(second)
    assert len(calls) == 2
    window.close()
