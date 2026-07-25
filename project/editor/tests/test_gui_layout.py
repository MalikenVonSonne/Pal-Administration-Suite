import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QGroupBox, QScrollArea
from PySide6.QtTest import QTest

from pal_editor.domain import PalInstance, PalTemplate
from pal_editor.gui import PalEditorWindow
from pal_editor.ledger import OperationLedger
from pal_editor.navigation import BLUEPRINTS, RUNTIME


_APP = QApplication.instance() or QApplication([])


def _app() -> QApplication:
    return _APP


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
            instance_id="test-instance",
        )
    ]
    window._refresh_roster_list(preferred_index=0)
    window.show()
    _app().processEvents()
    window._fit_roster_editor()
    _app().processEvents()
    return window


def test_public_navigation_hides_unfinished_blueprints_placeholder():
    window = PalEditorWindow()
    window.show()
    _app().processEvents()

    labels = [window.nav_list.item(index).text() for index in range(window.nav_list.count())]
    assert labels == ["Roster", "Live Roster", "Ledger", "Settings / Data"]
    assert BLUEPRINTS not in window._nav_pages
    window.close()


def test_settings_safety_note_wraps_for_narrow_public_layout():
    window = PalEditorWindow()
    window.show()
    window.resize(760, 480)
    _app().processEvents()

    assert window.settings_safety_note.wordWrap()
    assert window.settings_safety_note.sizePolicy().horizontalPolicy().name == "Ignored"
    window.close()


def test_roster_workflow_labels_and_normal_layout():
    window = _window_with_pal()
    window.resize(980, 600)
    _app().processEvents()

    tab_labels = [window.roster_detail_tabs.tabText(i).replace("&&", "&") for i in range(3)]
    assert tab_labels == [
        "Overview",
        "Build",
        "Skills & Passives",
    ]
    assert window.apply_preset_button.text() == "Apply Blueprint"
    assert window.scope_attributes.text() == "Apply IVs"
    assert [window.scope_level.text(), window.scope_rank.text()] == ["Level", "Rank"]
    assert [window.scope_active_skills.text(), window.scope_passives.text()] == [
        "Active Skills",
        "Passives",
    ]
    build_titles = [group.title() for group in window.roster_detail_tabs.findChildren(QGroupBox)]
    displayed_build_titles = [title.replace("&&", "&") for title in build_titles]
    assert "Manual Progression & IVs" in displayed_build_titles
    assert all("_" not in text for text in [
        *tab_labels,
        *displayed_build_titles,
    ])
    for index in range(window.roster_detail_tabs.count()):
        window.roster_detail_tabs.setCurrentIndex(index)
        _app().processEvents()
        assert not window.roster_right_scroll.horizontalScrollBar().isVisible()
        assert not window.roster_right_scroll.verticalScrollBar().isVisible()

    window.close()


def test_empty_state_save_button_is_compact_and_visible_page_fits():
    window = PalEditorWindow()
    window.show()
    window.resize(980, 600)
    _app().processEvents()
    window._fit_roster_editor()
    _app().processEvents()

    assert window.empty_open_button.maximumWidth() <= 320
    assert window.empty_open_button.sizePolicy().horizontalPolicy().name == "Fixed"
    assert window.empty_open_button.x() <= 20
    assert not window.roster_right_scroll.horizontalScrollBar().isVisible()
    assert not window.roster_right_scroll.verticalScrollBar().isVisible()
    window.close()


def test_selection_details_uses_remaining_space_and_internal_scroll_only():
    window = _window_with_pal()
    window.resize(980, 600)
    window.roster_detail_tabs.setCurrentIndex(2)
    _app().processEvents()
    window._fit_roster_editor()
    _app().processEvents()

    skills_page = window.roster_detail_tabs.currentWidget()
    normal_height = window.skill_details.height()
    active_group = next(group for group in skills_page.findChildren(QGroupBox) if group.title() == "Active Skills")
    passive_group = next(group for group in skills_page.findChildren(QGroupBox) if group.title() == "Passives")
    assert normal_height >= 200
    assert 6 <= passive_group.y() - active_group.geometry().bottom() <= 9
    assert 6 <= window.skill_details.y() - passive_group.geometry().bottom() <= 9
    assert 2 <= skills_page.height() - window.skill_details.geometry().bottom() <= 12
    assert not window.skill_detail_scroll.verticalScrollBar().isVisible()
    assert not window.skill_detail_scroll.horizontalScrollBar().isVisible()
    assert not window.roster_right_scroll.verticalScrollBar().isVisible()
    assert not window.roster_right_scroll.horizontalScrollBar().isVisible()

    window.resize(980, 800)
    _app().processEvents()
    window._fit_roster_editor()
    _app().processEvents()
    assert window.skill_details.height() > normal_height
    assert 2 <= skills_page.height() - window.skill_details.geometry().bottom() <= 12

    window.skill_detail_label.setText("\n".join(["Long skill description"] * 100))
    _app().processEvents()
    assert window.skill_detail_scroll.verticalScrollBar().isVisible()
    assert not window.skill_detail_scroll.horizontalScrollBar().isVisible()
    window.close()


