import os
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QApplication

from pal_editor.domain import PalInstance, PalTemplate
from pal_editor.direct_save import DirectSaveRequest
from pal_editor.gui import PalEditorWindow
from pal_editor.ledger import OperationLedger
from pal_editor.navigation import LEDGER, ROSTER, RUNTIME, SETTINGS_DATA
from pal_editor.safe_save import (
    FailureStage,
    RecoveryResult,
    SourceConfidence,
    TransactionState,
    fingerprint_file,
)


_APP = QApplication.instance() or QApplication([])


def _process_events() -> None:
    _APP.processEvents()


def _window_with_pal() -> PalEditorWindow:
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
            instance_id="phase1-test-instance",
        )
    ]
    window._refresh_roster_list(preferred_index=0)
    _process_events()
    return window


def _dirty_save_window(tmp_path: Path) -> tuple[PalEditorWindow, Path]:
    window = _window_with_pal()
    source = tmp_path / "Level.sav"
    source.write_bytes(b"phase3-gui-baseline")
    window.source_path = source
    window.source_baseline = fingerprint_file(source)
    window.ledger = OperationLedger(source)
    window.current_index = 0
    window.nickname_edit.setText("Phase 3 GUI draft")
    _process_events()
    window._update_action_states()
    assert window.action_save.isEnabled()
    return window, source


def _direct_result(
    tmp_path: Path,
    *,
    success: bool = False,
    state: TransactionState = TransactionState.FAILED,
    recovery: RecoveryResult = RecoveryResult.NOT_REQUIRED,
    confidence: SourceConfidence = SourceConfidence.NOT_VERIFIED,
    failure_stage: FailureStage | None = FailureStage.BACKUP,
    primary: str = "Backup could not be verified",
    cleanup: str | None = None,
    backup: bool = False,
    pruning_warning: str | None = None,
) -> SimpleNamespace:
    backup_path = None
    if backup:
        backup_path = tmp_path / "verified-backup.sav"
        backup_path.write_bytes(b"verified-backup")
    transaction = SimpleNamespace(
        state=state,
        success=success,
        source_confidence=confidence,
        failure_stage=failure_stage,
    )
    return SimpleNamespace(
        success=success,
        transaction=transaction,
        validation_report=None,
        backup_path=backup_path,
        pruning_warning=pruning_warning,
        recovery_result=recovery,
        source_confidence=confidence,
        primary_failure=primary,
        cleanup_failure=cleanup,
    )


def _capture_message_boxes(monkeypatch):
    dialogs: list[tuple[str, str, str, object | None]] = []
    monkeypatch.setattr(
        "pal_editor.gui.QMessageBox.warning",
        lambda _parent, title, text, *args, **kwargs: dialogs.append(
            ("warning", title, text, None)
        ),
    )
    monkeypatch.setattr(
        "pal_editor.gui.QMessageBox.information",
        lambda _parent, title, text, *args, **kwargs: dialogs.append(
            ("information", title, text, None)
        ),
    )
    monkeypatch.setattr(
        "pal_editor.gui.QMessageBox.critical",
        lambda _parent, title, text, *args, **kwargs: dialogs.append(
            ("critical", title, text, None)
        ),
    )
    monkeypatch.setattr(
        "pal_editor.gui.QMessageBox.exec",
        lambda box: dialogs.append(("dialog", box.windowTitle(), box.text(), box.icon())),
    )
    return dialogs


