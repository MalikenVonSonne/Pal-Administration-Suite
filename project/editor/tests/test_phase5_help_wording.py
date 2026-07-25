import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from pal_editor.domain import PalInstance, PalTemplate
from pal_editor.gui import ABOUT_TEXT, ATTRIBUTION_TEXT, SAVE_SAFETY_TEXT, PalEditorWindow
from pal_editor.ledger import OperationLedger
from pal_editor.safe_save import fingerprint_file
from pal_editor.presets import PRESETS, BLUEPRINT_CATEGORY_ORDER, blueprint_category, ordered_presets


APP = QApplication.instance() or QApplication([])


def test_phase5_help_dialog_content_is_selectable_user_facing_text(monkeypatch) -> None:
    window = PalEditorWindow()
    captured: list[tuple[str, str]] = []
    monkeypatch.setattr(
        window,
        "_show_text_dialog",
        lambda title, text, **_kwargs: captured.append((title, text)),
    )

    window.action_save_safety.trigger()
    window.action_attribution.trigger()
    window.action_about.trigger()

    assert [title for title, _text in captured] == [
        "Save Safety",
        "Attribution and Licenses",
        "About Pal Admin",
    ]
    assert "%LOCALAPPDATA%\\PalAdmin\\Backups" in SAVE_SAFETY_TEXT
    assert "The latest five verified backups are retained per source" in SAVE_SAFETY_TEXT
    assert "palsav-flex" in ATTRIBUTION_TEXT
    assert "PalCalc" in ATTRIBUTION_TEXT
    assert "MIT License" in ATTRIBUTION_TEXT
    assert "GNU General Public License" in ATTRIBUTION_TEXT
    assert "Pal Administration Suite v1.0.0" in ABOUT_TEXT
    assert "Created by MalikenVonSonne" in ABOUT_TEXT
    assert ABOUT_TEXT.count("Created by MalikenVonSonne") == 1
    assert "MalikenVonSonne" not in ATTRIBUTION_TEXT
    assert "not affiliated with or endorsed by Pocketpair" in ABOUT_TEXT
    assert "—" not in ABOUT_TEXT
    assert "—" not in ATTRIBUTION_TEXT
    assert all("—" not in text for _title, text in captured)
    window.close()


def test_phase5_blueprint_picker_is_deterministic_and_complete() -> None:
    keys = [preset.key for preset in ordered_presets()]
    assert keys == [preset.key for preset in ordered_presets()]
    assert set(keys) == {preset.key for preset in PRESETS}
    assert len(keys) == len(set(keys))
    category_indexes = [
        BLUEPRINT_CATEGORY_ORDER.index(blueprint_category(key)) for key in keys
    ]
    assert category_indexes == sorted(category_indexes)

    window = PalEditorWindow()
    picker_keys = [
        window.preset_combo.itemData(index)
        for index in range(1, window.preset_combo.count())
    ]
    assert picker_keys == keys
    assert window.preset_combo.view().minimumWidth() > 0
    window.close()


def test_phase5_backup_folder_action_creates_and_opens_configured_root(monkeypatch, tmp_path) -> None:
    window = PalEditorWindow()
    backup_root = tmp_path / "PalAdmin" / "Backups"
    monkeypatch.setattr("pal_editor.gui.default_backup_root", lambda: backup_root)
    opened: list[str] = []
    monkeypatch.setattr(
        "pal_editor.gui.QDesktopServices.openUrl",
        lambda url: opened.append(url.toLocalFile()) or True,
    )
    monkeypatch.setattr(window, "_show_transient_status", lambda _text: None)

    assert window.open_backup_folder()
    assert backup_root.is_dir()
    assert opened == [str(backup_root).replace("\\", "/")]
    window.close()


def test_phase5_revert_wording_uses_singular_forms(monkeypatch, tmp_path) -> None:
    window = PalEditorWindow()
    window.instances = [
        PalInstance(
            template=PalTemplate(species="PinkCat", level=2, iv_hp=10, iv_attack=20, iv_defense=30),
            instance_id="phase5-revert",
        )
    ]
    window._refresh_roster_list(preferred_index=0)
    source = tmp_path / "Level.sav"
    source.write_bytes(b"phase5")
    window.source_path = source
    window.source_baseline = fingerprint_file(source)
    window.ledger = OperationLedger(source)
    window.current_index = 0
    window.nickname_edit.setText("Edited")
    APP.processEvents()

    boxes = []
    monkeypatch.setattr("pal_editor.gui.QMessageBox.exec", lambda box: boxes.append(box))
    window.revert_draft()
    assert boxes[0].text() == "Revert the pending change for 1 edited Pal?"
    window.close()
