"""First desktop Pal editor UI.

Run with the project environment:
    tools\\venv-palworld\\Scripts\\python.exe -m pal_editor.gui
"""

from __future__ import annotations

import sys
import time
from dataclasses import fields, replace
from enum import Enum
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QEvent, QSettings, Qt, QSignalBlocker, QTimer, QUrl
from PySide6.QtGui import QAction, QActionGroup, QColor, QCloseEvent, QDesktopServices, QKeySequence, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QStatusBar,
    QScrollArea,
    QSizePolicy,
    QTabWidget,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .__main__ import inspect
from . import __version__
from .backup_store import RETENTION_LIMIT, default_backup_root
from .direct_save import DirectSaveCoordinator, DirectSaveRequest, validate_edit_batch
from .domain import PalInstance, PalTemplate
from .game_data import CatalogEntry, GameDataCatalog
from .ledger import BackupPolicy, OperationLedger
from .operations import BatchEdit, BatchEditResult, SaveEditError, edit_save_copy_batch
from .navigation import BLUEPRINTS, DEFAULT_NAVIGATION, LEDGER, ROSTER, RUNTIME, SETTINGS_DATA
from .presets import PRESETS, PresetScope, apply_preset, ordered_presets
from .runtime_monitor import (
    RuntimeSnapshotError,
    canonical_identity_key,
    load_runtime_snapshot,
)
from .save_locations import default_save_games_dir, find_latest_level_save
from .safety import GameSafetyStatus, get_game_safety_status
from .safe_save import FailureStage, RecoveryResult, SourceFingerprint, fingerprint_file
from .validation import validate_template


NAV_WIDTH = 150
SIDE_PANEL_MIN_WIDTH = 280
SIDE_PANEL_MAX_WIDTH = 340


SAVE_SAFETY_TEXT = """Before editing

Close Palworld before editing or saving. Pal Admin locks editing while Palworld is running. Work against the correct world's Level.sav.

Draft behavior

Changes remain pending until Save is completed. Multiple Pals may be edited before saving. Switching Pals does not discard pending edits. Review Changes shows the complete draft. Revert Draft discards the complete draft after confirmation.

Save

Save validates every edited Pal, creates a verified automatic backup, writes all pending edits in one transaction, and reparses and verifies the result before reporting success. Pal Admin keeps the draft if the operation fails or the result is uncertain.

Save a Copy

Save a Copy writes a separate edited file. The loaded source remains unchanged, the current draft remains pending, and a source safety copy may also be created beside the selected output.

Automatic backups

Automatic direct-Save backups are stored under:
%LOCALAPPDATA%\\PalAdmin\\Backups

The latest five verified backups are retained per source. Backup restoration remains a manual process in this version.

Practical advice

Keep an external copy of important worlds. Test major edits on a disposable copy first. Relaunch Palworld only after Pal Admin reports Save complete."""


ATTRIBUTION_TEXT = """Pal Admin
Independent Palworld save editor project. The project is distributed under the GNU General Public License, version 3 or later. Full license: licenses/palsav-flex-GPL-3.0-or-later.txt

palsav-flex
Palworld save parser and writer. Licensed under the GNU General Public License, version 3 or later. Full license: licenses/palsav-flex-GPL-3.0-or-later.txt

PalCalc portrait resources
Portrait assets sourced from PalCalc by Tyler Camp. Copyright 2024 Tyler Camp. Licensed under the MIT License. Full license: data/portraits/LICENSE.txt

PySide6
The desktop interface uses PySide6 under its applicable LGPL/GPL terms. The bundled Qt notices are included with the packaged runtime.

Palworld data and imagery
Pal Admin does not claim ownership of Palworld names, game-derived catalog data, or game-derived imagery. Palworld belongs to its respective rights holders. The application is not affiliated with Pocketpair.

Full project notices are included in licenses/THIRD_PARTY_NOTICES.md."""


ABOUT_TEXT = f"""Pal Admin

Pal Administration Suite v{__version__}

A standalone Palworld save editor for reviewing and editing Pal records.

Created by MalikenVonSonne

Direct Save uses verified backups and post-write validation.

Pal Admin is an independent, unofficial tool. It is not affiliated with or endorsed by Pocketpair. Palworld belongs to its respective rights holders.

Project: Pal Admin community project."""


class DirtyDraftDecision(str, Enum):
    SAVE = "save"
    DISCARD = "discard"
    CANCEL = "cancel"