def test_phase1_shared_actions_have_stable_identity_and_retained_buttons() -> None:
    window = PalEditorWindow()

    assert window._action_handler_names == {
        "action_open_source_save": "open_save",
        "action_open_latest_save": "open_latest_detected_save",
        "action_reload_source": "reload",
        "action_save": "save",
        "action_create_save_copy": "save_copy",
        "action_exit": "close",
        "action_review_changes": "preview_changes",
            "action_revert_draft": "revert_draft",
            "action_refresh_snapshot": "refresh_runtime_snapshot",
            "action_save_safety": "show_save_safety",
            "action_attribution": "show_attribution",
            "action_about": "show_about",
            "action_contextual_refresh": "_contextual_refresh",
        "action_view_roster": "navigate",
        "action_view_runtime": "navigate",
        "action_view_ledger": "navigate",
        "action_view_settings": "navigate",
    }
    assert isinstance(window.action_open_source_save, QAction)
    assert window.action_open_source_save is window._shared_actions["action_open_source_save"]
    assert window.action_open_source_save.objectName() == "action_open_source_save"
    assert not hasattr(window, "open_button")
    assert not hasattr(window, "reload_button")
    assert not hasattr(window, "diff_button")
    assert not hasattr(window, "save_button")
    assert window._action_button_bindings["action_open_source_save"] == [
        window.empty_open_button,
    ]
    assert window._action_button_bindings["action_refresh_snapshot"] == [
        window.runtime_refresh_button,
    ]
    assert window._action_button_bindings["action_open_latest_save"] == [
        window.open_latest_button,
    ]
    assert window._action_button_bindings["action_revert_draft"] == [
        window.revert_draft_button,
    ]
    assert window.menuBar() is not None
    window.close()


def test_phase1_action_shortcuts_and_context_dispatch() -> None:
    window = PalEditorWindow()

    assert window.action_open_source_save.shortcut().toString() == "Ctrl+O"
    assert window.action_save.shortcut().toString() == "Ctrl+S"
    assert window.action_create_save_copy.shortcut().toString() == "Ctrl+Shift+S"
    assert window.action_contextual_refresh.shortcut().toString() == "F5"
    assert window.action_contextual_refresh in window.actions()

    source = Path("phase1-source.sav").resolve()
    reloaded: list[Path] = []
    window.source_path = source
    window.load_path = lambda path: reloaded.append(path)  # type: ignore[method-assign]
    window._update_action_states()
    window.action_contextual_refresh.trigger()
    assert reloaded == [source]

    runtime_refreshes: list[bool] = []
    window.refresh_runtime_snapshot = lambda: runtime_refreshes.append(True)  # type: ignore[method-assign]
    window.nav_list.setCurrentRow(1)
    _process_events()
    window.action_contextual_refresh.trigger()
    assert runtime_refreshes == [True]
    window.close()


def test_phase1_enabled_states_match_existing_button_rules() -> None:
    window = PalEditorWindow()

    assert window.action_open_source_save.isEnabled()
    assert window.action_open_latest_save.isEnabled()
    assert not window.action_reload_source.isEnabled()
    assert not window.action_save.isEnabled()
    assert not window.action_review_changes.isEnabled()
    assert not window.action_create_save_copy.isEnabled()
    assert not window.action_revert_draft.isEnabled()
    assert window.empty_open_button.isEnabled() == window.action_open_source_save.isEnabled()

    window.instances = [
        PalInstance(template=PalTemplate(species="PinkCat"), instance_id="state-test")
    ]
    window._refresh_roster_list(preferred_index=0)
    _process_events()
    assert window.action_reload_source.isEnabled() is False
    assert window.action_review_changes.isEnabled()
    assert window.action_create_save_copy.isEnabled()
    assert window.action_review_changes.isEnabled()
    assert window.action_create_save_copy.isEnabled()

    window.reference_only = True
    window._update_action_states()
    assert window.action_review_changes.isEnabled() is False
    assert window.action_create_save_copy.isEnabled() is False
    assert window.action_review_changes.isEnabled() is False
    assert window.action_create_save_copy.isEnabled() is False

    window.reference_only = False
    window.roster_search_edit.setText("does-not-match")
    _process_events()
    assert window.action_review_changes.isEnabled() is False
    assert window.action_create_save_copy.isEnabled() is False
    window.close()