def test_skill_selector_focus_and_click_inspect_without_mutating_draft():
    window = _window_with_pal()
    window.instances[0].template = PalTemplate(
        species="PinkCat",
        active_skills=["EPalWazaID::Unique_PinkCat_CatPunch"],
        passive_skills=["PAL_ALLAttack_down1"],
    )
    window._refresh_roster_list(preferred_index=0)
    window.ledger = OperationLedger(Path("inspection-source.sav"))
    window._update_action_states()
    window._refresh_selected_pending_message()
    selectors = [*window.active_selectors, *window.passive_selectors]
    original_indexes = [selector.currentIndex() for selector in selectors]
    original_values = window.form_template().to_dict()
    original_detail = window.skill_detail_label.text()
    assert original_detail == "Select an active skill or passive to see its effect. Internal ID is shown secondarily."
    assert not window.ledger.dirty

    for selector in selectors:
        selector.setFocus()
        _app().processEvents()
        QTest.mouseClick(selector, Qt.MouseButton.LeftButton)
        _app().processEvents()
        assert selector.currentIndex() == original_indexes[selectors.index(selector)]
        assert window.form_template().to_dict() == original_values
        assert not window.ledger.dirty
        assert window.draft_status_label.text() == "Draft: Clean"
        assert window.skill_detail_label.text()
        if selector.currentData() == "__BLANK__":
            assert "Select an active skill" in window.skill_detail_label.text()

    window.close()


def test_skill_selector_value_change_keeps_existing_edit_behavior():
    window = _window_with_pal()
    window.ledger = OperationLedger(Path("selection-source.sav"))
    selector = window.active_selectors[0]
    original_index = selector.currentIndex()
    next_index = 1 if original_index != 1 else 2
    selector.setCurrentIndex(next_index)
    _app().processEvents()

    assert selector.currentIndex() == next_index
    assert window.ledger.dirty
    assert window.draft_status_label.text() != "Draft: Clean"
    assert "Active Skills:" in window.source_draft_label.text()
    assert window.skill_detail_label.text()
    selector.setCurrentIndex(original_index)
    _app().processEvents()
    assert not window.ledger.dirty
    window.close()


def test_overview_major_sections_keep_compact_spacing_and_technical_heading_visible():
    window = _window_with_pal()
    window.resize(980, 600)
    window.roster_detail_tabs.setCurrentIndex(0)
    _app().processEvents()
    window._fit_roster_editor()
    _app().processEvents()

    overview = window.roster_detail_tabs.currentWidget()
    groups = {
        group.title(): group
        for group in overview.findChildren(QGroupBox)
        if group.isVisible()
    }
    for upper, lower in (
        ("Pal record", "Quick stats"),
        ("Quick stats", "Skills and location"),
        ("Skills and location", "Pending changes"),
    ):
        gap = groups[lower].y() - groups[upper].geometry().bottom()
        assert 3 <= gap <= 9
    assert window.technical_toggle.isVisible()
    assert not window.roster_right_scroll.verticalScrollBar().isVisible()
    assert not window.roster_right_scroll.horizontalScrollBar().isVisible()
    window.close()


def test_technical_details_expansion_refits_only_the_visible_overview():
    window = _window_with_pal()
    window.resize(980, 600)
    window.roster_detail_tabs.setCurrentIndex(0)
    _app().processEvents()
    window._fit_roster_editor()
    _app().processEvents()

    assert not window.technical_details.isVisible()
    assert not window.roster_right_scroll.verticalScrollBar().isVisible()
    closed_height = window.roster_right_host.height()

    window.technical_toggle.setChecked(True)
    _app().processEvents()
    window._fit_roster_editor()
    _app().processEvents()
    assert window.technical_details.isVisible()
    assert window.roster_right_host.height() > closed_height
    assert not window.roster_right_scroll.horizontalScrollBar().isVisible()

    window.technical_toggle.setChecked(False)
    _app().processEvents()
    window._fit_roster_editor()
    _app().processEvents()
    assert not window.technical_details.isVisible()
    assert not window.roster_right_scroll.verticalScrollBar().isVisible()
    assert not window.roster_right_scroll.horizontalScrollBar().isVisible()
    window.close()


def test_roster_uses_narrow_window_vertical_fallback_only():
    window = _window_with_pal()
    window.resize(760, 480)
    _app().processEvents()

    assert not window.roster_right_scroll.horizontalScrollBar().isVisible()
    assert window.roster_right_scroll.verticalScrollBar().isVisible()
    window.close()