class PalEditorWindow(QMainWindow):
    def __init__(self, initial_path: Path | None = None) -> None:
        super().__init__()
        self.setWindowTitle("Pal Admin: Roster Workbench")
        # Keep the first launch usable on 1080p displays and high-DPI
        # desktops.  The window remains freely resizable after launch.
        self.resize(980, 600)
        self.setMinimumSize(760, 480)
        self.source_path: Path | None = None
        self.source_baseline: SourceFingerprint | None = None
        self.target_path: Path | None = None
        self.current_path: Path | None = None  # compatibility alias during migration
        self.ledger: OperationLedger | None = None
        self.safety_status = GameSafetyStatus()
        self.app_settings = QSettings("Pal Admin", "Pal Admin")
        configured_dir = self.app_settings.value("save_games_dir", "", type=str)
        configured_path = self.app_settings.value("last_save_path", "", type=str)
        self.save_games_dir = Path(configured_dir) if configured_dir else default_save_games_dir()
        self.last_save_path = Path(configured_path) if configured_path else None
        self.reference_only = False
        self.records: list[dict] = []
        self.instances: list[PalInstance] = []
        self.current_index = -1
        self.current_instance_id: str | None = None
        self._suppress_selection_prompt = False
        self._suppress_form_sync = False
        self._direct_save_active = False
        self.direct_save_coordinator = DirectSaveCoordinator()
        self.last_direct_save_result = None
        self.runtime_snapshot = None
        self.runtime_selected_key: str | None = None
        self.runtime_collection_identity_keys: set[str] = set()
        self.catalog = GameDataCatalog.load()
        self.active_initial: list[str] = []
        self.passive_initial: list[str] = []
        self._species_loaded_code: str | None = None
        self._portrait_code: str | None = None
        self._portrait_paths = self._load_portrait_index()
        self._build_ui()
        self.runtime_timer = QTimer(self)
        self.runtime_timer.setInterval(1000)
        self.runtime_timer.timeout.connect(self.refresh_runtime_snapshot)
        self.runtime_timer.start()
        self.refresh_runtime_snapshot()
        if initial_path:
            self.load_path(initial_path)

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        self.setStyleSheet(
            """
            QMainWindow, QWidget { background: #17191a; color: #eee6d4; }
            QLabel { background: transparent; }
            QGroupBox {
                background: #242321; border: 1px solid #5a5144;
                border-radius: 10px; margin-top: 7px; padding: 5px;
            }
            QGroupBox::title {
                subcontrol-origin: margin; left: 12px; padding: 0 6px;
                color: #d9b56f; font-weight: 600;
            }
            QPushButton {
                background: #302f2b; border: 1px solid #6e6251;
                border-radius: 7px; padding: 6px 12px;
            }
            QPushButton:hover { background: #403a30; border-color: #d9b56f; }
            QPushButton:disabled { color: #77736c; border-color: #393735; }
            QListWidget, QLineEdit, QComboBox {
                background: #1f2121; border: 1px solid #514b42;
                border-radius: 6px; padding: 2px 4px;
            }
            QSpinBox {
                background: #2a2b29; border: 1px solid #665d4f;
                border-radius: 6px; padding: 1px 4px;
            }
            QSpinBox:focus {
                background: #30312e; border-color: #d9b56f;
            }
            QSpinBox QLineEdit {
                background: transparent; border: none; padding: 1px 2px;
            }
            QSpinBox::up-button, QSpinBox::down-button {
                background: #35352f; border-left: 1px solid #665d4f;
                width: 14px;
            }
            QSpinBox::up-button:hover, QSpinBox::down-button:hover {
                background: #4a463a;
            }
            QToolButton {
                background: transparent; border: none; color: #d9b56f;
                padding: 3px 2px; text-align: left;
            }
            QToolButton:hover { color: #f2d08b; }
            QListWidget::item { padding: 5px; }
            QListWidget::item:selected { background: #385246; color: #f5f1e6; }
            QComboBox QAbstractItemView { background: #242321; selection-background-color: #385246; }
            QTabWidget::pane { border: 1px solid #5a5144; border-radius: 8px; top: -1px; }
            QTabBar::tab { background: #242321; color: #b9b2a5; padding: 5px 10px; margin-right: 2px; }
            QTabBar::tab:selected { background: #385246; color: #f5f1e6; }
            QStatusBar { background: #111313; color: #a8c5af; }
            """
        )
        main = QVBoxLayout(central)

        self.safety_warning_banner = QLabel(
            "Palworld is running. Editing is locked and saving is unavailable until the game is closed."
        )
        self.safety_warning_banner.setObjectName("safetyWarningBanner")
        self.safety_warning_banner.setWordWrap(True)
        self.safety_warning_banner.setStyleSheet(
            "color: #f1c2a8; background: #3a2520; border: 1px solid #8f5b49; "
            "border-radius: 6px; padding: 7px 10px;"
        )
        self.safety_warning_banner.setToolTip(
            "Palworld must be closed before a loaded save can be edited, saved, or copied."
        )
        self.safety_warning_banner.setVisible(False)
        main.addWidget(self.safety_warning_banner)

        splitter = QSplitter(Qt.Horizontal)

        left = QWidget()
        left.setMinimumWidth(SIDE_PANEL_MIN_WIDTH)
        left.setMaximumWidth(SIDE_PANEL_MAX_WIDTH)
        self.roster_left_panel = left
        left_layout = QVBoxLayout(left)
        left_layout.addWidget(QLabel("Roster"))
        self.roster_search_edit = QLineEdit()
        self.roster_search_edit.setPlaceholderText("Search species, nickname, skill, passive…")
        self.roster_search_edit.setToolTip(
            "Search player-facing names, internal IDs, nicknames, active skills, and passives."
        )
        left_layout.addWidget(self.roster_search_edit)
        self.roster_filter_combo = QComboBox()
        self.roster_filter_combo.addItem("All Pals", "all")
        self.roster_filter_combo.addItem("Has nickname", "nickname")
        self.roster_filter_combo.addItem("Level 50+", "level_50")
        self.roster_filter_combo.addItem("High IV (80+)", "iv_80")
        self.roster_filter_combo.addItem("Has passives", "passives")
        left_layout.addWidget(self.roster_filter_combo)
        self.pal_list = QListWidget()
        self.pal_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        left_layout.addWidget(self.pal_list, 1)
        splitter.addWidget(left)

        right = QWidget()
        right.setMinimumWidth(0)
        right.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        detail_tabs = QTabWidget()
        detail_tabs.setDocumentMode(True)
        detail_tabs.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        identity_page = QWidget()
        identity_page_layout = QVBoxLayout(identity_page)
        identity_page_layout.setContentsMargins(6, 6, 6, 6)
        identity_page_layout.setSpacing(4)
        identity = QGroupBox("Pal record")
        identity.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        identity_layout = QHBoxLayout(identity)
        identity_layout.setContentsMargins(6, 6, 6, 6)
        identity_layout.setSpacing(10)
        self.portrait_label = QLabel("Loading portrait...")
        self.portrait_label.setObjectName("portrait")
        self.portrait_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.portrait_label.setMinimumSize(92, 92)
        self.portrait_label.setMaximumSize(116, 116)
        self.portrait_label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.portrait_label.setProperty("portrait_state", "loading")
        self.portrait_label.setToolTip("Species portrait")
        identity_layout.addWidget(self.portrait_label)
        identity_form = QFormLayout()
        identity_form.setVerticalSpacing(3)
        identity_form.setHorizontalSpacing(8)
        self.species_combo = self._selector()
        self.nickname_edit = QLineEdit()
        self.gender_combo = QComboBox()
        self.gender_combo.setMinimumWidth(0)
        self.gender_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.gender_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.gender_combo.setMinimumContentsLength(10)
        self.gender_combo.addItem("Unchanged / unknown", "")
        self.gender_combo.addItem("Male", "EPalGenderType::Male")
        self.gender_combo.addItem("Female", "EPalGenderType::Female")
        self.instance_label = QLabel("Unavailable")
        self.owner_label = QLabel("Unavailable")
        self.player_uid_label = QLabel("Unavailable")
        self.container_label = QLabel("Unavailable")
        self.slot_label = QLabel("Unavailable")
        for label in (
            self.instance_label,
            self.owner_label,
            self.player_uid_label,
            self.container_label,
            self.slot_label,
        ):
            label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
            label.setToolTip("Full value is retained; the field may clip at compact widths.")
        identity_form.addRow("Species", self.species_combo)
        identity_form.addRow("Nickname", self.nickname_edit)
        identity_form.addRow("Gender", self.gender_combo)
        identity_layout.addLayout(identity_form, 1)
        self._technical_copy_buttons: dict[QLabel, QPushButton] = {}
        identity_page_layout.addWidget(identity)
        identity_page_layout.addStretch()
        detail_tabs.addTab(identity_page, "Overview")

        build_page = QWidget()
        build_page_layout = QVBoxLayout(build_page)
        build_page_layout.setContentsMargins(4, 4, 4, 4)
        build_page_layout.setSpacing(4)

        blueprint_section = QGroupBox("Apply Blueprint")
        blueprint_form = QFormLayout(blueprint_section)
        blueprint_form.setVerticalSpacing(3)
        blueprint_form.setHorizontalSpacing(8)
        blueprint_explanation = QLabel(
            "Apply a saved role blueprint, such as Worker, Combat, Mount, Ranch, or Breeding, to the selected Pal."
        )
        blueprint_explanation.setWordWrap(True)
        blueprint_explanation.setStyleSheet("color: #a8c5af; padding: 2px 0 4px;")
        blueprint_form.addRow(blueprint_explanation)
        self.level_spin = self._spin(1, 80)
        self.rank_spin = self._spin(0, 5)
        self.iv_hp_spin = self._spin(0, 100)
        self.iv_attack_spin = self._spin(0, 100)
        self.iv_defense_spin = self._spin(0, 100)

        quick_stats = QGroupBox("Quick stats")
        quick_stats.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        quick_grid = QGridLayout(quick_stats)
        self.overview_level_label = QLabel("Unavailable")
        self.overview_rank_label = QLabel("Unavailable")
        self.overview_iv_hp_label = QLabel("Unavailable")
        self.overview_iv_attack_label = QLabel("Unavailable")
        self.overview_iv_defense_label = QLabel("Unavailable")
        quick_values = (
            ("Level", self.overview_level_label, 0, 0),
            ("Rank", self.overview_rank_label, 0, 2),
            ("HP IV", self.overview_iv_hp_label, 1, 0),
            ("Attack IV", self.overview_iv_attack_label, 1, 2),
            ("Defense IV", self.overview_iv_defense_label, 2, 0),
        )
        for label_text, value_label, row, column in quick_values:
            quick_grid.addWidget(QLabel(label_text), row, column)
            quick_grid.addWidget(value_label, row, column + 1)
        identity_page_layout.insertWidget(1, quick_stats)

        summary_section = QGroupBox("Skills and location")
        summary_grid = QGridLayout(summary_section)
        summary_grid.setVerticalSpacing(3)
        summary_grid.setHorizontalSpacing(8)
        self.overview_active_label = QLabel("Unavailable")
        self.overview_passive_label = QLabel("Unavailable")
        self.overview_location_label = QLabel("Unavailable")
        for label in (
            self.overview_active_label,
            self.overview_passive_label,
            self.overview_location_label,
        ):
            label.setWordWrap(True)
            label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        # Skill summaries get the full editor width.  Keeping them beside
        # Location made each value column too narrow for real loadouts even
        # after the editor reclaimed the splitter's unused space.
        summary_grid.addWidget(QLabel("Active skills"), 0, 0)
        summary_grid.addWidget(self.overview_active_label, 0, 1, 1, 3)
        summary_grid.addWidget(QLabel("Passive skills"), 1, 0)
        summary_grid.addWidget(self.overview_passive_label, 1, 1, 1, 3)
        summary_grid.addWidget(QLabel("Location"), 2, 0)
        summary_grid.addWidget(self.overview_location_label, 2, 1, 1, 3)
        summary_grid.setColumnStretch(1, 1)
        identity_page_layout.insertWidget(2, summary_section)

        pending_section = QGroupBox("Pending changes")
        pending_layout = QVBoxLayout(pending_section)
        pending_layout.setContentsMargins(8, 7, 8, 7)
        self.source_draft_label = QLabel("No pending changes. Draft matches the source.")
        self.source_draft_label.setWordWrap(True)
        self.source_draft_label.setToolTip(
            "Changed fields show the loaded source value followed by the current draft value."
        )
        self.source_draft_label.setStyleSheet("color: #a8c5af; padding: 1px 0;")
        pending_layout.addWidget(self.source_draft_label)
        identity_page_layout.insertWidget(3, pending_section)

        technical_toggle = QToolButton()
        technical_toggle.setText("Technical details")
        technical_toggle.setCheckable(True)
        technical_toggle.setChecked(False)
        technical_toggle.setArrowType(Qt.RightArrow)
        technical_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        technical_details = QGroupBox()
        technical_form = QFormLayout(technical_details)
        technical_form.setVerticalSpacing(3)
        technical_form.setHorizontalSpacing(8)
        for label_text, value_label in (
            ("Instance", self.instance_label),
            ("Owner", self.owner_label),
            ("Player UID", self.player_uid_label),
            ("Container", self.container_label),
            ("Slot", self.slot_label),
        ):
            technical_form.addRow(label_text, self._technical_value_row(value_label))
        technical_details.setVisible(False)
        technical_toggle.toggled.connect(self._toggle_technical_details)
        self.technical_toggle = technical_toggle
        self.technical_details = technical_details
        identity_page_layout.insertWidget(4, technical_toggle)
        identity_page_layout.insertWidget(5, technical_details)

        self.preset_combo = QComboBox()
        self.preset_combo.setMinimumWidth(0)
        self.preset_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.preset_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.preset_combo.setMinimumContentsLength(8)
        self.preset_combo.addItem("Choose a blueprint…", "")
        self.preset_combo.setItemData(0, "Choose a blueprint...", Qt.ItemDataRole.ToolTipRole)
        for preset in ordered_presets():
            self.preset_combo.addItem(preset.label, preset.key)
            self.preset_combo.setItemData(
                self.preset_combo.count() - 1,
                preset.label,
                Qt.ItemDataRole.ToolTipRole,
            )
        self.preset_combo.view().setTextElideMode(Qt.TextElideMode.ElideRight)
        longest_blueprint = max(
            (self.preset_combo.fontMetrics().horizontalAdvance(self.preset_combo.itemText(index))
             for index in range(self.preset_combo.count())),
            default=240,
        )
        self.preset_combo.view().setMinimumWidth(min(longest_blueprint + 48, 900))
        self.apply_preset_button = QPushButton("Apply Blueprint")
        self.apply_preset_button.setEnabled(False)
        preset_row = QWidget()
        preset_layout = QHBoxLayout(preset_row)
        preset_layout.setContentsMargins(0, 0, 0, 0)
        preset_layout.addWidget(self.preset_combo, 1)
        preset_layout.addWidget(self.apply_preset_button)
        blueprint_form.addRow("Blueprint", preset_row)
        scope = QWidget()
        scope_layout = QGridLayout(scope)
        scope_layout.setContentsMargins(0, 0, 0, 0)
        self.scope_level = QCheckBox("Level")
        self.scope_rank = QCheckBox("Rank")
        self.scope_attributes = QCheckBox("Apply IVs")
        self.scope_active_skills = QCheckBox("Active Skills")
        self.scope_passives = QCheckBox("Passives")
        # Compatibility alias for existing signal and enablement code.
        self.scope_skills = self.scope_passives
        self.scope_level.setToolTip("Allow this blueprint to change the Pal level.")
        self.scope_rank.setToolTip("Allow a rank-aware blueprint to change the Pal rank.")
        self.scope_attributes.setToolTip("Allow this blueprint to change IVs and related attributes.")
        self.scope_active_skills.setToolTip("Allow this blueprint to replace the active skill loadout.")
        self.scope_passives.setToolTip("Allow this blueprint to replace the passive loadout.")
        scope_layout.addWidget(self.scope_level, 0, 0)
        scope_layout.addWidget(self.scope_rank, 0, 1)
        scope_layout.addWidget(self.scope_attributes, 0, 2)
        scope_layout.addWidget(self.scope_active_skills, 1, 0)
        scope_layout.addWidget(self.scope_passives, 1, 1)
        blueprint_form.addRow("Include from blueprint", scope)
        self.blueprint_impact = QLabel("Select a Pal and blueprint to see exact pending changes.")
        self.blueprint_impact.setWordWrap(True)
        self.blueprint_impact.setMinimumHeight(40)
        self.blueprint_impact.setMinimumWidth(0)
        self.blueprint_impact.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.blueprint_impact.setStyleSheet("color: #a8c5af; padding: 4px;")
        blueprint_form.addRow("Impact", self.blueprint_impact)
        build_page_layout.addWidget(blueprint_section)

        # Qt treats a single ampersand as a mnemonic marker.  Double it so
        # the literal ampersand remains visible in the rendered heading.
        manual = QGroupBox("Manual Progression && IVs")
        manual.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        manual_form = QGridLayout(manual)
        manual_form.setVerticalSpacing(3)
        manual_form.setHorizontalSpacing(8)
        manual_form.setColumnStretch(1, 1)
        manual_form.setColumnStretch(3, 1)
        for row, left_label, left_widget, right_label, right_widget in (
            (0, "Level", self.level_spin, "Rank", self.rank_spin),
            (1, "HP IV", self.iv_hp_spin, "Attack IV", self.iv_attack_spin),
        ):
            manual_form.addWidget(QLabel(left_label), row, 0)
            manual_form.addWidget(left_widget, row, 1)
            manual_form.addWidget(QLabel(right_label), row, 2)
            manual_form.addWidget(right_widget, row, 3)
        manual_form.addWidget(QLabel("Defense IV"), 2, 0)
        manual_form.addWidget(self.iv_defense_spin, 2, 1)
        build_page_layout.addWidget(manual)
        build_page_layout.addStretch()
        detail_tabs.addTab(build_page, "Build")

        skills_page = QWidget()
        skills_page_layout = QVBoxLayout(skills_page)
        skills_page_layout.setContentsMargins(2, 2, 2, 2)
        skills_page_layout.setSpacing(7)
        active_section = QGroupBox("Active Skills")
        active_section.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        active_layout = QFormLayout(active_section)
        active_layout.setVerticalSpacing(2)
        active_layout.setHorizontalSpacing(8)
        passive_section = QGroupBox("Passives")
        passive_section.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        passive_layout = QFormLayout(passive_section)
        passive_layout.setVerticalSpacing(2)
        passive_layout.setHorizontalSpacing(8)
        self.active_selectors = [self._selector() for _ in range(3)]
        self.passive_selectors = [self._selector() for _ in range(4)]
        for selector in (*self.active_selectors, *self.passive_selectors):
            selector.setFixedHeight(24)
        self._populate_selector(
            self.species_combo, self.catalog.pals, "Select a species…", "", show_code=True
        )
        for selector in self.active_selectors:
            self._populate_selector(selector, self.catalog.attacks, "Leave slot blank", "__BLANK__")
        for selector in self.passive_selectors:
            self._populate_selector(
                selector,
                self.catalog.standard_passives,
                "Leave slot blank",
                "__BLANK__",
            )
        for index, selector in enumerate(self.active_selectors, 1):
            active_layout.addRow(f"Active Skill {index}", selector)
        for index, selector in enumerate(self.passive_selectors, 1):
            passive_layout.addRow(f"Passive {index}", selector)
        skills_page_layout.addWidget(active_section)
        skills_page_layout.addWidget(passive_section)
        passive_options = QHBoxLayout()
        self.advanced_passives_check = QCheckBox("Show advanced/internal passives")
        self.advanced_passives_check.setToolTip(
            "Include special, partner, equipment, and other internal passive records. "
            "Leave this off for the normal player-facing Pal passive list."
        )
        passive_options.addWidget(self.advanced_passives_check)
        self.passive_catalog_label = QLabel(
            f"{len(self.catalog.standard_passives)} standard passives"
        )
        self.passive_catalog_label.setStyleSheet("color: #a8c5af; padding: 2px;")
        passive_options.addWidget(self.passive_catalog_label)
        passive_options.addStretch()
        passive_layout.addRow(passive_options)
        skill_details = QGroupBox("Selection details")
        skill_details.setSizePolicy(
            QSizePolicy.Policy.Ignored,
            QSizePolicy.Policy.Expanding,
        )
        skill_details.setMinimumHeight(80)
        skill_details_layout = QVBoxLayout(skill_details)
        skill_details_layout.setContentsMargins(8, 4, 8, 4)
        skill_details_layout.setSpacing(0)
        self.skill_detail_label = QLabel(
            "Select an active skill or passive to see its effect. Internal ID is shown secondarily."
        )
        self.skill_detail_label.setWordWrap(True)
        self.skill_detail_label.setMinimumHeight(24)
        self.skill_detail_label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.skill_detail_label.setStyleSheet("color: #a8c5af; font-size: 13px; padding: 0;")
        self.skill_detail_scroll = QScrollArea()
        self.skill_detail_scroll.setWidgetResizable(True)
        self.skill_detail_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.skill_detail_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.skill_detail_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.skill_detail_scroll.setMinimumHeight(56)
        self.skill_detail_scroll.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        self.skill_detail_scroll.setWidget(self.skill_detail_label)
        skill_details_layout.addWidget(self.skill_detail_scroll, 1)
        self.skill_details = skill_details
        skills_page_layout.addWidget(skill_details, 1)
        # Escape the mnemonic marker so the tab visibly renders "Skills & Passives".
        detail_tabs.addTab(skills_page, "Skills && Passives")
        catalog_text = (
            f"Data ledger: {self.catalog.source_label}\n"
            f"Loaded {len(self.catalog.pals)} species, {len(self.catalog.attacks)} attacks, "
            f"and {len(self.catalog.passives)} passives."
        )
        if self.catalog.warnings:
            catalog_text += "\nCatalog warnings: " + " | ".join(self.catalog.warnings)
        catalog_summary = (
            f"Catalog: Palworld 1.0 · {len(self.catalog.pals)} species · "
            f"{len(self.catalog.attacks)} attacks · {len(self.catalog.passives)} passives"
        )
        self.catalog_label = QLabel(catalog_summary)
        self.catalog_label.setWordWrap(False)
        self.catalog_label.setMaximumHeight(28)
        self.catalog_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.catalog_label.setToolTip(catalog_text)
        self.catalog_label.setStyleSheet("color: #6b7280; padding: 4px;")
        self.roster_detail_tabs = detail_tabs
        self.roster_empty_page = QWidget()
        empty_layout = QVBoxLayout(self.roster_empty_page)
        empty_heading = QLabel("Roster ready")
        empty_heading.setStyleSheet("font-size: 20px; color: #d9b56f; font-weight: 600;")
        empty_layout.addWidget(empty_heading)
        empty_layout.addWidget(
            QLabel(
                "Load a Palworld Level.sav to populate the roster. Save writes pending edits to the "
                "loaded source after a verified automatic backup. While Palworld is running, editing "
                "and saving are locked."
            )
        )
        self.empty_save_path_label = QLabel(str(self.last_save_path or self.save_games_dir))
        self.empty_save_path_label.setWordWrap(True)
        self.empty_save_path_label.setStyleSheet("color: #a8c5af; padding: 6px 0;")
        empty_layout.addWidget(self.empty_save_path_label)
        self.empty_open_button = QPushButton("Open source save")
        self.empty_open_button.setMaximumWidth(300)
        self.empty_open_button.setSizePolicy(
            QSizePolicy.Policy.Fixed,
            QSizePolicy.Policy.Fixed,
        )
        empty_layout.addWidget(self.empty_open_button, 0, Qt.AlignmentFlag.AlignLeft)
        empty_layout.addWidget(
            QLabel("Tip: Settings / Data remembers the save folder and can detect the latest Level.sav.")
        )
        empty_layout.addStretch()
        for label in self.roster_empty_page.findChildren(QLabel):
            label.setWordWrap(True)
            label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.roster_detail_stack = QStackedWidget()
        self.roster_detail_stack.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        self.roster_empty_page.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self.roster_detail_stack.addWidget(self.roster_empty_page)
        self.roster_detail_stack.addWidget(self.roster_detail_tabs)
        self.roster_detail_stack.setCurrentWidget(self.roster_empty_page)
        self.roster_detail_tabs.currentChanged.connect(
            lambda _index: self._schedule_roster_fit()
        )
        right_layout.addWidget(self.roster_detail_stack, 1)
        # Keep the editor bound to the available client area.  The individual
        # tabs own their content and may scroll only where that content is
        # genuinely longer than the compact window.
        right.setMinimumHeight(0)
        right.setMinimumWidth(0)
        right_scroll = QScrollArea()
        # The host height is managed from the viewport below.  This avoids
        # QScrollArea choosing the stacked widget's stale size hint after the
        # placeholder page is replaced by the editor tabs.
        right_scroll.setWidgetResizable(False)
        right_scroll.setFrameShape(QFrame.Shape.NoFrame)
        right_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        right_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        right_scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        right_scroll.setWidget(right)
        self.roster_right_scroll = right_scroll
        self.roster_right_host = right
        splitter.addWidget(right_scroll)
        splitter.setChildrenCollapsible(False)
        # The platform style can give the splitter handle a very wide gutter.
        # Keep the roster column stable while reclaiming that unused space for
        # the complete editor, with a small intentional separation remaining.
        splitter.setHandleWidth(8)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        splitter.setSizes([SIDE_PANEL_MAX_WIDTH, 780])

        work_area = QHBoxLayout()
        self.nav_list = QListWidget()
        self.nav_list.setFixedWidth(NAV_WIDTH)
        self.nav_list.setToolTip("Pal Admin work areas")
        self._nav_pages: dict[str, int] = {ROSTER: 0}
        for destination in DEFAULT_NAVIGATION.all_destinations:
            if destination.key == BLUEPRINTS:
                # Blueprint application is part of Roster > Build.  Keep the
                # navigation metadata for a future dedicated workspace, but
                # do not expose an unfinished placeholder in the release UI.
                continue
            item = QListWidgetItem(destination.label)
            item.setData(Qt.UserRole, destination.key)
            item.setToolTip(destination.description)
            item.setForeground(QColor("#eee6d4"))
            item.setBackground(QColor("#1f2121"))
            self.nav_list.addItem(item)

        self.content_stack = QStackedWidget()
        self.content_stack.addWidget(splitter)
        for destination in DEFAULT_NAVIGATION.all_destinations:
            if destination.key in {ROSTER, BLUEPRINTS}:
                continue
            if destination.key == LEDGER:
                page = self._ledger_page()
            elif destination.key == RUNTIME:
                page = self._runtime_page()
            elif destination.key == SETTINGS_DATA:
                page = self._settings_page()
            else:
                page = self._placeholder_page(destination.label, destination.description)
            self._nav_pages[destination.key] = self.content_stack.addWidget(page)
        work_area.addWidget(self.nav_list)
        work_area.addWidget(self.content_stack, 1)
        main.addLayout(work_area, 1)

        status_bar = QStatusBar()
        self.setStatusBar(status_bar)
        self.status_guidance_label = QLabel(DEFAULT_NAVIGATION.resolve(ROSTER).description)
        self.status_guidance_label.setObjectName("statusGuidance")
        self.status_guidance_label.setToolTip("Current page guidance")
        status_bar.addWidget(self.status_guidance_label, 1)
        self.draft_status_label = self._status_state_label("Draft: Clean")
        self.draft_status_label.setObjectName("draftStatus")
        self.palworld_status_label = self._status_state_label("Palworld: Closed")
        self.palworld_status_label.setObjectName("palworldStatus")
        self.source_status_label = self._status_state_label("Source: None")
        self.source_status_label.setObjectName("sourceStatus")
        status_bar.addPermanentWidget(self.draft_status_label)
        status_bar.addPermanentWidget(self.palworld_status_label)
        status_bar.addPermanentWidget(self.source_status_label)
        self._build_actions()
        self._build_menus()
        self.apply_preset_button.clicked.connect(self.apply_selected_preset)
        self.preset_combo.currentIndexChanged.connect(self.update_blueprint_scope)
        self.scope_level.stateChanged.connect(self.refresh_blueprint_impact)
        self.scope_rank.stateChanged.connect(self.refresh_blueprint_impact)
        self.scope_attributes.stateChanged.connect(self.refresh_blueprint_impact)
        self.scope_active_skills.stateChanged.connect(self.refresh_blueprint_impact)
        self.scope_passives.stateChanged.connect(self.refresh_blueprint_impact)
        self.pal_list.currentRowChanged.connect(self.select_pal)
        self.roster_search_edit.textChanged.connect(self.filter_roster)
        self.roster_filter_combo.currentIndexChanged.connect(self.filter_roster)
        self.nav_list.currentRowChanged.connect(self.navigate)
        self.species_combo.currentIndexChanged.connect(self._sync_draft_from_form)
        self.species_combo.currentIndexChanged.connect(self._refresh_portrait_for_current_species)
        self.nickname_edit.textChanged.connect(self._sync_draft_from_form)
        self.gender_combo.currentIndexChanged.connect(self._sync_draft_from_form)
        for spin in (self.level_spin, self.rank_spin, self.iv_hp_spin, self.iv_attack_spin, self.iv_defense_spin):
            spin.valueChanged.connect(self._sync_draft_from_form)
        for selector in (*self.active_selectors, *self.passive_selectors):
            selector.currentIndexChanged.connect(self._sync_draft_from_form)
            selector.currentIndexChanged.connect(self.refresh_skill_detail)
            selector.installEventFilter(self)
            selector.lineEdit().installEventFilter(self)
        self.advanced_passives_check.toggled.connect(self._refresh_passive_selectors)
        self.update_blueprint_scope(0)
        self.nav_list.setCurrentRow(0)
        self._refresh_safety_status()
        self._refresh_ledger_page()
        self._update_action_states()

    @staticmethod
    def _status_state_label(text: str) -> QLabel:
        label = QLabel(text)
        label.setStyleSheet("color: #a8c5af; padding: 0 6px;")
        label.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred)
        return label

    def _show_text_dialog(
        self,
        title: str,
        text: str,
        *,
        minimum_size: tuple[int, int] = (700, 520),
    ) -> None:
        """Show selectable, copyable long-form user-facing information."""
        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        dialog.setMinimumSize(*minimum_size)
        layout = QVBoxLayout(dialog)
        editor = QTextEdit()
        editor.setReadOnly(True)
        editor.setPlainText(text)
        editor.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.TextSelectableByKeyboard
        )
        layout.addWidget(editor)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        buttons.accepted.connect(dialog.accept)
        layout.addWidget(buttons)
        dialog.exec()

    def show_save_safety(self) -> None:
        self._show_text_dialog("Save Safety", SAVE_SAFETY_TEXT)

    def show_attribution(self) -> None:
        self._show_text_dialog("Attribution and Licenses", ATTRIBUTION_TEXT)

    def show_about(self) -> None:
        self._show_text_dialog("About Pal Admin", ABOUT_TEXT, minimum_size=(620, 360))

    def _build_actions(self) -> None:
        """Create the Phase 1 action source of truth without adding menus yet."""

        self._shared_actions: dict[str, QAction] = {}
        self._action_handler_names: dict[str, str] = {}
        self._action_button_bindings: dict[str, list[QPushButton]] = {}

        self.action_open_source_save = self._new_action(
            "action_open_source_save",
            "Open Source Save...",
            self.open_save,
            "Ctrl+O",
        )
        self.action_open_latest_save = self._new_action(
            "action_open_latest_save",
            "Open Latest Detected Save",
            self.open_latest_detected_save,
        )
        self.action_reload_source = self._new_action(
            "action_reload_source",
            "Reload Source Save",
            self.reload,
        )
        self.action_save = self._new_action(
            "action_save",
            "Save",
            self.save,
            "Ctrl+S",
        )
        self.action_create_save_copy = self._new_action(
            "action_create_save_copy",
            "Save a Copy...",
            self.save_copy,
            "Ctrl+Shift+S",
        )
        self.action_exit = self._new_action(
            "action_exit",
            "Exit",
            self.close,
        )
        self.action_review_changes = self._new_action(
            "action_review_changes",
            "Review Changes...",
            self.preview_changes,
        )
        self.action_revert_draft = self._new_action(
            "action_revert_draft",
            "Revert Draft...",
            self.revert_draft,
        )
        self.action_refresh_snapshot = self._new_action(
            "action_refresh_snapshot",
            "Refresh Snapshot",
            self.refresh_runtime_snapshot,
        )
        self.action_save_safety = self._new_action(
            "action_save_safety",
            "Save Safety...",
            self.show_save_safety,
        )
        self.action_attribution = self._new_action(
            "action_attribution",
            "Attribution and Licenses...",
            self.show_attribution,
        )
        self.action_about = self._new_action(
            "action_about",
            "About Pal Admin...",
            self.show_about,
        )

        self.action_contextual_refresh = self._new_action(
            "action_contextual_refresh",
            "Contextual Refresh",
            self._contextual_refresh,
            "F5",
        )
        self.action_contextual_refresh.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
        self.addAction(self.action_contextual_refresh)

        self.view_action_group = QActionGroup(self)
        self.view_action_group.setExclusive(True)
        for key, attribute, label in (
            (ROSTER, "action_view_roster", "Roster"),
            (RUNTIME, "action_view_runtime", "Live Roster"),
            (LEDGER, "action_view_ledger", "Ledger"),
            (SETTINGS_DATA, "action_view_settings", "Settings / Data"),
        ):
            action = QAction(label, self)
            action.setObjectName(attribute)
            action.setCheckable(True)
            action.setData(key)
            action.triggered.connect(
                lambda _checked=False, destination=key: self._navigate_from_action(destination)
            )
            self.view_action_group.addAction(action)
            setattr(self, attribute, action)
            self._shared_actions[attribute] = action
            self._action_handler_names[attribute] = "navigate"

        self._bind_action_button(self.empty_open_button, self.action_open_source_save)
        self._bind_action_button(self.runtime_refresh_button, self.action_refresh_snapshot)
        self._bind_action_button(self.open_latest_button, self.action_open_latest_save)
        self._bind_action_button(self.revert_draft_button, self.action_revert_draft)

    def _build_menus(self) -> None:
        """Expose the Phase 1 actions without changing the existing shell."""
        self.file_menu = self.menuBar().addMenu("File")
        self.file_menu.addAction(self.action_open_source_save)
        self.file_menu.addAction(self.action_open_latest_save)
        self.file_menu.addAction(self.action_reload_source)
        self.file_menu.addSeparator()
        self.file_menu.addAction(self.action_save)
        self.file_menu.addAction(self.action_create_save_copy)
        self.file_menu.addSeparator()
        self.file_menu.addAction(self.action_exit)

        self.edit_menu = self.menuBar().addMenu("Edit")
        self.edit_menu.addAction(self.action_review_changes)
        self.edit_menu.addAction(self.action_revert_draft)

        self.view_menu = self.menuBar().addMenu("View")
        for action in (
            self.action_view_roster,
            self.action_view_runtime,
            self.action_view_ledger,
            self.action_view_settings,
        ):
            self.view_menu.addAction(action)

        self.help_menu = self.menuBar().addMenu("Help")
        self.help_menu.addAction(self.action_save_safety)
        self.help_menu.addSeparator()
        self.help_menu.addAction(self.action_attribution)
        self.help_menu.addAction(self.action_about)

    def _new_action(
        self,
        object_name: str,
        text: str,
        callback: object,
        shortcut: str | None = None,
    ) -> QAction:
        action = QAction(text, self)
        action.setObjectName(object_name)
        if shortcut:
            action.setShortcut(QKeySequence(shortcut))
        self._action_handler_names[object_name] = getattr(callback, "__name__", str(callback))
        action.triggered.connect(
            lambda _checked=False, handler=callback: handler()  # type: ignore[operator]
        )
        self._shared_actions[object_name] = action
        return action

    def _bind_action_button(self, button: QPushButton, action: QAction) -> None:
        """Route a retained button through the same QAction as future menus."""
        button.clicked.connect(lambda _checked=False, shared=action: shared.trigger())
        self._action_button_bindings.setdefault(action.objectName(), []).append(button)

    def _navigate_from_action(self, key: str) -> None:
        for row in range(self.nav_list.count()):
            if self.nav_list.item(row).data(Qt.UserRole) == key:
                if self.nav_list.currentRow() == row:
                    self.navigate(row)
                else:
                    self.nav_list.setCurrentRow(row)
                return

    def _contextual_refresh(self) -> None:
        key = self._current_navigation_key()
        if key in {ROSTER, LEDGER}:
            self.reload()
        elif key == RUNTIME:
            self.refresh_runtime_snapshot()

    def _current_navigation_key(self) -> str:
        item = self.nav_list.currentItem()
        return str(item.data(Qt.UserRole)) if item is not None else ROSTER

    def _update_action_states(self) -> None:
        """Fan out current state to shared actions and retained buttons."""
        if not hasattr(self, "_shared_actions"):
            return
        self._refresh_global_shell_state()

        visible_pals = bool(getattr(self, "pal_list", None) and self.pal_list.count())
        can_edit = bool(self.instances) and visible_pals and not self.reference_only
        can_revert = bool(self.ledger is not None and self.ledger.dirty)
        can_save = self._can_direct_save(can_edit=can_edit)

        enabled = {
            "action_open_source_save": True,
            "action_open_latest_save": True,
            "action_reload_source": self.source_path is not None,
            "action_save": can_save,
            "action_create_save_copy": can_edit,
            "action_exit": True,
            "action_review_changes": can_edit,
            "action_revert_draft": can_revert,
            "action_refresh_snapshot": True,
            "action_contextual_refresh": self._current_navigation_key()
            in {ROSTER, RUNTIME, LEDGER},
            "action_view_roster": True,
            "action_view_runtime": True,
            "action_view_ledger": True,
            "action_view_settings": True,
        }
        for object_name, is_enabled in enabled.items():
            action = self._shared_actions[object_name]
            action.setEnabled(bool(is_enabled))
            for button in self._action_button_bindings.get(object_name, []):
                button.setEnabled(bool(is_enabled))

        current_key = self._current_navigation_key()
        for key, action in (
            (ROSTER, self.action_view_roster),
            (RUNTIME, self.action_view_runtime),
            (LEDGER, self.action_view_ledger),
            (SETTINGS_DATA, self.action_view_settings),
        ):
            action.setChecked(current_key == key)

    def _can_direct_save(self, *, can_edit: bool | None = None) -> bool:
        """Return the same truthful availability used by the Save action."""

        if can_edit is None:
            visible_pals = bool(getattr(self, "pal_list", None) and self.pal_list.count())
            can_edit = bool(self.instances) and visible_pals and not self.reference_only
        return bool(
            can_edit
            and self.source_path is not None
            and self.source_baseline is not None
            and self.ledger is not None
            and self.ledger.dirty
            and self.safety_status.safe_for_offline_editing
            and not self._direct_save_active
        )

    @staticmethod
    def _draft_operation_label(operation: str) -> str:
        return {
            "open_source": "opening another source save",
            "open_latest": "opening the latest detected source save",
            "reload": "reloading the source save",
            "exit": "exiting Pal Admin",
        }.get(operation, "continuing")

    def _prompt_dirty_draft(
        self,
        operation: str,
        *,
        save_available: bool,
    ) -> DirtyDraftDecision:
        """Ask once how a dirty draft should be handled before replacement/exit."""

        count = self.ledger.total_changed_field_count if self.ledger is not None else 0
        edited_pals = self.ledger.pending_pal_count if self.ledger is not None else 0
        destination = self._draft_operation_label(operation)
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Unsaved changes")
        context = (
            f"{count} pending change{'s' if count != 1 else ''} across "
            f"{edited_pals} edited Pal{'s' if edited_pals != 1 else ''}."
        )
        if save_available:
            text = f"You have unsaved changes. Save them before {destination}?\n\n{context}"
        else:
            text = (
                f"You have unsaved changes, but Save is unavailable while Palworld is running or "
                f"editing is locked. Choose whether to discard them before {destination}.\n\n{context}"
            )
        box.setText(text)
        if save_available:
            save_button = box.addButton("Save", QMessageBox.ButtonRole.AcceptRole)
        else:
            save_button = None
        discard_button = box.addButton(
            "Discard Changes", QMessageBox.ButtonRole.DestructiveRole
        )
        cancel_button = box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(cancel_button)
        box.setEscapeButton(cancel_button)
        box.exec()
        clicked = box.clickedButton()
        if save_button is not None and clicked is save_button:
            return DirtyDraftDecision.SAVE
        if clicked is discard_button:
            return DirtyDraftDecision.DISCARD
        return DirtyDraftDecision.CANCEL

    def _guard_pending_operation(
        self,
        operation: str,
        continuation: Callable[[], bool],
    ) -> bool:
        """Centralize dirty-draft protection for source replacement and exit."""

        if self._direct_save_active:
            QMessageBox.warning(
                self,
                "Save in progress",
                "A direct Save is still in progress. Save must finish before this operation can continue.",
            )
            return False

        if self.ledger is None or not self.ledger.dirty:
            return bool(continuation())

        # Refresh the lock immediately before presenting choices so a game
        # launched since the last action-state update cannot expose Save.
        self._refresh_safety_status()
        save_available = self._can_direct_save()
        decision = self._prompt_dirty_draft(
            operation,
            save_available=save_available,
        )
        if decision is DirtyDraftDecision.CANCEL:
            return False
        if decision is DirtyDraftDecision.DISCARD:
            return bool(continuation())
        if not save_available:
            return False
        if not self.save():
            return False
        return bool(continuation())

    def _runtime_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        heading_row = QHBoxLayout()
        heading = QLabel("Live Roster")
        heading.setStyleSheet("font-size: 22px; color: #d9b56f; font-weight: 600;")
        heading_row.addWidget(heading)
        self.runtime_mode_badge = QLabel("LIVE · READ ONLY")
        self.runtime_mode_badge.setStyleSheet(
            "color: #8fd3b0; border: 1px solid #385246; border-radius: 5px; padding: 3px 7px;"
        )
        heading_row.addWidget(self.runtime_mode_badge)
        heading_row.addStretch()
        self.runtime_refresh_button = QPushButton("Refresh snapshot")
        heading_row.addWidget(self.runtime_refresh_button)
        layout.addLayout(heading_row)

        self.runtime_status_label = QLabel("Waiting for Palworld and UE4SS…")
        self.runtime_status_label.setWordWrap(True)
        self.runtime_status_label.setStyleSheet("color: #a8c5af; padding: 4px 0;")
        layout.addWidget(self.runtime_status_label)
        self.runtime_path_label = QLabel("Bridge: %LOCALAPPDATA%\\PalAdmin\\runtime_snapshot.json")
        self.runtime_path_label.setStyleSheet("color: #6b7280; padding-bottom: 6px;")
        layout.addWidget(self.runtime_path_label)

        splitter = QSplitter(Qt.Horizontal)
        left = QWidget()
        left.setMinimumWidth(SIDE_PANEL_MIN_WIDTH)
        left.setMaximumWidth(SIDE_PANEL_MAX_WIDTH)
        self.runtime_left_panel = left
        left_layout = QVBoxLayout(left)
        left_layout.addWidget(QLabel("Live Pals"))
        self.runtime_search_edit = QLineEdit()
        self.runtime_search_edit.setPlaceholderText("Search species, nickname, or identity…")
        self.runtime_search_edit.textChanged.connect(self.filter_runtime_roster)
        left_layout.addWidget(self.runtime_search_edit)
        self.runtime_list = QListWidget()
        self.runtime_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.runtime_list.currentRowChanged.connect(self.select_runtime_record)
        left_layout.addWidget(self.runtime_list, 1)
        splitter.addWidget(left)

        right = QWidget()
        right.setMinimumWidth(0)
        right.setMaximumWidth(780)
        right.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        detail = QGroupBox("Live Pal record")
        detail.setMinimumWidth(0)
        detail.setMaximumWidth(760)
        detail.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        detail_form = QFormLayout(detail)
        detail_form.setVerticalSpacing(4)
        detail_form.setHorizontalSpacing(8)
        detail_form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.DontWrapRows)
        self.runtime_species_label = QLabel("Unavailable")
        self.runtime_nickname_label = QLabel("Unavailable")
        self.runtime_level_label = QLabel("Unavailable")
        self.runtime_rank_label = QLabel("Unavailable")
        self.runtime_instance_label = QLabel("Unavailable")
        self.runtime_identity_label = QLabel("Unavailable")
        self.runtime_owner_label = QLabel("Unavailable")
        self.runtime_player_label = QLabel("Unavailable")
        self.runtime_class_label = QLabel("Unavailable")
        self.runtime_active_label = QLabel("Unavailable")
        self.runtime_passive_label = QLabel("Unavailable")
        self.runtime_active_count_label = QLabel("Unavailable")
        self.runtime_passive_count_label = QLabel("Unavailable")
        for label in (
            self.runtime_species_label,
            self.runtime_nickname_label,
            self.runtime_level_label,
            self.runtime_rank_label,
            self.runtime_instance_label,
            self.runtime_identity_label,
            self.runtime_owner_label,
            self.runtime_player_label,
            self.runtime_class_label,
            self.runtime_active_label,
            self.runtime_passive_label,
            self.runtime_active_count_label,
            self.runtime_passive_count_label,
        ):
            label.setWordWrap(True)
            label.setMinimumHeight(18)
            label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        detail_form.addRow("Species", self.runtime_species_label)
        detail_form.addRow("Nickname", self.runtime_nickname_label)
        detail_form.addRow("Level", self.runtime_level_label)
        detail_form.addRow("Rank", self.runtime_rank_label)
        detail_form.addRow("Active skill entries", self.runtime_active_count_label)
        detail_form.addRow("Skill names", self.runtime_active_label)
        detail_form.addRow("Passive skill entries", self.runtime_passive_count_label)
        detail_form.addRow("Passive names", self.runtime_passive_label)
        right_layout.addWidget(detail)

        runtime_technical_toggle = QToolButton()
        runtime_technical_toggle.setText("Technical details")
        runtime_technical_toggle.setCheckable(True)
        runtime_technical_toggle.setChecked(False)
        runtime_technical_toggle.setArrowType(Qt.RightArrow)
        runtime_technical_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        runtime_technical_details = QGroupBox()
        runtime_technical_form = QFormLayout(runtime_technical_details)
        runtime_technical_form.setVerticalSpacing(3)
        runtime_technical_form.setHorizontalSpacing(8)
        for label_text, value_label in (
            ("Instance", self.runtime_instance_label),
            ("Identity", self.runtime_identity_label),
            ("Owner", self.runtime_owner_label),
            ("Player UID", self.runtime_player_label),
            ("Class", self.runtime_class_label),
        ):
            runtime_technical_form.addRow(label_text, self._technical_value_row(value_label))
        runtime_technical_details.setVisible(False)
        runtime_technical_toggle.toggled.connect(self._toggle_runtime_technical_details)
        self.runtime_technical_toggle = runtime_technical_toggle
        self.runtime_technical_details = runtime_technical_details
        right_layout.addWidget(runtime_technical_toggle)
        right_layout.addWidget(runtime_technical_details)
        explanation = QLabel(
            "This panel observes the running game through Pal Admin’s local snapshot bridge. "
            "It intentionally has no edit, save, or mutation controls."
        )
        explanation.setWordWrap(True)
        explanation.setStyleSheet("color: #a8c5af; padding: 8px 0;")
        right_layout.addWidget(explanation)
        right_layout.addStretch()
        # This detail card is content-sized. An outer scroll area would only
        # create misleading scrollbars around fields that already fit.
        right.setMinimumHeight(0)
        right.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        splitter.addWidget(right)
        splitter.setChildrenCollapsible(False)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        splitter.setSizes([360, 680])
        layout.addWidget(splitter, 1)
        return page

    def refresh_runtime_snapshot(self) -> None:
        previous_snapshot = self.runtime_snapshot
        try:
            snapshot = load_runtime_snapshot()
        except RuntimeSnapshotError as exc:
            self.runtime_snapshot = None
            self.runtime_status_label.setText(f"Bridge error: {exc}")
            self.runtime_status_label.setToolTip(str(exc))
            self.runtime_status_label.setStyleSheet("color: #e6a6a6; padding: 4px 0;")
            self.runtime_list.clear()
            self._clear_runtime_detail()
            return

        self.runtime_snapshot = snapshot
        if snapshot is None:
            self.runtime_status_label.setText(
                "Waiting for a live snapshot. Launch Palworld with UE4SS and press F9."
            )
            self.runtime_status_label.setStyleSheet("color: #a8c5af; padding: 4px 0;")
            self.runtime_status_label.setToolTip("No runtime snapshot has been published yet.")
            self.runtime_list.clear()
            self._clear_runtime_detail()
            return

        age = max(0, int(time.time() - snapshot.file_mtime))
        freshness = "Live" if age <= 15 else f"Last refreshed {self._human_age(age)}"
        if not snapshot.ok:
            status = f"Runtime unavailable ({freshness})"
            if snapshot.error:
                status += f": {snapshot.error}"
            self.runtime_status_label.setText(status)
            self.runtime_status_label.setToolTip(snapshot.error or "The runtime bridge reported unavailable data.")
            self.runtime_status_label.setStyleSheet("color: #e6a6a6; padding: 4px 0;")
        else:
            visible_count = len(self._runtime_collection_records())
            scope = "matched to this save" if self.runtime_collection_identity_keys else "nearby records"
            self.runtime_status_label.setText(f"{freshness} · {visible_count} live Pals {scope}")
            self.runtime_status_label.setToolTip(
                f"Detailed scan: {snapshot.scanned} actors scanned, {snapshot.included} included, "
                f"{snapshot.filtered} filtered by the runtime bridge."
            )
            self.runtime_status_label.setStyleSheet("color: #a8c5af; padding: 4px 0;")
        self.runtime_path_label.setText(f"Bridge: {snapshot.path}")
        snapshot_changed = (
            previous_snapshot is None
            or previous_snapshot.written_at != snapshot.written_at
            or previous_snapshot.ok != snapshot.ok
            or previous_snapshot.error != snapshot.error
            or previous_snapshot.records != snapshot.records
        )
        if snapshot_changed:
            self._refresh_runtime_list()

    def _refresh_runtime_list(self) -> None:
        if self.runtime_snapshot is None:
            return
        query = self.runtime_search_edit.text().strip().casefold()
        records = [
            record
            for record in self._runtime_collection_records()
            if not query
            or query in " ".join(
                (record.species, record.nickname, record.identity_key)
            ).casefold()
        ]
        self.runtime_list.blockSignals(True)
        self.runtime_list.clear()
        selected_row = -1
        for row, record in enumerate(records):
            canonical = self._catalog_label(self.catalog.pals, record.species)
            name = f"{canonical} [{record.species}]" if record.species else "Unknown Pal"
            if record.nickname.strip():
                name += f" ({record.nickname.strip()})"
            item = QListWidgetItem(f"{name}  |  Lv {record.level or '?'}")
            item.setData(Qt.UserRole, record.identity_key)
            self.runtime_list.addItem(item)
            if record.identity_key == self.runtime_selected_key:
                selected_row = row
        if records and selected_row < 0:
            selected_row = 0
        if selected_row >= 0:
            self.runtime_list.setCurrentRow(selected_row)
        self.runtime_list.blockSignals(False)
        self._fit_list_panel(self.runtime_left_panel, self.runtime_list)
        self.select_runtime_record(selected_row)

    @staticmethod
    def _fit_list_panel(panel: QWidget, list_widget: QListWidget) -> None:
        """Keep roster columns close to their displayed content width.

        Fullscreen windows should provide more workspace for future pages, not
        inflate a short list into a wide empty rectangle.  The lower bound
        keeps search controls usable; the upper bound prevents long nicknames
        or malformed data from taking over the workbench.
        """

        content_width = max(
            (list_widget.fontMetrics().horizontalAdvance(list_widget.item(row).text())
             for row in range(list_widget.count())),
            default=0,
        )
        width = max(280, min(340, content_width + 32))
        panel.setFixedWidth(width)

    def _runtime_collection_records(self):
        """Return saved Pal identities when a source save is loaded.

        The runtime manager also contains nearby wild actors.  Matching the
        live FGuid to every Pal entry in the loaded save keeps the default
        Live Roster useful without throwing away Pals whose OwnerPlayerUId is
        blank while they are in a container.
        """

        if self.runtime_snapshot is None:
            return ()
        records = self.runtime_snapshot.records
        if not self.runtime_collection_identity_keys:
            return tuple(record for record in records if not record.is_player)
        return tuple(
            record
            for record in records
            if canonical_identity_key(record.identity_key)
            in self.runtime_collection_identity_keys
        )

    def filter_runtime_roster(self, *_args: object) -> None:
        self._refresh_runtime_list()

    def select_runtime_record(self, row: int) -> None:
        item = self.runtime_list.item(row) if row >= 0 else None
        identity_key = item.data(Qt.UserRole) if item else None
        self.runtime_selected_key = identity_key if isinstance(identity_key, str) else None
        record = self.runtime_snapshot.find_record(self.runtime_selected_key) if self.runtime_snapshot else None
        if record is None:
            self._clear_runtime_detail()
            return
        canonical = self._catalog_label(self.catalog.pals, record.species)
        self.runtime_species_label.setText(canonical if record.species else "Unavailable")
        self.runtime_species_label.setToolTip(record.species or "Unavailable")
        self.runtime_nickname_label.setText(record.nickname or "Unavailable")
        self.runtime_level_label.setText(str(record.level) if record.level is not None else "Unavailable")
        self.runtime_rank_label.setText(str(record.rank) if record.rank is not None else "Unavailable")
        self._set_runtime_skill_summary(
            self.runtime_active_count_label,
            self.runtime_active_label,
            record.active_skills,
        )
        self._set_runtime_skill_summary(
            self.runtime_passive_count_label,
            self.runtime_passive_label,
            record.passive_skills,
        )
        self._set_technical_value(self.runtime_instance_label, record.instance_id)
        self._set_technical_value(self.runtime_identity_label, record.identity_key)
        self._set_technical_value(self.runtime_owner_label, record.owner_player_uid)
        self._set_technical_value(self.runtime_player_label, record.player_uid)
        self._set_technical_value(self.runtime_class_label, record.character_class)

    def _clear_runtime_detail(self) -> None:
        for label in (
            self.runtime_instance_label,
            self.runtime_identity_label,
            self.runtime_owner_label,
            self.runtime_player_label,
            self.runtime_class_label,
        ):
            self._set_technical_value(label, None)
        for label in (
            self.runtime_species_label,
            self.runtime_nickname_label,
            self.runtime_level_label,
            self.runtime_rank_label,
        ):
            label.setText("Unavailable")
        self.runtime_active_count_label.setText("Unavailable")
        self.runtime_passive_count_label.setText("Unavailable")
        self.runtime_active_label.setText("Unavailable")
        self.runtime_passive_label.setText("Unavailable")
        self.runtime_active_label.setToolTip("")
        self.runtime_passive_label.setToolTip("")

    def _set_runtime_skill_summary(
        self,
        count_label: QLabel,
        names_label: QLabel,
        values: tuple[str, ...],
    ) -> None:
        """Separate recorded runtime entries from names the catalog can resolve."""
        count_label.setText(str(len(values)))
        known_names: list[str] = []
        unknown_values: list[str] = []
        for value in values:
            display = self._display_code(value)
            if display.startswith("Unavailable ["):
                unknown_values.append(str(value))
            else:
                known_names.append(display)
        names_label.setText(", ".join(known_names) if known_names else "Unavailable")
        if unknown_values:
            names_label.setToolTip("Unresolved runtime values: " + ", ".join(unknown_values))
        else:
            names_label.setToolTip("Names resolved from the Palworld 1.0 catalog.")

    @staticmethod
    def _placeholder_page(title: str, description: str) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        heading = QLabel(title)
        heading.setStyleSheet("font-size: 22px; color: #d9b56f; font-weight: 600;")
        body = QLabel(description + "\n\nThis surface is staged in the shell and will be connected after the core roster workflow is complete.")
        body.setWordWrap(True)
        body.setStyleSheet("color: #a8c5af; font-size: 14px; padding: 8px;")
        layout.addWidget(heading)
        layout.addWidget(body)
        layout.addStretch()
        return page

    def _settings_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        heading = QLabel("Settings / Data")
        heading.setStyleSheet("font-size: 22px; color: #d9b56f; font-weight: 600;")
        layout.addWidget(heading)

        group = QGroupBox("Palworld save location")
        form = QFormLayout(group)
        self.save_location_edit = QLineEdit(str(self.save_games_dir))
        self.save_location_edit.setToolTip(
            "Pal Admin searches this folder for normal Level.sav files and remembers your choice."
        )
        browse = QPushButton("Browse")
        browse.clicked.connect(self.choose_save_games_dir)
        detect = QPushButton("Use detected default")
        detect.clicked.connect(self.use_detected_save_dir)
        row = QHBoxLayout()
        row.addWidget(self.save_location_edit, 1)
        row.addWidget(browse)
        row.addWidget(detect)
        form.addRow("Save games folder", row)

        self.save_location_status = QLabel()
        self.save_location_status.setWordWrap(True)
        self.save_location_status.setStyleSheet("color: #a8c5af; padding-top: 6px;")
        form.addRow("Discovery", self.save_location_status)
        layout.addWidget(group)

        self.open_latest_button = QPushButton("Open latest detected save")
        layout.addWidget(self.open_latest_button)
        data_group = QGroupBox("Data catalog")
        data_layout = QVBoxLayout(data_group)
        data_layout.setContentsMargins(6, 6, 6, 6)
        data_layout.addWidget(self.catalog_label)
        portrait_attribution = QLabel(
            "Portraits: PalCalc reference assets by Tyler Camp. "
            "Attribution and license are included with the packaged portrait data."
        )
        portrait_attribution.setWordWrap(True)
        portrait_attribution.setStyleSheet("color: #6b7280; padding-top: 4px;")
        data_layout.addWidget(portrait_attribution)
        layout.addWidget(data_group)
        backup_group = QGroupBox("Automatic backups")
        backup_form = QFormLayout(backup_group)
        backup_root = QLabel(str(default_backup_root()))
        backup_root.setWordWrap(True)
        backup_root.setToolTip(str(default_backup_root()))
        self.backup_root_label = backup_root
        open_backup_button = QPushButton("Open Backup Folder")
        open_backup_button.clicked.connect(self.open_backup_folder)
        backup_row = QHBoxLayout()
        backup_row.addWidget(backup_root, 1)
        backup_row.addWidget(open_backup_button)
        backup_form.addRow("Automatic backup location", backup_row)
        backup_form.addRow("Retention", QLabel(f"Latest {RETENTION_LIMIT} verified backups per source"))
        backup_note = QLabel(
            "Save a Copy source safety copies are separate and are stored beside the selected output."
        )
        backup_note.setWordWrap(True)
        backup_note.setStyleSheet("color: #a8c5af; padding-top: 4px;")
        backup_form.addRow(backup_note)
        layout.addWidget(backup_group)
        self.settings_safety_note = QLabel(
            "Direct Save creates an automatic verified backup before replacing the loaded source. "
            "While Palworld is running, editing and saving are locked."
        )
        self.settings_safety_note.setWordWrap(True)
        self.settings_safety_note.setSizePolicy(
            QSizePolicy.Policy.Ignored,
            QSizePolicy.Policy.Preferred,
        )
        layout.addWidget(self.settings_safety_note)
        layout.addStretch()
        self._refresh_save_location_status()
        return page

    def open_backup_folder(self) -> bool:
        """Create and open the production automatic-backup folder."""
        backup_root = default_backup_root()
        try:
            backup_root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            QMessageBox.critical(
                self,
                "Could not open backup folder",
                f"Could not create the automatic backup folder.\n\nPath: {backup_root}\n\n{exc}",
            )
            return False
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(backup_root))):
            QMessageBox.critical(
                self,
                "Could not open backup folder",
                f"Could not open the automatic backup folder.\n\nPath: {backup_root}\n\n"
                "Open this path manually in File Explorer.",
            )
            return False
        self._show_transient_status("Opened automatic backup folder")
        return True

    def _persist_save_location(self) -> None:
        self.app_settings.setValue("save_games_dir", str(self.save_games_dir))
        if self.last_save_path:
            self.app_settings.setValue("last_save_path", str(self.last_save_path))
        self.app_settings.sync()

    def _refresh_save_location_status(self) -> None:
        latest = find_latest_level_save(self.save_games_dir)
        if latest:
            self.save_location_status.setText(f"Latest save found: {latest}")
        else:
            self.save_location_status.setText(
                "No Level.sav found here. Browse to the folder containing your saves; the choice will be remembered."
            )

    def choose_save_games_dir(self) -> None:
        selected = QFileDialog.getExistingDirectory(
            self,
            "Choose Palworld save games folder",
            str(self.save_games_dir if self.save_games_dir.exists() else default_save_games_dir()),
        )
        if selected:
            self.save_games_dir = Path(selected).resolve()
            self.save_location_edit.setText(str(self.save_games_dir))
            self._persist_save_location()
            self._refresh_save_location_status()

    def use_detected_save_dir(self) -> None:
        self.save_games_dir = default_save_games_dir()
        self.save_location_edit.setText(str(self.save_games_dir))
        self._persist_save_location()
        self._refresh_save_location_status()

    def open_latest_detected_save(self) -> bool:
        latest = find_latest_level_save(self.save_games_dir)
        if latest is None:
            self._refresh_save_location_status()
            return False
        return self._guard_pending_operation(
            "open_latest",
            lambda: self.load_path(latest),
        )

    def _ledger_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        heading = QLabel("Ledger")
        heading.setStyleSheet("font-size: 22px; color: #d9b56f; font-weight: 600;")
        layout.addWidget(heading)
        description = QLabel(
            "A safety record of the selected Pal's draft, validation state, and source/output identity."
        )
        description.setWordWrap(True)
        description.setStyleSheet("color: #a8c5af; padding: 8px 0;")
        layout.addWidget(description)

        summary = QGroupBox("Operation state")
        summary_form = QFormLayout(summary)
        self.ledger_state_label = QLabel("No save loaded")
        self.ledger_source_label = QLabel("Unavailable")
        self.ledger_target_label = QLabel("Unavailable")
        self.ledger_validation_label = QLabel("Unavailable")
        self.backup_policy_combo = QComboBox()
        self.backup_policy_combo.addItem("Always backup (recommended)", BackupPolicy.ALWAYS.value)
        self.backup_policy_combo.addItem("Ask before backup", BackupPolicy.ASK.value)
        self.backup_policy_combo.addItem("Backups off", BackupPolicy.OFF.value)
        self.backup_policy_combo.setToolTip(
            "Choose whether Pal Admin creates a timestamped copy of the source before Save Copy."
        )
        self.backup_policy_combo.currentIndexChanged.connect(self.update_backup_policy)
        for label in (
            self.ledger_state_label,
            self.ledger_source_label,
            self.ledger_target_label,
            self.ledger_validation_label,
        ):
            label.setWordWrap(True)
        summary_form.addRow("Draft", self.ledger_state_label)
        summary_form.addRow("Source", self.ledger_source_label)
        summary_form.addRow("Latest output", self.ledger_target_label)
        summary_form.addRow("Validation", self.ledger_validation_label)
        summary_form.addRow("Backup policy", self.backup_policy_combo)
        layout.addWidget(summary)

        changes = QGroupBox("Changed fields")
        changes_layout = QVBoxLayout(changes)
        self.ledger_changes_list = QListWidget()
        changes_layout.addWidget(self.ledger_changes_list)
        layout.addWidget(changes, 1)

        actions = QHBoxLayout()
        self.revert_draft_button = QPushButton("Revert draft")
        self.revert_draft_button.setToolTip("Restore the selected Pal's form to its loaded source values.")
        actions.addWidget(self.revert_draft_button)
        actions.addStretch()
        layout.addLayout(actions)
        return page

    def _refresh_ledger_page(self) -> None:
        if not hasattr(self, "ledger_state_label"):
            return
        self._refresh_global_shell_state()
        if self.ledger is None:
            self.ledger_state_label.setText("No save loaded")
            self.ledger_source_label.setText("Unavailable")
            self.ledger_target_label.setText("Unavailable")
            self.ledger_validation_label.setText("Unavailable")
            self.backup_policy_combo.setCurrentIndex(0)
            self.ledger_changes_list.clear()
            self._update_action_states()
            return
        count = self.ledger.total_changed_field_count
        state = "clean" if not self.ledger.dirty else f"{count} field{'s' if count != 1 else ''} changed"
        operation_labels = {
            "save_succeeded": "Save succeeded",
            "save_succeeded_prune_warning": "Save succeeded, backup retention warning",
            "save_refresh_failed": "Save verified, reload needed",
            "validation_failed": "Validation failed",
            "source_changed": "Source changed externally",
            "transaction_failed": "Save failed",
            "restored": "Save failed, source restored",
            "uncertain": "Save outcome uncertain",
        }
        if self.ledger.operation_status != "idle":
            state += f" ({operation_labels.get(self.ledger.operation_status, self.ledger.operation_status)})"
        self.ledger_state_label.setText(state)
        self.ledger_source_label.setText(str(self.ledger.source_path))
        self.ledger_target_label.setText(str(self.ledger.target_path or "Not created"))
        self.ledger_validation_label.setText(
            "PASS / no messages" if not self.ledger.validation_messages
            else " | ".join(self.ledger.validation_messages)
        )
        self.backup_policy_combo.blockSignals(True)
        self.backup_policy_combo.setCurrentIndex(
            self.backup_policy_combo.findData(self.ledger.backup_policy.value)
        )
        self.backup_policy_combo.blockSignals(False)
        self.ledger_changes_list.clear()
        if self.ledger.drafts:
            for entry in self.ledger.pending_entries:
                context = entry.display_context or entry.instance_id
                self.ledger_changes_list.addItem(f"{context} [{entry.instance_id}]")
                for change in entry.changes:
                    self.ledger_changes_list.addItem(
                        f"  {self._friendly_field_name(change.name)}: "
                        f"{change.before!r} -> {change.after!r}"
                    )
        else:
            for name in self.ledger.changed_fields:
                before = self.ledger.before_fields.get(name, "<missing>")
                after = self.ledger.after_fields.get(name, "<missing>")
                self.ledger_changes_list.addItem(f"{name}: {before!r} -> {after!r}")
        self._update_action_states()

    def update_backup_policy(self, _index: int) -> None:
        if self.ledger is None:
            return
        value = self.backup_policy_combo.currentData()
        if value:
            self.ledger.backup_policy = BackupPolicy(str(value))
            self._show_transient_status(f"Backup policy set to {self.ledger.backup_policy.value}")

    def revert_draft(self) -> None:
        if self.ledger is None or not self.ledger.dirty:
            return
        count = self.ledger.total_changed_field_count
        pals = self.ledger.pending_pal_count
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Revert Draft")
        if count == 1 and pals == 1:
            prompt = "Revert the pending change for 1 edited Pal?"
        elif pals == 1:
            prompt = f"Revert all {count} pending changes for 1 edited Pal?"
        else:
            prompt = f"Revert all {count} pending changes across {pals} edited Pals?"
        box.setText(prompt)
        revert_button = box.addButton(
            "Revert All Changes", QMessageBox.ButtonRole.DestructiveRole
        )
        cancel_button = box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(cancel_button)
        box.setEscapeButton(cancel_button)
        box.exec()
        if box.clickedButton() is not revert_button:
            return
        self.ledger.clear_drafts()
        if 0 <= self.current_index < len(self.instances):
            try:
                self._populate_selected_pal(self.current_index)
            except Exception as exc:
                self._show_transient_status(f"Draft reverted, but the editor could not refresh: {exc}")
                return
        self._refresh_ledger_page()
        self._show_transient_status("All Pal drafts reverted to the loaded source values")

    def navigate(self, row: int) -> None:
        if 0 <= row < self.nav_list.count() and self.nav_list.currentRow() != row:
            self.nav_list.setCurrentRow(row)
            return
        item = self.nav_list.item(row)
        key = item.data(Qt.UserRole) if item else ROSTER
        for index in range(self.nav_list.count()):
            nav_item = self.nav_list.item(index)
            nav_item.setForeground(QColor("#f5f1e6"))
            nav_item.setBackground(QColor("#385246" if index == row else "#1f2121"))
        self.content_stack.setCurrentIndex(self._nav_pages.get(key, 0))
        destination = DEFAULT_NAVIGATION.resolve(key)
        self.statusBar().clearMessage()
        self.status_guidance_label.setText(destination.description)
        self._update_action_states()

    @staticmethod
    def _spin(minimum: int, maximum: int) -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(minimum, maximum)
        return spin

    def _technical_value_row(self, label: QLabel) -> QWidget:
        """Create a compact raw-value row with a reliable copy affordance."""
        label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        label.setStyleSheet("font-family: Consolas, 'Courier New'; color: #c8c1b4;")
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(label, 1)
        copy_button = QPushButton("Copy")
        copy_button.setFixedWidth(52)
        copy_button.clicked.connect(lambda: self._copy_technical_value(label))
        self._technical_copy_buttons[label] = copy_button
        layout.addWidget(copy_button)
        return row

    def _set_technical_value(self, label: QLabel, value: str | None) -> None:
        raw = str(value).strip() if value is not None else ""
        label.setProperty("technical_value", raw)
        label.setText(self._compact_identifier(raw))
        label.setToolTip(raw or "Unavailable")
        button = self._technical_copy_buttons.get(label)
        if button is not None:
            button.setEnabled(bool(raw))

    def _copy_technical_value(self, label: QLabel) -> None:
        raw = str(label.property("technical_value") or "")
        if raw:
            QApplication.clipboard().setText(raw)
            self._show_transient_status("Technical value copied")

    def _toggle_technical_details(self, expanded: bool) -> None:
        self.technical_toggle.setArrowType(Qt.DownArrow if expanded else Qt.RightArrow)
        self.technical_details.setVisible(expanded)
        self._schedule_roster_fit()

    def _toggle_runtime_technical_details(self, expanded: bool) -> None:
        self.runtime_technical_toggle.setArrowType(Qt.DownArrow if expanded else Qt.RightArrow)
        self.runtime_technical_details.setVisible(expanded)

    @staticmethod
    def _portrait_asset_directory() -> Path:
        """Locate the bundled Pal portrait reference assets in source or a frozen build."""

        if getattr(sys, "frozen", False):
            return Path(getattr(sys, "_MEIPASS")) / "data" / "portraits"
        return (
            Path(__file__).resolve().parents[3]
            / "tools"
            / "asset-extraction"
            / "PalCalc"
            / "PalCalc.UI"
            / "Resources"
            / "Pals"
        )

    @classmethod
    def _load_portrait_index(cls) -> dict[str, Path]:
        directory = cls._portrait_asset_directory()
        try:
            return {path.stem.casefold(): path for path in directory.glob("*.png")}
        except OSError:
            return {}

    def _portrait_path(self, species_code: str | None) -> tuple[Path | None, bool]:
        """Return an image path and whether the species itself is known to the catalog."""

        if not species_code:
            return None, False
        entry = next(
            (item for item in self.catalog.pals if item.code.casefold() == species_code.casefold()),
            None,
        )
        if entry is None:
            return None, False
        for candidate in (entry.label, entry.code):
            path = self._portrait_paths.get(candidate.casefold())
            if path is not None:
                return path, True
        return None, True

    def _refresh_portrait_for_current_species(self, *_args: object) -> None:
        code = str(self.species_combo.currentData() or "").strip() or None
        if code == self._portrait_code and self.portrait_label.property("portrait_state") == "loaded":
            return
        self._portrait_code = code
        self.portrait_label.setPixmap(QPixmap())
        self.portrait_label.setText("Loading portrait...")
        self.portrait_label.setProperty("portrait_state", "loading")
        QTimer.singleShot(0, lambda requested_code=code: self._load_portrait(requested_code))

    def _load_portrait(self, species_code: str | None) -> None:
        if species_code != self._portrait_code:
            return
        path, known_species = self._portrait_path(species_code)
        if path is None:
            state = "missing" if known_species else "unknown"
            message = "No portrait available" if known_species else "Unknown species"
            self.portrait_label.setPixmap(QPixmap())
            self.portrait_label.setText(message)
            self.portrait_label.setProperty("portrait_state", state)
            self.portrait_label.setToolTip(
                "The catalog knows this species, but no portrait asset is bundled."
                if known_species
                else "This species is not present in the current catalog."
            )
            return
        pixmap = QPixmap(str(path))
        if pixmap.isNull():
            self.portrait_label.setPixmap(QPixmap())
            self.portrait_label.setText("No portrait available")
            self.portrait_label.setProperty("portrait_state", "missing")
            self.portrait_label.setToolTip("The portrait asset could not be loaded.")
            return
        self.portrait_label.setText("")
        self.portrait_label.setPixmap(
            pixmap.scaled(
                self.portrait_label.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )
        self.portrait_label.setProperty("portrait_state", "loaded")
        self.portrait_label.setToolTip(f"Species portrait: {path.stem}")

    @staticmethod
    def _selector() -> QComboBox:
        combo = QComboBox()
        combo.setEditable(True)
        combo.lineEdit().setFont(combo.font())
        combo.lineEdit().setStyleSheet(
            "background: transparent; border: none; padding: 0;"
        )
        combo.setMinimumWidth(0)
        combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        # Do not let a long internal skill identifier dictate the entire page
        # width.  The selected value remains editable and the popup can still
        # be searched; only the in-form field is kept to the available column.
        combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        combo.setMinimumContentsLength(12)
        combo.setInsertPolicy(QComboBox.NoInsert)
        combo.completer().setFilterMode(Qt.MatchContains)
        combo.completer().setCaseSensitivity(Qt.CaseInsensitive)
        return combo

    @staticmethod
    def _compact_identifier(value: str | None) -> str:
        """Keep long save identifiers readable in a compact editor pane."""

        if not value:
            return "Unavailable"
        if len(value) <= 24:
            return value
        return f"{value[:10]}...{value[-10:]}"

    @staticmethod
    def _human_age(seconds: int) -> str:
        """Format bridge freshness without exposing raw machine seconds."""
        if seconds < 60:
            return "less than a minute ago"
        minutes, remainder = divmod(seconds, 60)
        hours, minutes = divmod(minutes, 60)
        days, hours = divmod(hours, 24)
        parts: list[str] = []
        if days:
            parts.append(f"{days} day{'s' if days != 1 else ''}")
        if hours:
            parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
        if minutes and len(parts) < 2:
            parts.append(f"{minutes} minute{'s' if minutes != 1 else ''}")
        return ", ".join(parts) + " ago"

    @staticmethod
    def _catalog_label(entries: tuple[CatalogEntry, ...], code: str | None) -> str:
        """Return the player-facing catalog label while preserving unknown codes."""
        if not code:
            return "Unknown species"
        for entry in entries:
            if entry.code.casefold() == code.casefold():
                return entry.label
        return code

    @staticmethod
    def _populate_selector(
        combo: QComboBox,
        entries: tuple[CatalogEntry, ...],
        blank_label: str,
        blank_code: str,
        *,
        show_code: bool = False,
    ) -> None:
        combo.clear()
        combo.addItem(blank_label, blank_code)
        for entry in entries:
            label = f"{entry.label}  [{entry.code}]" if show_code else entry.label
            combo.addItem(label, entry.code)
            tooltip = f"{entry.label}  [{entry.code}]"
            if entry.description:
                tooltip += f"\n{entry.description}"
            combo.setItemData(combo.count() - 1, tooltip, Qt.ToolTipRole)

    def _refresh_passive_selectors(self, advanced: bool) -> None:
        """Switch between the clean Pal list and the complete raw catalog."""

        selected_codes = [selector.currentData() for selector in self.passive_selectors]
        entries = self.catalog.passives if advanced else self.catalog.standard_passives
        self.passive_catalog_label.setText(
            f"{len(entries)} {'catalog' if advanced else 'standard'} passives"
        )
        for selector, selected_code in zip(self.passive_selectors, selected_codes):
            selector.blockSignals(True)
            self._populate_selector(selector, entries, "Leave slot blank", "__BLANK__")
            self._set_combo_code(selector, selected_code, self.catalog.passives)
            selector.blockSignals(False)
        self._sync_draft_from_form()

    @staticmethod
    def _set_combo_code(
        combo: QComboBox,
        code: str | None,
        entries: tuple[CatalogEntry, ...] = (),
    ) -> None:
        if not code:
            combo.setCurrentIndex(0)
            return
        index = combo.findData(code)
        if index < 0:
            folded = str(code).casefold()
            for candidate in range(combo.count()):
                data = combo.itemData(candidate)
                if isinstance(data, str) and data.casefold() == folded:
                    index = candidate
                    break
        if index < 0:
            entry = next(
                (candidate for candidate in entries if candidate.code.casefold() == str(code).casefold()),
                None,
            )
            label = entry.label if entry else f"Unknown  [{code}]"
            combo.insertItem(1, label, code)
            if entry:
                tooltip = f"{entry.label}  [{entry.code}]"
                if entry.description:
                    tooltip += f"\n{entry.description}"
                combo.setItemData(1, tooltip, Qt.ToolTipRole)
            index = 1
        combo.setCurrentIndex(index)

    @staticmethod
    def _selector_values(selectors: list[QComboBox], original: list[str]) -> list[str]:
        values: list[str] = []
        for index, selector in enumerate(selectors):
            code = selector.currentData()
            if code == "__BLANK__":
                continue
            if code:
                values.append(str(code))
            elif index < len(original):
                values.append(original[index])
        return values

    def _skill_selector_for_event(self, watched: object) -> QComboBox | None:
        selectors = (*self.active_selectors, *self.passive_selectors)
        if isinstance(watched, QComboBox) and watched in selectors:
            return watched
        if isinstance(watched, QLineEdit):
            parent = watched.parentWidget()
            if isinstance(parent, QComboBox) and parent in selectors:
                return parent
        return None

    def eventFilter(self, watched: object, event: QEvent) -> bool:  # noqa: N802 - Qt override
        selector = self._skill_selector_for_event(watched)
        if selector is not None and event.type() in (
            QEvent.Type.FocusIn,
            QEvent.Type.MouseButtonPress,
        ):
            self._show_skill_detail(selector)
        return super().eventFilter(watched, event)

    def refresh_skill_detail(self, *_args: object) -> None:
        """Show readable context after a selector value genuinely changes."""

        selector = self.sender()
        if not isinstance(selector, QComboBox):
            self.skill_detail_label.setText(
                "Select an active skill or passive to see its effect. Internal ID is shown secondarily."
            )
            return
        self._show_skill_detail(selector)

    def _show_skill_detail(self, selector: QComboBox) -> None:
        """Render selector context without reading or changing editor draft state."""

        code = selector.currentData()
        if not code or code == "__BLANK__":
            self.skill_detail_label.setText(
                "Select an active skill or passive to see its effect. Internal ID is shown secondarily."
            )
            return
        entries = self.catalog.attacks if selector in self.active_selectors else self.catalog.passives
        entry = self.catalog.entry(entries, str(code))
        if entry is None:
            self.skill_detail_label.setText(f"Internal ID: {code}")
            return
        lines = [entry.label]
        if entry.description:
            lines.append(entry.description)
        lines.append(f"Internal ID: {entry.code}")
        self.skill_detail_label.setText("\n".join(lines))

    def open_save(self) -> bool:
        if self.last_save_path and self.last_save_path.exists():
            initial = self.last_save_path
        elif self.save_games_dir.exists():
            initial = self.save_games_dir
        else:
            initial = default_save_games_dir()
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Open Palworld Level.sav",
            str(initial),
            "Palworld saves (*.sav);;Level.sav (Level.sav);;All files (*.*)",
        )
        if not path:
            return False
        selected_path = Path(path)
        return self._guard_pending_operation(
            "open_source",
            lambda: self.load_path(selected_path),
        )

    def _refresh_safety_status(self) -> GameSafetyStatus:
        self.safety_status = get_game_safety_status()
        self._refresh_global_shell_state()
        return self.safety_status

    def _refresh_global_shell_state(self) -> None:
        """Synchronize compact global shell state from existing application state."""
        if not hasattr(self, "draft_status_label"):
            return

        count = self.ledger.total_changed_field_count if self.ledger is not None else 0
        edited_pals = self.ledger.pending_pal_count if self.ledger is not None else 0
        if count == 0:
            draft_text = "Draft: Clean"
        elif count == 1:
            draft_text = "Draft: 1 change"
        else:
            draft_text = f"Draft: {count} changes"
        self.draft_status_label.setText(draft_text)
        self.draft_status_label.setToolTip(
            "No pending changes. Draft matches the source."
            if count == 0
            else f"{count} pending change{'s' if count != 1 else ''}; "
            f"{count} changed field{'s' if count != 1 else ''} across "
            f"{edited_pals} Pal{'s' if edited_pals != 1 else ''}."
        )

        running = not self.safety_status.safe_for_offline_editing
        self.palworld_status_label.setText("Palworld: Running" if running else "Palworld: Closed")
        self.palworld_status_label.setToolTip(
            "Palworld is running. Editing and saving are locked until the game is closed."
            if running
            else "Palworld is closed. Offline editing is available."
        )
        self.palworld_status_label.setStyleSheet(
            "color: #f1c2a8; padding: 0 6px;"
            if running
            else "color: #a8c5af; padding: 0 6px;"
        )

        if self.source_path is None:
            self.source_status_label.setText("Source: None")
            self.source_status_label.setToolTip("No source save is open.")
        else:
            protection = (
                "Editing is locked while Palworld is running."
                if running
                else "Save replaces the loaded source only after an automatic verified backup and post-write validation. Save a Copy creates a separate file and keeps this draft pending."
            )
            self.source_status_label.setText("Source: Loaded")
            self.source_status_label.setToolTip(
                f"Source save: {self.source_path}\n{protection}"
            )

        self.safety_warning_banner.setVisible(running)
        self.safety_warning_banner.setToolTip(
            "Palworld is still running. Close the game completely before editing or creating a save copy."
        )

    def _update_window_title(self) -> None:
        title = (
            "Pal Admin: Roster Workbench"
            if self.source_path is None
            else f"Pal Admin: {self.source_path.name}"
        )
        self.setWindowTitle(title)

    def _show_transient_status(self, message: str, timeout: int = 5000) -> None:
        self.statusBar().showMessage(message, timeout)

    def _require_game_closed(self, action: str) -> bool:
        status = self._refresh_safety_status()
        if status.safe_for_offline_editing:
            return True
        QMessageBox.warning(
            self,
            "Close Palworld first",
            f"Palworld is still running. Close it completely before {action}.\n\n"
            "This protects the save from being locked or overwritten by the game.",
        )
        return False

    def _set_editing_enabled(self, enabled: bool) -> None:
        """Lock mutation controls when the loaded save is a live reference."""

        widgets = [
            self.species_combo,
            self.nickname_edit,
            self.gender_combo,
            self.level_spin,
            self.rank_spin,
            self.iv_hp_spin,
            self.iv_attack_spin,
            self.iv_defense_spin,
            self.apply_preset_button,
            self.scope_level,
            self.scope_rank,
            self.scope_attributes,
            self.scope_active_skills,
            self.scope_passives,
            *self.active_selectors,
            *self.passive_selectors,
        ]
        for widget in widgets:
            widget.setEnabled(enabled)

    def _roster_matches(self, index: int) -> bool:
        instance = self.instances[index]
        template = instance.template
        species_label = self._catalog_label(self.catalog.pals, template.species)
        searchable = " ".join(
            (
                species_label,
                template.species,
                template.nickname,
                *template.active_skills,
                *template.passive_skills,
            )
        ).casefold()
        query = self.roster_search_edit.text().strip().casefold()
        if query and query not in searchable:
            return False

        mode = self.roster_filter_combo.currentData()
        if mode == "nickname" and not template.nickname.strip():
            return False
        if mode == "level_50" and (template.level or 0) < 50:
            return False
        if mode == "iv_80" and max(
            template.iv_hp or 0,
            template.iv_attack or 0,
            template.iv_defense or 0,
        ) < 80:
            return False
        if mode == "passives" and not template.passive_skills:
            return False
        return True

    def _refresh_roster_list(self, preferred_index: int | None = None) -> None:
        """Rebuild the visible roster while retaining the source-record index."""
        if not self.instances:
            self.roster_detail_stack.setCurrentWidget(self.roster_empty_page)
        else:
            self.roster_detail_stack.setCurrentWidget(self.roster_detail_tabs)
        selected_index = self.current_index if preferred_index is None else preferred_index
        visible_indices = [
            index for index in range(len(self.instances)) if self._roster_matches(index)
        ]
        self.pal_list.blockSignals(True)
        self.pal_list.clear()
        for source_index in visible_indices:
            template = self.instances[source_index].template
            species_label = self._catalog_label(self.catalog.pals, template.species)
            roster_name = species_label
            if template.nickname.strip():
                roster_name += f" ({template.nickname.strip()})"
            label = (
                f"{roster_name}  |  Lv {template.level or '?'}  |  "
                f"IV {template.iv_hp}/{template.iv_attack}/{template.iv_defense}"
            )
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, source_index)
            self.pal_list.addItem(item)
        self.pal_list.blockSignals(False)
        self._fit_list_panel(self.roster_left_panel, self.pal_list)

        if not visible_indices:
            self.current_index = -1
            self._clear_selected_form()
            self._update_action_states()
            self._show_transient_status("No Pals match the current roster search/filter")
            self._schedule_roster_fit()
            return
        if selected_index not in visible_indices:
            selected_index = visible_indices[0]
        self.pal_list.setCurrentRow(visible_indices.index(selected_index))
        self._update_action_states()
        self._schedule_roster_fit()

    def filter_roster(self, *_args: object) -> None:
        self._refresh_roster_list()

    def _fit_roster_editor(self) -> None:
        """Keep the editor host exactly inside the scroll viewport.

        QStackedWidget can retain the size hint of the placeholder page after
        switching to the real editor.  Letting the host negotiate from that
        stale hint is what produced the apparent full-window-sized overflow.
        The host now follows the viewport; a real narrow-window overflow is
        handled by the outer vertical fallback.
        """
        if not hasattr(self, "roster_right_scroll"):
            return
        viewport = self.roster_right_scroll.viewport().size()
        if viewport.width() <= 0 or viewport.height() <= 0:
            return
        self.roster_right_host.setMinimumSize(0, 0)
        # Give the current page the actual available width before asking its
        # layout for a height.  QLabel/QGroupBox size hints otherwise use
        # their unconstrained default width and report wrapping that cannot
        # occur in the visible editor.
        self.roster_right_host.resize(viewport.width(), viewport.height())
        self.roster_right_host.layout().activate()
        visible_page = self.roster_detail_stack.currentWidget()
        if visible_page is self.roster_empty_page:
            page = self.roster_empty_page
            page_layout = page.layout()
            if page_layout is not None:
                page_layout.activate()
            content_height = max(page_layout.sizeHint().height(), page_layout.minimumSize().height())
        else:
            page = self.roster_detail_tabs.currentWidget()
            if page is None:
                page = self.roster_detail_tabs
            page_layout = page.layout()
            if page_layout is not None:
                page_layout.activate()
            if page_layout is not None:
                # Build a height from visible top-level items only.  Hidden
                # controls such as collapsed Technical details still
                # contribute to QVBoxLayout.sizeHint() on some Qt styles.
                visible_items = []
                for index in range(page_layout.count()):
                    item = page_layout.itemAt(index)
                    widget = item.widget()
                    if widget is not None:
                        if not widget.isVisible():
                            continue
                        width = max(widget.width(), page.width() - 12)
                        height = (
                            widget.heightForWidth(width)
                            if widget.hasHeightForWidth()
                            else widget.sizeHint().height()
                        )
                    elif item.spacerItem() is not None:
                        continue
                    else:
                        continue
                    visible_items.append(max(0, height))
                margins = page_layout.contentsMargins()
                page_height = (
                    sum(visible_items)
                    + margins.top()
                    + margins.bottom()
                    + max(0, len(visible_items) - 1) * page_layout.spacing()
                )
                page_height = max(page_height, page_layout.minimumSize().height())
            else:
                page_height = page.minimumSizeHint().height()
            tab_bar_height = self.roster_detail_tabs.tabBar().sizeHint().height()
            # The tab widget's tab bar hint is the only overhead needed here.
            # Do not use QTabWidget.minimumSizeHint(), which reflects the
            # tallest hidden page on some Qt styles.
            content_height = page_height + tab_bar_height
        self.roster_right_host.resize(
            viewport.width(),
            max(viewport.height(), content_height),
        )
        self.roster_right_host.layout().activate()

    def _schedule_roster_fit(self) -> None:
        """Re-fit after QScrollArea has recalculated its scrollbar viewport."""
        QTimer.singleShot(0, self._fit_roster_editor)
        QTimer.singleShot(30, self._fit_roster_editor)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        if hasattr(self, "roster_right_scroll"):
            QTimer.singleShot(0, self._fit_roster_editor)

    def load_path(
        self,
        path: Path,
        *,
        preferred_instance_id: str | None = None,
    ) -> bool:
        resolved_path = path.expanduser().resolve(strict=False)
        try:
            report = inspect(resolved_path)
            baseline = fingerprint_file(resolved_path)
            # The save's Pal records are the editable collection.  Some
            # container-held Pals legitimately have a blank OwnerPlayerUId,
            # so the narrower derived ``owned_pals`` list would hide valid
            # entries from the roster.
            self.records = report["pals"]
            self.runtime_collection_identity_keys = {
                canonical_identity_key(record.get("instance_id"))
                for record in report.get("pals", [])
                if record.get("instance_id")
            }
            self.instances = [
                PalInstance.from_record(record, source_build=report["engine_version"])
                for record in self.records
            ]
        except Exception as exc:  # UI boundary: show a useful error instead of a traceback
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Icon.Critical)
            box.setWindowTitle("Could not load save")
            box.setText("Pal Admin could not load this save file.")
            box.setDetailedText(str(exc))
            box.exec()
            return False

        self.source_path = resolved_path
        self.source_baseline = baseline
        self.last_save_path = self.source_path
        if self.source_path.name.casefold() == "level.sav" and len(self.source_path.parents) >= 3:
            self.save_games_dir = self.source_path.parents[2]
        else:
            self.save_games_dir = self.source_path.parent
        self._persist_save_location()
        if hasattr(self, "save_location_edit"):
            self.save_location_edit.setText(str(self.save_games_dir))
            self._refresh_save_location_status()
        if hasattr(self, "empty_save_path_label"):
            self.empty_save_path_label.setText(str(self.last_save_path or self.save_games_dir))
        self.target_path = None
        self.current_path = self.source_path
        self.ledger = OperationLedger(self.source_path)
        self.reference_only = not self._refresh_safety_status().safe_for_offline_editing
        self._update_window_title()
        self._refresh_global_shell_state()
        self.current_index = -1
        preferred_index = 0
        if preferred_instance_id:
            for index, instance in enumerate(self.instances):
                if str(instance.instance_id) == str(preferred_instance_id):
                    preferred_index = index
                    break
        self._refresh_roster_list(preferred_index=preferred_index)
        if self.runtime_snapshot is not None:
            self._refresh_runtime_list()
        self._set_editing_enabled(not self.reference_only)
        self._sync_draft_from_form()
        self._update_action_states()
        self._show_transient_status(
            f"Loaded {len(report.get('pals', []))} saved Pals from {report['engine_version']}"
            + (" as a read-only live reference" if self.reference_only else "")
        )
        return True

    def reload(self) -> bool:
        if not self.source_path:
            self._show_transient_status("No source save is loaded")
            return False
        source_path = self.source_path
        return self._guard_pending_operation(
            "reload",
            lambda: self.load_path(source_path),
        )

    def select_pal(self, row: int) -> None:
        item = self.pal_list.item(row)
        source_index = item.data(Qt.UserRole) if item else None
        if not isinstance(source_index, int) or not 0 <= source_index < len(self.instances):
            self._restore_roster_selection(self.current_index)
            return
        if source_index == self.current_index:
            displayed_identity = str(self.instance_label.property("technical_value") or "")
            target_identity = str(self.instances[source_index].instance_id or "")
            if self.current_instance_id == target_identity and displayed_identity == target_identity:
                return
            # The source collection was rebuilt while retaining the same row
            # number.  Treat it as a fresh selection rather than syncing the
            # old form into the replacement collection.
            self.current_index = -1
        previous_index = self.current_index
        if previous_index >= 0:
            try:
                self._sync_draft_from_form()
            except Exception as exc:
                self._restore_roster_selection(previous_index)
                self._show_transient_status(f"Could not preserve the current Pal draft: {exc}")
                return
        try:
            self._populate_selected_pal(source_index)
        except Exception as exc:
            self._restore_roster_selection(previous_index)
            self._show_transient_status(f"Could not switch Pal: {exc}")

    def _restore_roster_selection(self, source_index: int) -> None:
        """Restore the visible selection without re-entering select_pal."""

        previous_row = self._visible_row_for_source(source_index)
        if previous_row >= 0:
            self.pal_list.blockSignals(True)
            self.pal_list.setCurrentRow(previous_row)
            self.pal_list.blockSignals(False)

    def _stable_identity_for_index(self, source_index: int) -> str:
        if not 0 <= source_index < len(self.instances):
            raise SaveEditError("The selected Pal is no longer available")
        identity = str(self.instances[source_index].instance_id or "").strip()
        if not identity:
            raise SaveEditError(
                "This Pal has no stable instance identity; its draft cannot be edited safely."
            )
        matches = [
            instance for instance in self.instances
            if str(instance.instance_id or "").strip() == identity
        ]
        if len(matches) != 1:
            raise SaveEditError(
                f"The Pal identity {identity} is not unique; its draft cannot be edited safely."
            )
        return identity

    def _draft_template_for_index(self, source_index: int) -> PalTemplate:
        instance = self.instances[source_index]
        identity = self._stable_identity_for_index(source_index)
        entry = self.ledger.draft_for(identity) if self.ledger is not None else None
        if entry is None:
            return instance.template
        return replace(instance.template, **entry.after_fields)

    def _display_context_for_index(self, source_index: int) -> str:
        instance = self.instances[source_index]
        species = instance.template.species or "Unknown species"
        nickname = instance.template.nickname.strip()
        context = f"{species}"
        if nickname:
            context += f" ({nickname})"
        location = self._friendly_location(instance)
        if location != "Unavailable":
            context += f", {location}"
        return context

    def _populate_selected_pal(self, source_index: int) -> None:
        """Populate the complete editor from source or this Pal's pending draft."""

        template = self._draft_template_for_index(source_index)
        instance = self.instances[source_index]
        self._suppress_form_sync = True
        try:
            self.current_index = source_index
            self.current_instance_id = self._stable_identity_for_index(source_index)
            self._set_combo_code(self.species_combo, template.species)
            self._species_loaded_code = instance.template.species
            self.nickname_edit.setText(template.nickname)
            self._set_combo_code(self.gender_combo, template.gender)
            self._set_technical_value(self.instance_label, instance.instance_id)
            self._set_technical_value(self.owner_label, instance.owner_uid)
            self._set_technical_value(self.player_uid_label, instance.player_uid)
            self._set_technical_value(self.container_label, instance.container_id)
            self._set_technical_value(
                self.slot_label,
                str(instance.slot_index) if instance.slot_index is not None else None,
            )
            self.level_spin.setValue(template.level or 1)
            self.rank_spin.setValue(template.rank or 0)
            self.iv_hp_spin.setValue(template.iv_hp or 0)
            self.iv_attack_spin.setValue(template.iv_attack or 0)
            self.iv_defense_spin.setValue(template.iv_defense or 0)
            self.active_initial = list(template.active_skills)
            self.passive_initial = list(template.passive_skills)
            for index, selector in enumerate(self.active_selectors):
                self._set_combo_code(
                    selector,
                    template.active_skills[index] if index < len(template.active_skills) else None,
                    self.catalog.attacks,
                )
            for index, selector in enumerate(self.passive_selectors):
                self._set_combo_code(
                    selector,
                    template.passive_skills[index] if index < len(template.passive_skills) else None,
                    self.catalog.passives,
                )
        finally:
            self._suppress_form_sync = False
        self._refresh_overview_summary()
        self._refresh_selected_pending_message()
        self.refresh_blueprint_impact()
        self._update_action_states()

    def _visible_row_for_source(self, source_index: int) -> int:
        for row in range(self.pal_list.count()):
            item = self.pal_list.item(row)
            if item and item.data(Qt.UserRole) == source_index:
                return row
        return -1

    def _clear_selected_form(self) -> None:
        """Clear stale editor fields when the roster has no active selection."""
        self.species_combo.setCurrentIndex(0)
        self.current_instance_id = None
        self._species_loaded_code = None
        self.nickname_edit.clear()
        self.gender_combo.setCurrentIndex(0)
        self._set_technical_value(self.instance_label, None)
        self._set_technical_value(self.owner_label, None)
        self._set_technical_value(self.player_uid_label, None)
        self._set_technical_value(self.container_label, None)
        self._set_technical_value(self.slot_label, None)
        self.level_spin.setValue(1)
        self.rank_spin.setValue(0)
        self.iv_hp_spin.setValue(0)
        self.iv_attack_spin.setValue(0)
        self.iv_defense_spin.setValue(0)
        self._refresh_overview_summary()
        self.active_initial = []
        self.passive_initial = []
        for selector in (*self.active_selectors, *self.passive_selectors):
            selector.setCurrentIndex(0)
        self._set_source_draft_message("No Pal selected. Select a Pal to compare source and draft values.")
        self._refresh_ledger_page()

    def _refresh_overview_summary(self) -> None:
        """Refresh the compact Overview without changing the editable form state."""

        source = (
            self.instances[self.current_index].template
            if 0 <= self.current_index < len(self.instances)
            else None
        )
        draft = self.form_template() if source is not None else None
        values = {
            field_name: getattr(draft, field_name) if draft is not None else None
            for field_name in ("level", "rank", "iv_hp", "iv_attack", "iv_defense")
        }
        labels = (
            ("level", self.overview_level_label),
            ("rank", self.overview_rank_label),
            ("iv_hp", self.overview_iv_hp_label),
            ("iv_attack", self.overview_iv_attack_label),
            ("iv_defense", self.overview_iv_defense_label),
        )
        for field_name, label in labels:
            old = getattr(source, field_name) if source is not None else None
            label.setText(self._state_text(old, values[field_name]))
        if draft is None:
            active_values: list[str] = []
            passive_values: list[str] = []
            self.overview_location_label.setText("Unavailable")
        else:
            active_values = list(draft.active_skills)
            passive_values = list(draft.passive_skills)
            instance = self.instances[self.current_index]
            self.overview_location_label.setText(self._friendly_location(instance))
            self.overview_location_label.setToolTip(
                "Friendly location inferred only from reliable slot metadata."
            )
        self._set_skill_summary(self.overview_active_label, active_values, "No active skills")
        self._set_skill_summary(self.overview_passive_label, passive_values, "No passive skills")
        self._refresh_portrait_for_current_species()

    def _set_skill_summary(
        self,
        label: QLabel,
        codes: list[str],
        empty_text: str,
    ) -> None:
        if not codes:
            label.setText(empty_text)
            label.setToolTip(empty_text)
            return
        friendly = [self._display_code(code) for code in codes]
        label.setText(", ".join(friendly))
        label.setToolTip("\n".join(friendly))

    @staticmethod
    def _friendly_location(instance: PalInstance) -> str:
        if instance.slot_index is not None:
            return f"Stored container slot {instance.slot_index}"
        if instance.container_id:
            return "Assigned container"
        return "Unavailable"

    @staticmethod
    def _state_text(source: object, draft: object) -> str:
        if source == draft:
            return "Unavailable" if draft is None else str(draft)
        old = "Unavailable" if source is None else str(source)
        new = "Unavailable" if draft is None else str(draft)
        return f"{old} -> {new}"

    def form_template(self) -> PalTemplate:
        if self.current_index < 0:
            raise SaveEditError("Select a Pal first")
        base = self.instances[self.current_index].template
        rank_value = self.rank_spin.value()
        selected_species = str(
            self.species_combo.currentData() or self.species_combo.currentText()
        ).strip()
        if (
            self._species_loaded_code
            and selected_species.casefold() == self._species_loaded_code.casefold()
        ):
            selected_species = self._species_loaded_code
        return replace(
            base,
            species=selected_species,
            nickname=self.nickname_edit.text(),
            gender=str(self.gender_combo.currentData() or "").strip() or None,
            level=self.level_spin.value(),
            rank=rank_value if (base.rank is not None or rank_value != 0) else None,
            iv_hp=self.iv_hp_spin.value(),
            iv_attack=self.iv_attack_spin.value(),
            iv_defense=self.iv_defense_spin.value(),
            active_skills=self._selector_values(self.active_selectors, self.active_initial),
            passive_skills=self._selector_values(self.passive_selectors, self.passive_initial),
        )

    def apply_selected_preset(self) -> None:
        key = self.preset_combo.currentData()
        if not key:
            return
        if not any(
            (
                self.scope_level.isChecked(),
                self.scope_rank.isChecked(),
                self.scope_attributes.isChecked(),
                self.scope_active_skills.isChecked(),
                self.scope_passives.isChecked(),
            )
        ):
            self._show_transient_status("Choose at least one blueprint scope option before applying")
            return
        try:
            scope = PresetScope(
                level=self.scope_level.isChecked(),
                rank=self.scope_rank.isChecked(),
                attributes=self.scope_attributes.isChecked(),
                active_skills=self.scope_active_skills.isChecked(),
                passives=self.scope_passives.isChecked(),
            )
            template = apply_preset(self.form_template(), str(key), scope)
            report = validate_template(template, mode=template.validation_mode)  # type: ignore[arg-type]
            catalog_warnings = self._catalog_blueprint_warnings(
                tuple(next(item for item in PRESETS if item.key == key).active_skills),
                tuple(next(item for item in PRESETS if item.key == key).passive_skills),
            )
            if not report.valid or any(message.startswith("Rejected ") for message in catalog_warnings):
                details = [issue.message for issue in report.errors] + list(catalog_warnings)
                QMessageBox.warning(self, "Blueprint rejected", "\n".join(details))
                return
            self._record_validation(report)
        except Exception as exc:
            QMessageBox.warning(self, "Could not apply preset", str(exc))
            return
        self.level_spin.setValue(template.level or 1)
        self.rank_spin.setValue(template.rank or 0)
        self.iv_hp_spin.setValue(template.iv_hp or 0)
        self.iv_attack_spin.setValue(template.iv_attack or 0)
        self.iv_defense_spin.setValue(template.iv_defense or 0)
        if scope.active_skills:
            for index, selector in enumerate(self.active_selectors):
                self._set_combo_code(
                    selector,
                    template.active_skills[index] if index < len(template.active_skills) else None,
                    self.catalog.attacks,
                )
        if scope.passives_enabled:
            for index, selector in enumerate(self.passive_selectors):
                self._set_combo_code(
                    selector,
                    template.passive_skills[index] if index < len(template.passive_skills) else None,
                    self.catalog.passives,
                )
        preset = next(item for item in PRESETS if item.key == key)
        selected_scope = ", ".join(
            label
            for enabled, label in (
                (scope.level, "level"),
                (scope.rank, "rank"),
                (scope.attributes, "IVs"),
                (scope.active_skills, "active skills"),
                (scope.passives_enabled, "passives"),
            )
            if enabled
        ) or "no fields"
        self._show_transient_status(
            f"Applied {preset.label} to {selected_scope}: {preset.description}"
        )
        self.preset_combo.setCurrentIndex(0)

    def update_blueprint_scope(self, _index: int) -> None:
        """Show the fields relevant to the selected blueprint without hiding scope controls."""
        key = self.preset_combo.currentData()
        self.preset_combo.setToolTip(
            self.preset_combo.currentText()
            if key
            else "Choose a blueprint to see its affected fields."
        )
        self.scope_level.setChecked(key in {"max_level", "combat_max"})
        self.scope_rank.setChecked(key in {"rank_max"})
        self.scope_attributes.setChecked(key in {"max_iv", "combat_max"})
        selected = next((preset for preset in PRESETS if preset.key == key), None)
        has_active = bool(selected and selected.active_skills)
        has_passives = bool(selected and selected.passive_skills)
        self.scope_active_skills.setEnabled(has_active and not self.reference_only)
        self.scope_passives.setEnabled(has_passives and not self.reference_only)
        self.scope_active_skills.setChecked(has_active)
        self.scope_passives.setChecked(has_passives)
        self.apply_preset_button.setEnabled(
            bool(selected and self.current_index >= 0 and not self.reference_only and (has_active or has_passives or key in {"max_iv", "max_level", "rank_max", "combat_max"}))
        )
        self.refresh_blueprint_impact()

    def refresh_blueprint_impact(self, _state: int | None = None) -> None:
        key = self.preset_combo.currentData()
        if not key:
            self.apply_preset_button.setEnabled(False)
            self.scope_active_skills.setEnabled(False)
            self.scope_passives.setEnabled(False)
            self.blueprint_impact.setText("Select a Pal and blueprint to see exact pending changes.")
            return
        scope = PresetScope(
            level=self.scope_level.isChecked(),
            rank=self.scope_rank.isChecked(),
            attributes=self.scope_attributes.isChecked(),
            active_skills=self.scope_active_skills.isChecked(),
            passives=self.scope_passives.isChecked(),
        )
        self.apply_preset_button.setEnabled(
            bool(self.current_index >= 0 and not self.reference_only and any((
                scope.level,
                scope.rank,
                scope.attributes,
                scope.active_skills,
                scope.passives_enabled,
            )))
        )
        if self.current_index < 0:
            self.blueprint_impact.setText("Select a Pal before applying or evaluating this blueprint.")
            return
        try:
            source = self.form_template()
            candidate = apply_preset(source, str(key), scope)
            preset = next(item for item in PRESETS if item.key == key)
            self.blueprint_impact.setText("\n".join(self._blueprint_impact_lines(source, candidate, preset, scope)))
        except Exception as exc:
            self.blueprint_impact.setText(f"Evaluation rejected: {exc}")

    def _blueprint_impact_lines(
        self,
        source: PalTemplate,
        candidate: PalTemplate,
        preset: object,
        scope: PresetScope,
    ) -> tuple[str, ...]:
        """Render exact blueprint effects without changing the current draft."""

        lines = [f"{getattr(preset, 'label', 'Blueprint')}: pending impact"]

        def scalar(field_name: str, label: str, enabled: bool) -> None:
            old = getattr(source, field_name)
            new = getattr(candidate, field_name)
            if not enabled:
                lines.append(f"{label}: skipped (scope off)")
            elif old == new:
                lines.append(f"{label}: unchanged ({self._display_value(old)})")
            else:
                lines.append(
                    f"{label}: {self._display_value(old)} -> {self._display_value(new)}"
                )

        scalar("level", "Level", scope.level)
        scalar("rank", "Rank", scope.rank)
        for field_name, label in (
            ("iv_hp", "HP IV"),
            ("iv_attack", "Attack IV"),
            ("iv_defense", "Defense IV"),
        ):
            scalar(field_name, label, scope.attributes)

        active = tuple(getattr(preset, "active_skills", ()))
        if not scope.active_skills:
            lines.append("Active skills: skipped (scope off)")
        elif not active:
            lines.append("Active skills: unchanged (blueprint provides none)")
        else:
            lines.extend(self._slot_change_lines("Active Skill", source.active_skills, candidate.active_skills, 3))

        passives = tuple(getattr(preset, "passive_skills", ()))
        if not scope.passives_enabled:
            lines.append("Passives: skipped (scope off)")
        elif not passives:
            lines.append("Passives: unchanged (blueprint provides none)")
        else:
            lines.extend(self._slot_change_lines("Passive", source.passive_skills, candidate.passive_skills, 4))

        report = validate_template(candidate, mode=candidate.validation_mode)
        lines.append(f"Validation: {'PASS' if report.valid else 'FAIL'}")
        lines.extend(f"{issue.severity.capitalize()}: {issue.message}" for issue in report.issues)
        lines.append(
            "Compatibility: catalog restrictions are limited; species-specific active-skill/passive compatibility requires manual review."
        )
        lines.extend(self._catalog_blueprint_warnings(active, passives))
        return tuple(lines)

    def _slot_change_lines(
        self,
        label: str,
        source_values: list[str],
        draft_values: list[str],
        slots: int,
    ) -> tuple[str, ...]:
        lines: list[str] = []
        for index in range(slots):
            old = source_values[index] if index < len(source_values) else None
            new = draft_values[index] if index < len(draft_values) else None
            lines.append(
                f"{label} {index + 1}: {self._display_code(old)} -> {self._display_code(new)}"
                if old != new
                else f"{label} {index + 1}: unchanged ({self._display_code(old)})"
            )
        return tuple(lines)

    def _catalog_blueprint_warnings(
        self,
        active_codes: tuple[str, ...],
        passive_codes: tuple[str, ...],
    ) -> tuple[str, ...]:
        warnings: list[str] = []
        active_catalog = {entry.code: entry for entry in self.catalog.attacks}
        passive_catalog = {entry.code: entry for entry in self.catalog.passives}
        standard_passives = {entry.code for entry in self.catalog.standard_passives}
        for code in active_codes:
            if code not in active_catalog:
                warnings.append(f"Rejected active skill: unavailable ({code})")
        for code in passive_codes:
            if code not in passive_catalog:
                warnings.append(f"Rejected passive: unavailable ({code})")
            elif code not in standard_passives:
                warnings.append(f"Internal-only passive: {self._display_code(code)}")
        return tuple(warnings)

    def _display_code(self, value: str | None) -> str:
        if not value:
            return "None"
        for entries in (self.catalog.attacks, self.catalog.passives):
            entry = self.catalog.entry(entries, value)
            if entry is not None:
                return entry.label
        return f"Unavailable [{value}]"

    @staticmethod
    def _display_value(value: object) -> str:
        return "Unavailable" if value is None else str(value)

    def _set_source_draft_message(self, message: str) -> None:
        """Keep the source-versus-draft state visible on every editor tab."""
        self.source_draft_label.setText(message)

    def _refresh_selected_pending_message(self) -> None:
        if self.reference_only and self.current_index >= 0:
            total = self.ledger.total_changed_field_count if self.ledger is not None else 0
            pals = self.ledger.pending_pal_count if self.ledger is not None else 0
            if total:
                self._set_source_draft_message(
                    "Loaded source is read only while Palworld is running. "
                    f"{total} pending change{'s' if total != 1 else ''} across "
                    f"{pals} edited Pal{'s' if pals != 1 else ''} are preserved."
                )
            else:
                self._set_source_draft_message(
                    "Loaded source is read only while Palworld is running. No pending changes."
                )
            return
        if self.current_index < 0:
            self._set_source_draft_message(
                "No Pal selected. Select a Pal to compare source and draft values."
            )
            return
        if self.ledger is None:
            self._set_source_draft_message("No pending changes. Draft matches the source.")
            return
        try:
            identity = self._stable_identity_for_index(self.current_index)
        except SaveEditError as exc:
            self._set_source_draft_message(str(exc))
            return
        entry = self.ledger.draft_for(identity)
        if entry is not None:
            lines = [
                f"{self._friendly_field_name(change.name)}: "
                f"{self._draft_field_value(change.before)} -> "
                f"{self._draft_field_value(change.after)}"
                for change in entry.changes
            ]
            self.source_draft_label.setToolTip("\n".join(lines))
            self._set_source_draft_message("\n".join(lines) or "No pending changes for this Pal.")
            return
        total = self.ledger.total_changed_field_count
        others = self.ledger.pending_pal_count
        if total:
            self._set_source_draft_message(
                f"No pending changes for this Pal. {total} change"
                f"{'s' if total != 1 else ''} are pending across {others} other Pal"
                f"{'s' if others != 1 else ''}."
            )
        else:
            self._set_source_draft_message("No pending changes. Draft matches the source.")

    def _pending_batch(self) -> tuple[BatchEdit, ...]:
        """Synchronize the active form and return an immutable edit snapshot."""

        self._sync_draft_from_form()
        if self.ledger is None:
            return ()
        edits: list[BatchEdit] = []
        seen: set[str] = set()
        for entry in self.ledger.pending_entries:
            identity = str(entry.instance_id).strip()
            matches = [
                index for index, instance in enumerate(self.instances)
                if str(instance.instance_id or "").strip() == identity
            ]
            if not identity or len(matches) != 1:
                raise SaveEditError(
                    f"Pending Pal identity {identity or '<missing>'} is missing or not unique."
                )
            if identity in seen:
                raise SaveEditError(f"Duplicate pending Pal identity: {identity}")
            seen.add(identity)
            index = matches[0]
            template = replace(self.instances[index].template, **entry.after_fields)
            edits.append(
                BatchEdit(
                    identity,
                    template,
                    entry.display_context or self._display_context_for_index(index),
                )
            )
        return tuple(edits)

    def _sync_draft_from_form(self, *_args: object) -> None:
        """Record the current form as a draft without mutating the loaded instance."""
        if self._suppress_form_sync:
            return
        self._refresh_overview_summary()
        if self.reference_only or self.ledger is None or self.current_index < 0:
            self._refresh_selected_pending_message()
            self._refresh_global_shell_state()
            return
        identity = self._stable_identity_for_index(self.current_index)
        before = self.instances[self.current_index].template.to_dict()
        after = self.form_template().to_dict()
        self.ledger.record_pal_draft(
            identity,
            before,
            after,
            source_index=self.current_index,
            display_context=self._display_context_for_index(self.current_index),
        )
        self._refresh_selected_pending_message()
        self._refresh_ledger_page()

    def _pending_change_lines(
        self,
        before: dict[str, object],
        after: dict[str, object],
    ) -> list[str]:
        return [
            f"{self._friendly_field_name(field)}: "
            f"{self._draft_field_value(before.get(field))} -> "
            f"{self._draft_field_value(after.get(field))}"
            for field in before
            if before.get(field) != after.get(field)
        ]

    def _draft_field_value(self, value: object) -> str:
        if isinstance(value, list):
            return ", ".join(self._display_code(str(item)) for item in value) or "None"
        return self._display_value(value)

    def _record_validation(self, report: object) -> None:
        if self.ledger is None:
            return
        self.ledger.set_validation_messages(
            getattr(issue, "message", str(issue))
            for issue in getattr(report, "issues", ())
        )
        self._refresh_ledger_page()

    def changes(self) -> list[tuple[str, object, object]]:
        if self.current_index < 0:
            return []
        before = self.instances[self.current_index].template
        after = self.form_template()
        self._sync_draft_from_form()
        self.refresh_blueprint_impact()
        result: list[tuple[str, object, object]] = []
        for field in fields(PalTemplate):
            old = getattr(before, field.name)
            new = getattr(after, field.name)
            if old != new:
                result.append((field.name, old, new))
        return result

    def preview_changes(self) -> None:
        try:
            edits = self._pending_batch()
            report = validate_edit_batch(edits)
            self._record_validation(report)
        except Exception as exc:
            QMessageBox.warning(self, "Cannot preview", str(exc))
            return
        lines = [f"Validation: {'PASS' if report.valid else 'FAIL'}"]
        lines += [f"{issue.severity.upper()}: {issue.message}" for issue in report.issues]
        lines.append("")
        lines.append("Pending changes by Pal (source -> draft):")
        if not edits:
            lines.append("No pending changes.")
        contexts = [edit.display_context or edit.instance_id for edit in edits]
        ambiguous_contexts = {context for context in contexts if contexts.count(context) > 1}
        technical_lines: list[str] = []
        for edit in edits:
            context = edit.display_context or "Unknown Pal"
            suffix = f" ({str(edit.instance_id)[:8]})" if context in ambiguous_contexts else ""
            lines.append(f"{context}{suffix}")
            technical_lines.append(f"{context}: {edit.instance_id}")
            before = self.instances[
                next(
                    index for index, instance in enumerate(self.instances)
                    if str(instance.instance_id) == str(edit.instance_id)
                )
            ].template
            for field in fields(PalTemplate):
                old = getattr(before, field.name)
                new = getattr(edit.template, field.name)
                if old != new:
                    lines.append(
                        f"  {self._friendly_field_name(field.name)}: "
                        f"{self._draft_field_value(old)} -> {self._draft_field_value(new)}"
                    )
        total = sum(len(entry.changed_fields) for entry in self.ledger.pending_entries) if self.ledger else 0
        pals = self.ledger.pending_pal_count if self.ledger else 0
        lines.append("")
        lines.append(f"Total: {total} changed field{'s' if total != 1 else ''} across {pals} Pal{'s' if pals != 1 else ''}.")
        lines.append("")
        lines.append(
            "Direct Save creates an automatic verified backup before replacing the loaded source. "
            "Save a Copy remains available."
        )
        if technical_lines:
            lines.extend(("", "Technical details:", *technical_lines))
        self._show_text_dialog("Review Changes", "\n".join(lines))

    @staticmethod
    def _friendly_field_name(name: str) -> str:
        return {
            "iv_hp": "HP IV",
            "iv_attack": "Attack IV",
            "iv_defense": "Defense IV",
            "active_skills": "Active Skills",
            "passive_skills": "Passives",
        }.get(name, name.replace("_", " ").title())

    def _verify_export(self, output_path: Path, result: object, template: PalTemplate) -> str:
        """Compatibility wrapper for the batch export verification path."""
        instance_id = str(getattr(result, "instance_id", ""))
        return self._verify_batch_export(
            output_path,
            result,
            (BatchEdit(instance_id, template),),
        )

    def _verify_batch_export(
        self,
        output_path: Path,
        result: object,
        edits: tuple[BatchEdit, ...],
    ) -> str:
        """Reload one copy and confirm every requested batch field survived."""
        report = inspect(output_path)
        results = result.results if isinstance(result, BatchEditResult) else (result,)
        if len(results) != len(edits):
            raise SaveEditError("The exported copy did not return one result per edited Pal")
        mismatches: list[str] = []
        total_fields = 0
        for edit, edit_result in zip(edits, results):
            record = next(
                (pal for pal in report.get("pals", []) if str(pal.get("instance_id")) == str(edit.instance_id)),
                None,
            )
            if record is None:
                raise SaveEditError(
                    f"The exported copy could not reload edited Pal {edit.instance_id}."
                )
            checks = {
                "CharacterID": ("species", edit.template.species),
                "NickName": ("nickname", edit.template.nickname),
                "Gender": ("gender", edit.template.gender),
                "Level": ("level", edit.template.level),
                "Rank": ("rank", edit.template.rank),
                "Exp": ("xp", edit.template.xp),
                "Hp": ("hp", edit.template.hp),
                "Talent_HP": ("iv_hp", edit.template.iv_hp),
                "Talent_Shot": ("iv_attack", edit.template.iv_attack),
                "Talent_Defense": ("iv_defense", edit.template.iv_defense),
                "EquipWaza": ("active_skills", edit.template.active_skills),
                "PassiveSkillList": ("passives", edit.template.passive_skills),
            }
            total_fields += len(getattr(edit_result, "changed_fields", ()))
            for field_name in getattr(edit_result, "changed_fields", ()):
                if field_name not in checks:
                    continue
                record_name, expected = checks[field_name]
                actual = record.get(record_name)
                if actual != expected:
                    mismatches.append(
                        f"{edit.instance_id} {field_name}: expected {expected!r}, reloaded {actual!r}"
                    )
        if mismatches:
            raise SaveEditError("Export verification failed: " + "; ".join(mismatches))
        return (
            f"Verified after reload: {total_fields} changed field"
            f"{'s' if total_fields != 1 else ''} across {len(edits)} Pal"
            f"{'s' if len(edits) != 1 else ''}"
        )

    def save(self) -> bool:
        """Save the complete pending draft through one coordinator transaction."""

        if (
            not self.source_path
            or self.source_baseline is None
            or self.ledger is None
            or not self.ledger.dirty
            or self._direct_save_active
        ):
            return False
        if not self._require_game_closed("saving the loaded source"):
            return False

        try:
            edits = self._pending_batch()
            if not edits:
                return False
            selected_instance_id = self._stable_identity_for_index(self.current_index)
        except Exception as exc:
            self.ledger.record_operation("transaction_failed", str(exc))
            self._refresh_ledger_page()
            QMessageBox.warning(self, "Cannot save", str(exc))
            return False

        request = DirectSaveRequest(
            source_path=self.source_path,
            baseline=self.source_baseline,
            edits=edits,
        )
        self._direct_save_active = True
        self._update_action_states()
        try:
            result = self.direct_save_coordinator.run(request)
            self.last_direct_save_result = result
            report = result.validation_report
            if report is not None:
                self._record_validation(report)
            if report is not None and not report.valid:
                self.ledger.record_operation(
                    "validation_failed",
                    "; ".join(issue.message for issue in report.errors),
                )
                self._refresh_ledger_page()
                QMessageBox.warning(
                    self,
                    "Validation failed",
                    "\n".join(issue.message for issue in report.errors),
                )
                return False

            if not result.success:
                self._handle_direct_save_failure(result)
                return False

            reloaded = self.load_path(
                self.source_path,
                preferred_instance_id=selected_instance_id,
            )
            if not reloaded:
                # The file was verified on disk, but the in-memory editor could
                # not refresh.  Keep the current draft and make the limitation
                # explicit instead of falsely marking it clean.
                if self.ledger is not None:
                    self.ledger.record_operation(
                        "save_refresh_failed",
                        "The source was verified, but the editor could not reload it.",
                    )
                    self._refresh_ledger_page()
                QMessageBox.warning(
                    self,
                    "Save verified; reload needed",
                    "The source save was replaced and verified, but Pal Admin could not reload it. "
                    "Use Reload Source Save before making another direct save.",
                )
                return False

            if self.ledger is not None:
                status = (
                    "save_succeeded_prune_warning"
                    if result.pruning_warning
                    else "save_succeeded"
                )
                self.ledger.record_operation(status, result.pruning_warning or "")
                self._refresh_ledger_page()
            message = "Source save replaced and verified. An automatic backup was retained."
            if result.pruning_warning:
                message += f"\n\n{result.pruning_warning}"
            QMessageBox.information(self, "Save complete", message)
            self._show_transient_status(
                "Source save replaced and verified"
                + ("; backup retention warning" if result.pruning_warning else "")
            )
            return True
        finally:
            self._direct_save_active = False
            self._update_action_states()

    def _handle_direct_save_failure(self, result: object) -> None:
        """Present coordinator failure states without changing the draft."""

        recovery = getattr(result, "recovery_result", RecoveryResult.UNCERTAIN)
        primary = getattr(result, "primary_failure", None) or "The transaction did not complete."
        cleanup = getattr(result, "cleanup_failure", None)
        details = primary + (f"\n\nCleanup detail: {cleanup}" if cleanup else "")
        backup_path = getattr(result, "backup_path", None)
        if backup_path:
            details += f"\n\nVerified backup retained at: {backup_path}"
        transaction = getattr(result, "transaction", None)
        failure_stage = getattr(transaction, "failure_stage", None)
        if failure_stage in {
            FailureStage.SOURCE_FINGERPRINT,
            FailureStage.FINAL_SOURCE_VERIFICATION,
        }:
            status = "source_changed"
            title = "Source changed since load"
            text = (
                details
                + "\n\nThe loaded source was not overwritten. Use Reload Source Save or Save a Copy before retrying."
            )
            level = QMessageBox.Icon.Warning
        elif recovery is RecoveryResult.RESTORED:
            status = "restored"
            title = "Save not completed; source restored"
            text = details + "\n\nThe original source was restored. Your draft remains available."
            level = QMessageBox.Icon.Warning
        elif recovery in {RecoveryResult.FAILED, RecoveryResult.UNCERTAIN}:
            status = "uncertain"
            title = "Save outcome uncertain"
            text = (
                details
                + "\n\nDo not launch Palworld or make another direct save until the source is inspected and recovered."
            )
            level = QMessageBox.Icon.Critical
        else:
            status = "transaction_failed"
            title = "Save not completed"
            text = details + "\n\nThe source was not replaced. Your draft remains available."
            level = QMessageBox.Icon.Warning
        if self.ledger is not None:
            self.ledger.record_operation(status, text)
            self._refresh_ledger_page()
        box = QMessageBox(self)
        box.setIcon(level)
        box.setWindowTitle(title)
        box.setText(text)
        box.exec()

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - Qt override
        """Use the same draft guard for menu Exit and native window close."""

        if self._guard_pending_operation("exit", lambda: True):
            event.accept()
        else:
            event.ignore()

    def save_copy(self) -> None:
        if not self.source_path or self.ledger is None or self.current_index < 0:
            return
        if not self._require_game_closed("creating a save copy"):
            return
        try:
            edits = self._pending_batch()
            report = validate_edit_batch(edits)
            self._record_validation(report)
            if not report.valid:
                QMessageBox.warning(
                    self,
                    "Validation failed",
                    "\n".join(issue.message for issue in report.errors),
                )
                return
        except Exception as exc:
            QMessageBox.warning(self, "Cannot save", str(exc))
            return

        export_dir = Path.home() / "Documents" / "PalAdmin"
        default = export_dir / f"{self.source_path.stem}_edited.sav"
        output, _ = QFileDialog.getSaveFileName(
            self,
            "Save edited copy",
            str(default),
            "Palworld save (*.sav);;All files (*.*)",
        )
        if not output:
            return
        output_path = Path(output)
        backup_path: Path | None = None
        backup_policy = BackupPolicy.ALWAYS
        if self.ledger is not None:
            backup_policy = self.ledger.backup_policy
        if backup_policy is BackupPolicy.ASK:
            answer = QMessageBox.question(
                self,
                "Create source safety copy?",
                "Create a timestamped source safety copy before writing the edited copy?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )
            if answer == QMessageBox.No:
                backup_policy = BackupPolicy.OFF
        if backup_policy is not BackupPolicy.OFF and self.ledger is not None:
            backup_path = self.ledger.new_backup_path(
                backup_dir=output_path.parent / "PalAdminBackups"
            )
        try:
            result = edit_save_copy_batch(
                self.source_path,
                output_path,
                edits,
                backup_path=backup_path,
            )
        except Exception as exc:
            QMessageBox.critical(self, "Could not save copy", str(exc))
            return
        try:
            verification = self._verify_batch_export(output_path, result, edits)
        except Exception as exc:
            QMessageBox.critical(
                self,
                "Export verification failed",
                f"The edited copy was written, but it did not pass reload verification.\n\n{exc}\n\n"
                "The original source save was not changed. Inspect or remove the output before using it.",
            )
            return
        self.target_path = output_path.resolve()
        if self.ledger is not None:
            self.ledger.set_target_path(self.target_path)
        self._refresh_ledger_page()
        total_fields = sum(len(item.changed_fields) for item in result.results)
        edited_pals = len(result.results)
        lines = [
            "The edited copy was created and verified. The loaded source and current draft were not changed.",
            "",
            f"Created:\n{result.output_path}",
        ]
        if result.backup_path:
            lines.extend(
                (
                    "",
                    f"Source safety copy:\n{result.backup_path}",
                )
            )
        lines.extend(
            (
                "",
                f"Changed fields: {total_fields}",
                f"Edited Pals: {edited_pals}",
                "",
                verification,
                "The current draft remains pending and Save is still available.",
            )
        )
        self._show_text_dialog("Save copy complete", "\n".join(lines), minimum_size=(720, 420))
        self._refresh_global_shell_state()
        self._show_transient_status("Save copy created; the current draft remains pending")


def main() -> int:
    app = QApplication(sys.argv)
    initial = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    window = PalEditorWindow(initial)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