def test_phase1_revert_action_tracks_ledger_state_without_changing_draft_rules() -> None:
    window = _window_with_pal()
    window.ledger = OperationLedger(Path("phase1-source.sav"))
    window.current_index = 0
    window.ledger.set_field("nickname", "", "Draft")
    window._refresh_ledger_page()
    assert window.action_revert_draft.isEnabled()
    assert window.revert_draft_button.isEnabled()

    window.ledger.record_draft({"nickname": "", "level": 1}, {"nickname": "", "level": 1})
    window._refresh_ledger_page()
    assert window.action_revert_draft.isEnabled() is False
    assert window.revert_draft_button.isEnabled() is False
    window.close()


def test_phase3_save_action_requires_dirty_loaded_baseline_and_reuses_coordinator(
    tmp_path: Path,
    monkeypatch,
) -> None:
    window = _window_with_pal()
    source = tmp_path / "Level.sav"
    source.write_bytes(b"baseline")
    window.source_path = source
    window.source_baseline = fingerprint_file(source)
    window.ledger = OperationLedger(source)
    window.current_index = 0
    window.nickname_edit.setText("Phase 3 draft")
    _process_events()
    window._update_action_states()
    assert window.action_save.isEnabled()

    calls: list[DirectSaveRequest] = []

    class StubCoordinator:
        def run(self, request):
            calls.append(request)
            return SimpleNamespace(
                success=True,
                validation_report=None,
                pruning_warning=None,
            )

    window.direct_save_coordinator = StubCoordinator()
    window._require_game_closed = lambda action: True  # type: ignore[method-assign]
    window.load_path = lambda path, **kwargs: True  # type: ignore[method-assign]
    monkeypatch.setattr(
        "pal_editor.gui.QMessageBox.information",
        lambda *args, **kwargs: None,
    )
    window.save()

    assert len(calls) == 1
    assert calls[0].source_path == source.resolve()
    assert calls[0].baseline.sha256 == window.source_baseline.sha256
    assert window.ledger.operation_status == "save_succeeded"
    window.ledger.mark_clean()
    window.close()


def test_phase3_gui_backup_failure_keeps_retryable_dirty_draft(
    tmp_path: Path, monkeypatch
) -> None:
    window, source = _dirty_save_window(tmp_path)
    dialogs = _capture_message_boxes(monkeypatch)
    coordinator_calls: list[DirectSaveRequest] = []
    reload_calls: list[Path] = []

    class StubCoordinator:
        def run(self, request):
            coordinator_calls.append(request)
            return _direct_result(tmp_path, primary="Backup verification failed")

    window.direct_save_coordinator = StubCoordinator()
    window._require_game_closed = lambda action: True  # type: ignore[method-assign]
    window.load_path = lambda path, **kwargs: reload_calls.append(path) or True  # type: ignore[method-assign]
    baseline_hash = window.source_baseline.sha256
    window.save()

    assert len(coordinator_calls) == 1
    assert reload_calls == []
    assert source.read_bytes() == b"phase3-gui-baseline"
    assert window.source_baseline.sha256 == baseline_hash
    assert window.ledger.dirty
    assert window.ledger.operation_status == "transaction_failed"
    assert window.action_save.isEnabled()
    assert any(title == "Save not completed" for _, title, _, _ in dialogs)
    window.close()