def test_blueprint_impact_is_exact_and_source_draft_state_is_shared():
    window = _window_with_pal()
    window.ledger = OperationLedger(Path("test-source.sav"))
    window.reference_only = False
    index = window.preset_combo.findData("combat_balanced")
    window.preset_combo.setCurrentIndex(index)
    _app().processEvents()

    assert window.apply_preset_button.isEnabled()
    impact = window.blueprint_impact.text()
    assert "Passive 1:" in impact
    assert "Validation: PASS" in impact
    assert "Compatibility:" in impact

    window.nickname_edit.setText("Draft Pal")
    _app().processEvents()
    assert "Nickname:" in window.source_draft_label.text()
    window.ledger.mark_clean()
    window.close()


def test_live_roster_is_read_only_without_outer_record_scroll():
    window = _window_with_pal()
    runtime_page = window.content_stack.widget(window._nav_pages[RUNTIME])
    titles = [group.title() for group in runtime_page.findChildren(QGroupBox)]

    assert "Live Pal record" in titles
    assert not runtime_page.findChildren(QScrollArea)
    assert window.runtime_mode_badge.text() == "LIVE · READ ONLY"
    window._set_runtime_skill_summary(
        window.runtime_active_count_label,
        window.runtime_active_label,
        ("16",),
    )
    assert window.runtime_active_count_label.text() == "1"
    assert window.runtime_active_label.text() == "Unavailable"
    assert "16" in window.runtime_active_label.toolTip()
    assert window.runtime_species_label.minimumHeight() >= 18
    assert window.runtime_passive_label.minimumHeight() >= 18
    detail = next(
        group for group in runtime_page.findChildren(QGroupBox) if group.title() == "Live Pal record"
    )
    assert detail.height() >= detail.layout().sizeHint().height()
    window.close()


def test_overview_summary_portrait_location_and_draft_reversal():
    window = _window_with_pal()
    window.instances = [
        PalInstance(
            template=PalTemplate(
                species="PinkCat",
                nickname="",
                level=2,
                rank=1,
                iv_hp=65,
                iv_attack=21,
                iv_defense=16,
                active_skills=["EPalWazaID::Unique_PinkCat_CatPunch"],
                passive_skills=["PAL_ALLAttack_down1"],
            ),
            instance_id="first",
            container_id="container-uuid",
            slot_index=3,
        ),
        PalInstance(
            template=PalTemplate(species="SheepBall", level=1),
            instance_id="second",
        ),
    ]
    window._refresh_roster_list(preferred_index=0)
    _app().processEvents()

    assert window.portrait_label.property("portrait_state") == "loaded"
    assert window.overview_active_label.text() == "Punch Flurry"
    assert window.overview_passive_label.text() == "Coward"
    assert window.overview_location_label.text() == "Stored container slot 3"
    assert not hasattr(window, "overview_container_label")
    assert window.source_draft_label.text() == "No pending changes. Draft matches the source."

    window._set_skill_summary(
        window.overview_passive_label,
        ["one", "two", "three", "four"],
        "No passive skills",
    )
    assert "+" not in window.overview_passive_label.text()
    assert window.overview_passive_label.text().count(",") == 3

    original_nickname = window.instances[0].template.nickname
    window.ledger = OperationLedger(Path("test-source.sav"))
    window.nickname_edit.setText("Draft Pal")
    _app().processEvents()
    assert "Nickname:" in window.source_draft_label.text()
    assert window.draft_status_label.text() == "Draft: 1 change"
    assert window.instances[0].template.nickname == original_nickname

    window.nickname_edit.clear()
    _app().processEvents()
    assert window.source_draft_label.text() == "No pending changes. Draft matches the source."
    assert window.draft_status_label.text() == "Draft: Clean"

    window.pal_list.setCurrentRow(1)
    _app().processEvents()
    assert window.portrait_label.property("portrait_state") == "loaded"
    assert window.overview_location_label.text() == "Unavailable"

    for index in range(window.roster_detail_tabs.count()):
        window.roster_detail_tabs.setCurrentIndex(index)
        _app().processEvents()
        assert not window.roster_right_scroll.horizontalScrollBar().isVisible()

    splitter_gap = window.roster_right_scroll.x() - (
        window.roster_left_panel.x() + window.roster_left_panel.width()
    )
    assert splitter_gap <= 12
    assert window.overview_active_label.width() >= 200
    assert window.overview_passive_label.width() >= 200

    window.resize(760, 480)
    _app().processEvents()
    assert not window.roster_right_scroll.horizontalScrollBar().isVisible()
    window.resize(1280, 800)
    _app().processEvents()
    assert not window.roster_right_scroll.horizontalScrollBar().isVisible()

    window.showMaximized()
    _app().processEvents()
    assert not window.roster_right_scroll.horizontalScrollBar().isVisible()
    window.showNormal()

    portrait_paths = window._portrait_paths
    window._portrait_paths = {}
    window._portrait_code = "PinkCat"
    window._load_portrait("PinkCat")
    assert window.portrait_label.property("portrait_state") == "missing"
    window._portrait_paths = portrait_paths

    window._portrait_code = "NotInCurrentCatalog"
    window._load_portrait("NotInCurrentCatalog")
    assert window.portrait_label.property("portrait_state") == "unknown"
    if window.ledger is not None:
        window.ledger.mark_clean()
    window.close()