def test_phase3_gui_restored_result_preserves_backup_and_draft(
    tmp_path: Path, monkeypatch
) -> None:
    window, source = _dirty_save_window(tmp_path)
    dialogs = _capture_message_boxes(monkeypatch)
    reload_calls: list[Path] = []

    class StubCoordinator:
        def run(self, request):
            return _direct_result(
                tmp_path,
                recovery=RecoveryResult.RESTORED,
                confidence=SourceConfidence.ORIGINAL_RESTORED,
                state=TransactionState.RESTORED,
                failure_stage=FailureStage.RESTORATION,
                primary="Edited output failed verification",
                backup=True,
            )

    window.direct_save_coordinator = StubCoordinator()
    window._require_game_closed = lambda action: True  # type: ignore[method-assign]
    window.load_path = lambda path, **kwargs: reload_calls.append(path) or True  # type: ignore[method-assign]
    baseline_hash = window.source_baseline.sha256
    window.save()

    assert reload_calls == []
    assert source.read_bytes() == b"phase3-gui-baseline"
    assert window.source_baseline.sha256 == baseline_hash
    assert window.ledger.dirty
    assert window.ledger.operation_status == "restored"
    dialog = next(item for item in dialogs if item[0] == "dialog")
    assert dialog[1] == "Save not completed; source restored"
    assert "Verified backup retained at:" in dialog[2]
    assert "original source was restored" in dialog[2]
    window.close()


def test_phase3_gui_uncertain_result_is_critical_and_keeps_diagnostics(
    tmp_path: Path, monkeypatch
) -> None:
    window, _source = _dirty_save_window(tmp_path)
    dialogs = _capture_message_boxes(monkeypatch)

    class StubCoordinator:
        def run(self, request):
            return _direct_result(
                tmp_path,
                recovery=RecoveryResult.FAILED,
                confidence=SourceConfidence.UNCERTAIN,
                failure_stage=FailureStage.RESTORATION,
                primary="Replacement completed but verification failed",
                cleanup="temporary cleanup failed",
                backup=True,
            )

    window.direct_save_coordinator = StubCoordinator()
    window._require_game_closed = lambda action: True  # type: ignore[method-assign]
    window.save()

    dialog = next(item for item in dialogs if item[0] == "dialog")
    assert dialog[1] == "Save outcome uncertain"
    assert dialog[3].name == "Critical"
    assert "Replacement completed but verification failed" in dialog[2]
    assert "Cleanup detail: temporary cleanup failed" in dialog[2]
    assert "Verified backup retained at:" in dialog[2]
    assert "Do not launch Palworld" in dialog[2]
    assert window.ledger.dirty
    assert window.ledger.operation_status == "uncertain"
    window.close()


def test_phase3_gui_external_source_change_is_distinguished_and_non_destructive(
    tmp_path: Path, monkeypatch
) -> None:
    window, source = _dirty_save_window(tmp_path)
    dialogs = _capture_message_boxes(monkeypatch)
    reload_calls: list[Path] = []
    baseline_hash = window.source_baseline.sha256

    class StubCoordinator:
        def run(self, request):
            return _direct_result(
                tmp_path,
                failure_stage=FailureStage.SOURCE_FINGERPRINT,
                primary="Source changed after loading (content_changed)",
            )

    window.direct_save_coordinator = StubCoordinator()
    window._require_game_closed = lambda action: True  # type: ignore[method-assign]
    window.load_path = lambda path, **kwargs: reload_calls.append(path) or True  # type: ignore[method-assign]
    window.save()

    assert reload_calls == []
    assert source.read_bytes() == b"phase3-gui-baseline"
    assert window.source_baseline.sha256 == baseline_hash
    assert window.ledger.dirty
    assert window.ledger.operation_status == "source_changed"
    dialog = next(item for item in dialogs if item[0] == "dialog")
    assert dialog[1] == "Source changed since load"
    assert "Reload Source Save" in dialog[2]
    assert "Save a Copy" in dialog[2]
    window.close()


def test_phase3_gui_verified_disk_save_reports_refresh_failure_without_second_save(
    tmp_path: Path, monkeypatch
) -> None:
    window, _source = _dirty_save_window(tmp_path)
    dialogs = _capture_message_boxes(monkeypatch)
    reload_calls: list[Path] = []
    coordinator_calls = 0

    class StubCoordinator:
        def run(self, request):
            nonlocal coordinator_calls
            coordinator_calls += 1
            return _direct_result(
                tmp_path,
                success=True,
                state=TransactionState.COMPLETED,
                confidence=SourceConfidence.EDITED_SOURCE_VERIFIED,
                failure_stage=None,
                primary=None,
                backup=True,
            )

    window.direct_save_coordinator = StubCoordinator()
    window._require_game_closed = lambda action: True  # type: ignore[method-assign]
    window.load_path = lambda path, **kwargs: reload_calls.append(path) or False  # type: ignore[method-assign]
    window.save()

    assert coordinator_calls == 1
    assert len(reload_calls) == 1
    assert window.ledger.dirty
    assert window.ledger.operation_status == "save_refresh_failed"
    assert any(title == "Save verified; reload needed" for _, title, _, _ in dialogs)
    assert not any(title == "Save complete" for _, title, _, _ in dialogs)
    window.close()


def test_phase3_gui_pruning_warning_keeps_verified_save_successful(
    tmp_path: Path, monkeypatch
) -> None:
    window, _source = _dirty_save_window(tmp_path)
    dialogs = _capture_message_boxes(monkeypatch)

    class StubCoordinator:
        def run(self, request):
            return _direct_result(
                tmp_path,
                success=True,
                state=TransactionState.COMPLETED,
                confidence=SourceConfidence.EDITED_SOURCE_VERIFIED,
                failure_stage=None,
                primary=None,
                backup=True,
                pruning_warning="Could not remove an older verified backup",
            )

    window.direct_save_coordinator = StubCoordinator()
    window._require_game_closed = lambda action: True  # type: ignore[method-assign]
    window.load_path = lambda path, **kwargs: window.ledger.mark_clean() or True  # type: ignore[method-assign]
    window.save()

    assert not window.ledger.dirty
    assert window.ledger.operation_status == "save_succeeded_prune_warning"
    dialog = next(item for item in dialogs if item[0] == "information")
    assert dialog[1] == "Save complete"
    assert "Could not remove an older verified backup" in dialog[2]
    window.close()


def test_phase3_gui_invalid_dirty_draft_stops_before_transaction_stage(
    tmp_path: Path, monkeypatch
) -> None:
    window, source = _dirty_save_window(tmp_path)
    dialogs = _capture_message_boxes(monkeypatch)
    transaction_factory_calls = 0

    def transaction_factory():
        nonlocal transaction_factory_calls
        transaction_factory_calls += 1
        raise AssertionError("invalid draft reached SafeSaveTransaction")

    from pal_editor.direct_save import DirectSaveCoordinator

    window.direct_save_coordinator = DirectSaveCoordinator(
        transaction_factory=transaction_factory,
    )
    window._require_game_closed = lambda action: True  # type: ignore[method-assign]
    window.form_template = lambda: PalTemplate(species="")  # type: ignore[method-assign]
    window.save()

    assert transaction_factory_calls == 0
    assert source.read_bytes() == b"phase3-gui-baseline"
    assert window.ledger.dirty
    assert window.ledger.operation_status == "validation_failed"
    assert any(title == "Validation failed" for _, title, _, _ in dialogs)
    window.close()


def test_phase3_gui_active_transaction_blocks_reentry_and_restores_save_state(
    tmp_path: Path,
) -> None:
    window, _source = _dirty_save_window(tmp_path)
    coordinator_calls = 0

    class StubCoordinator:
        def run(self, request):
            nonlocal coordinator_calls
            coordinator_calls += 1
            raise AssertionError("active Save re-entered the coordinator")

    window.direct_save_coordinator = StubCoordinator()
    window._direct_save_active = True
    window._update_action_states()
    assert not window.action_save.isEnabled()
    window.save()
    assert coordinator_calls == 0
    window._direct_save_active = False
    window._update_action_states()
    assert window.action_save.isEnabled()
    window.ledger.mark_clean()
    window.close()


def test_phase3_gui_palworld_lock_blocks_save_and_recovers_enabled_state(
    tmp_path: Path, monkeypatch
) -> None:
    from pal_editor.gui import GameSafetyStatus

    window, _source = _dirty_save_window(tmp_path)
    dialogs = _capture_message_boxes(monkeypatch)
    coordinator_calls = 0

    class StubCoordinator:
        def run(self, request):
            nonlocal coordinator_calls
            coordinator_calls += 1
            raise AssertionError("Palworld lock reached the coordinator")

    window.direct_save_coordinator = StubCoordinator()
    window.show()
    _process_events()
    monkeypatch.setattr(
        "pal_editor.gui.get_game_safety_status",
        lambda: GameSafetyStatus(("Palworld.exe",)),
    )
    window._refresh_safety_status()
    window._update_action_states()
    assert not window.action_save.isEnabled()
    assert window.safety_warning_banner.isVisible()
    baseline_hash = window.source_baseline.sha256
    window.save()

    assert coordinator_calls == 0
    assert window.ledger.dirty
    assert window.source_baseline.sha256 == baseline_hash
    assert any(title == "Close Palworld first" for _, title, _, _ in dialogs)

    monkeypatch.setattr("pal_editor.gui.get_game_safety_status", lambda: GameSafetyStatus(()))
    window._refresh_safety_status()
    window._update_action_states()
    assert not window.safety_warning_banner.isVisible()
    assert window.action_save.isEnabled()
    window.close()


def test_phase1_view_actions_remain_exclusive_and_follow_sidebar() -> None:
    window = PalEditorWindow()
    actions_by_key = {
        ROSTER: window.action_view_roster,
        RUNTIME: window.action_view_runtime,
        LEDGER: window.action_view_ledger,
        SETTINGS_DATA: window.action_view_settings,
    }

    for row, key in enumerate((ROSTER, RUNTIME, LEDGER, SETTINGS_DATA)):
        window.nav_list.setCurrentRow(row)
        _process_events()
        assert actions_by_key[key].isChecked()
        assert sum(action.isChecked() for action in actions_by_key.values()) == 1

    window.action_view_roster.trigger()
    _process_events()
    assert window.nav_list.currentRow() == 0
    assert window.content_stack.currentIndex() == window._nav_pages[ROSTER]
    assert window.action_view_roster.isChecked()
    window.close()


def _menus(window: PalEditorWindow) -> dict[str, object]:
    return {
        "File": window.file_menu,
        "Edit": window.edit_menu,
        "View": window.view_menu,
        "Help": window.help_menu,
    }


def test_phase2_native_menu_structure_reuses_phase1_actions() -> None:
    window = PalEditorWindow()
    menus = _menus(window)

    assert list(menus) == ["File", "Edit", "View", "Help"]
    assert set(menus) == {"File", "Edit", "View", "Help"}
    assert "Tools" not in menus
    assert "Blueprints" not in menus

    file_menu = menus["File"]
    file_actions = file_menu.actions()
    assert file_actions[0] is window.action_open_source_save
    assert file_actions[1] is window.action_open_latest_save
    assert file_actions[2] is window.action_reload_source
    assert file_actions[3].isSeparator()
    assert file_actions[4] is window.action_save
    assert file_actions[5] is window.action_create_save_copy
    assert file_actions[6].isSeparator()
    assert file_actions[7] is window.action_exit

    edit_menu = menus["Edit"]
    assert edit_menu.actions() == [window.action_review_changes, window.action_revert_draft]

    view_menu = menus["View"]
    assert view_menu.actions() == [
        window.action_view_roster,
        window.action_view_runtime,
        window.action_view_ledger,
        window.action_view_settings,
    ]
    assert all(action.isCheckable() for action in view_menu.actions())

    help_menu = menus["Help"]
    help_actions = help_menu.actions()
    assert help_actions[0] is window.action_save_safety
    assert help_actions[1].isSeparator()
    assert help_actions[2] is window.action_attribution
    assert help_actions[3] is window.action_about
    assert all(action.shortcut().isEmpty() for action in help_actions if not action.isSeparator())
    window.close()


def test_phase2_menu_shortcuts_are_unique_and_unmodified() -> None:
    window = PalEditorWindow()
    menus = _menus(window)
    actions = [window.action_contextual_refresh]
    for menu in menus.values():
        actions.extend(action for action in menu.actions() if not action.isSeparator())

    shortcut_map: dict[str, list[QAction]] = {}
    for action in actions:
        shortcut = action.shortcut().toString()
        if shortcut:
            shortcut_map.setdefault(shortcut, []).append(action)

    assert [action.objectName() for action in shortcut_map["Ctrl+O"]] == [
        "action_open_source_save"
    ]
    assert [action.objectName() for action in shortcut_map["Ctrl+Shift+S"]] == [
        "action_create_save_copy"
    ]
    assert [action.objectName() for action in shortcut_map["F5"]] == [
        "action_contextual_refresh"
    ]
    assert [action.objectName() for action in shortcut_map["Ctrl+S"]] == [
        "action_save"
    ]
    assert window.action_revert_draft.shortcut().isEmpty()
    assert window.action_contextual_refresh.shortcutContext() == Qt.ShortcutContext.WindowShortcut
    window.close()


def test_phase2_menu_state_and_disabled_trigger_follow_shared_actions() -> None:
    window = PalEditorWindow()
    menus = _menus(window)
    file_menu = menus["File"]
    edit_menu = menus["Edit"]

    assert file_menu.actions()[2].isEnabled() is False
    assert file_menu.actions()[4].isEnabled() is False
    assert file_menu.actions()[5].isEnabled() is False
    assert edit_menu.actions()[0].isEnabled() is False
    assert edit_menu.actions()[1].isEnabled() is False
    assert window.action_reload_source.isEnabled() == file_menu.actions()[2].isEnabled()
    assert window.action_save.isEnabled() == file_menu.actions()[4].isEnabled()
    assert window.action_create_save_copy.isEnabled() == file_menu.actions()[5].isEnabled()

    blocked = QSignalSpy(window.action_create_save_copy.triggered)
    window.action_create_save_copy.trigger()
    assert blocked.count() == 0

    window.instances = [
        PalInstance(template=PalTemplate(species="PinkCat"), instance_id="menu-state")
    ]
    window._refresh_roster_list(preferred_index=0)
    _process_events()
    assert file_menu.actions()[4].isEnabled() is False
    assert file_menu.actions()[5].isEnabled()
    assert window.action_create_save_copy.isEnabled()

    window.reference_only = True
    window._update_action_states()
    assert file_menu.actions()[5].isEnabled() is False
    assert window.action_create_save_copy.isEnabled() is False
    window.close()


def test_phase2_view_menu_and_sidebar_are_bidirectionally_synchronized() -> None:
    window = PalEditorWindow()
    view_menu = _menus(window)["View"]

    window.nav_list.setCurrentRow(2)
    _process_events()
    assert window.content_stack.currentIndex() == window._nav_pages[LEDGER]
    assert window.action_view_ledger.isChecked()
    assert sum(action.isChecked() for action in view_menu.actions()) == 1

    window.action_view_settings.trigger()
    _process_events()
    assert window.nav_list.currentRow() == 3
    assert window.content_stack.currentIndex() == window._nav_pages[SETTINGS_DATA]
    assert window.action_view_settings.isChecked()

    window.navigate(0)
    _process_events()
    assert window.nav_list.currentRow() == 0
    assert window.content_stack.currentIndex() == window._nav_pages[ROSTER]
    assert window.action_view_roster.isChecked()
    assert sum(action.isChecked() for action in view_menu.actions()) == 1

    before = window.nav_list.currentRow()
    view_menu.aboutToShow.emit()
    view_menu.aboutToHide.emit()
    _process_events()
    assert window.nav_list.currentRow() == before
    window.close()


def test_phase3_condensed_shell_removes_obsolete_header_and_top_buttons() -> None:
    window = PalEditorWindow()
    window.show()
    _process_events()

    assert not hasattr(window, "open_button")
    assert not hasattr(window, "reload_button")
    assert not hasattr(window, "diff_button")
    assert not hasattr(window, "save_button")
    assert not hasattr(window, "path_label")
    assert not hasattr(window, "draft_label")
    assert not hasattr(window, "safety_label")
    assert not window.empty_open_button.isHidden()
    assert not window.runtime_refresh_button.isHidden()
    assert not window.safety_warning_banner.isVisible()
    assert window.centralWidget().layout().count() == 2
    assert window.menuBar().actions()[0].text() == "File"
    window.close()


def test_phase3_title_and_permanent_state_widgets() -> None:
    window = _window_with_pal()
    window.source_path = Path("C:/Saves/Level_edited_v4.sav").resolve()
    window._update_window_title()
    window._update_action_states()

    assert window.windowTitle() == "Pal Admin: Level_edited_v4.sav"
    assert str(window.source_path) not in window.windowTitle()
    assert window.draft_status_label.text() == "Draft: Clean"
    assert window.palworld_status_label.text() == "Palworld: Closed"
    assert window.source_status_label.text() == "Source: Loaded"
    assert str(window.source_path) in window.source_status_label.toolTip()
    assert "automatic verified backup" in window.source_status_label.toolTip()

    window.ledger = OperationLedger(window.source_path)
    window.nickname_edit.setText("Draft Pal")
    _process_events()
    assert window.draft_status_label.text() == "Draft: 1 change"
    assert "1 pending change" in window.draft_status_label.toolTip()
    assert "*" not in window.windowTitle()

    window.nickname_edit.clear()
    _process_events()
    assert window.draft_status_label.text() == "Draft: Clean"
    window.source_path = None
    window._update_window_title()
    window._update_action_states()
    assert window.windowTitle() == "Pal Admin: Roster Workbench"
    assert window.source_status_label.text() == "Source: None"
    window.close()


def test_phase3_guidance_transient_message_and_permanent_widgets_coexist() -> None:
    window = PalEditorWindow()
    window.show()
    _process_events()
    window.navigate(1)
    assert "running game" in window.status_guidance_label.text().casefold()

    window._show_transient_status("Transient operation complete", 1000)
    assert window.statusBar().currentMessage() == "Transient operation complete"
    assert window.draft_status_label.isVisible()
    assert window.palworld_status_label.isVisible()
    assert window.source_status_label.isVisible()

    window.statusBar().clearMessage()
    assert "running game" in window.status_guidance_label.text().casefold()
    window.close()


def test_phase3_running_warning_reuses_safety_source_and_lock(monkeypatch) -> None:
    from pal_editor.gui import GameSafetyStatus

    window = _window_with_pal()
    window.show()
    _process_events()
    window.source_path = Path("C:/Saves/Level.sav").resolve()
    window.reference_only = True
    window._set_editing_enabled(False)
    monkeypatch.setattr(
        "pal_editor.gui.get_game_safety_status",
        lambda: GameSafetyStatus(("Palworld.exe",)),
    )
    window._refresh_safety_status()

    assert window.palworld_status_label.text() == "Palworld: Running"
    assert window.safety_warning_banner.isVisible()
    assert "Editing is locked" in window.safety_warning_banner.text()
    assert "Palworld is running" in window.palworld_status_label.toolTip()
    assert window.nickname_edit.isEnabled() is False

    monkeypatch.setattr(
        "pal_editor.gui.get_game_safety_status",
        lambda: GameSafetyStatus(()),
    )
    window._refresh_safety_status()
    assert window.palworld_status_label.text() == "Palworld: Closed"
    assert not window.safety_warning_banner.isVisible()
    window.close()
