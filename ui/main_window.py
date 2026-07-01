from __future__ import annotations

import base64
import json
import logging
import os
import sys
import time
from dataclasses import asdict
from pathlib import Path

from PySide6.QtCore import QByteArray, QObject, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QAction, QBrush, QColor, QCloseEvent, QKeyEvent, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDockWidget,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from app.paths import logs_dir, settings_dir
from app.settings import AppSettings, DEFAULT_F1_UDP_PORT
from models import METRICS, LapResult, ReferenceLap, TelemetrySample, TelemetrySession, format_gear, format_time_ms
from telemetry import SOURCE_LABELS, SOURCE_TYPES
from telemetry.base import SourceState
from telemetry.comparison import build_comparison_series, preferred_axis, speed_delta
from telemetry.display_names import display_car_name, display_track_name
from telemetry.f1_2018 import F12018TelemetrySource
from telemetry.importer import TelemetryImportError, import_telemetry_file
from telemetry.lap_delta import completed_lap_delta_ms, format_delta_ms, live_lap_delta_ms
from telemetry.lap_comparison import aligned_metric, assert_laps_comparable, common_position_grid, sector_marker_positions, time_delta
from telemetry.lap_tracker import LapTracker
from telemetry.sector_feedback import sector_feedback
from telemetry.session_store import SessionStore
from ui.graph_panel import GraphPanel, PENS
from ui.track_map_panel import TrackMapPanel
from ui.docking import DetachedPanelWindow, install_dock_context_menu
from ui.dashboard_workspace import DashboardWorkspace
from ui.panel_registry import PanelRegistry
from ui.panel_templates import BUILTIN_LAYOUTS, PANEL_TEMPLATES, TEMPLATE_GROUPS, PanelTemplate

try:
    import pyqtgraph as pg
except ImportError:  # pragma: no cover
    pg = None


LAYOUT_SCHEMA_VERSION = 2
LIVE_LAP_NUMBER_ROLE = int(Qt.ItemDataRole.UserRole)
LIVE_LAP_STATE_ROLE = int(Qt.ItemDataRole.UserRole) + 1
LIVE_LAP_STATE_CURRENT = "current"
LIVE_LAP_STATE_COMPLETED = "completed"


class MainWindow(QMainWindow):
    def __init__(self, reset_layout: bool = False) -> None:
        super().__init__()
        self.settings = AppSettings()
        self.session_store = SessionStore()
        self.sessions: list[TelemetrySession] = self.session_store.load()
        self.lap_tracker = LapTracker()
        self.saved_laps: list[LapResult] = self.lap_tracker.storage.load_laps()
        self.active_source = None
        self.telemetry_labels: dict[str, QLabel] = {}
        self.common_labels: dict[str, QLabel] = {}
        self.f1_labels: dict[str, QLabel] = {}
        self.shared_memory_labels: dict[str, QLabel] = {}
        self.docks: dict[str, QDockWidget] = {}
        self.detached_windows: dict[str, DetachedPanelWindow] = {}
        self.panel_widgets: dict[str, QWidget] = {}
        self.panel_titles: dict[str, str] = {}
        self.panel_registry = PanelRegistry()
        self.panel_templates: dict[str, str] = {}
        self._panel_type_counters: dict[str, int] = {}
        self._layout_restore_count = 0
        self._reset_layout_requested = reset_layout
        self.graph_panels: list[GraphPanel] = []
        self.current_lap_graph_panels: list[GraphPanel] = []
        self.saved_lap_graph_panels: list[GraphPanel] = []
        self.track_map_panels: list[TrackMapPanel] = []
        self.live_value_panels: list[dict[str, QLabel]] = []
        self.live_lap_tables: list[QTableWidget] = []
        self.sector_timing_tables: list[QTableWidget] = []
        self.session_history_tables: list[tuple[QTableWidget, QTableWidget]] = []
        self.active_reference_lap: ReferenceLap | None = None
        self.reference_overlay_enabled = True
        self._reference_panel_key = ""
        self.graph_counter = 0
        self.recording_samples: list[TelemetrySample] = []
        self.is_recording = False
        self._last_sample_wall_time = 0.0
        self._sample_count_window = 0
        self._rate_window_started = time.monotonic()
        self._logger = logging.getLogger(__name__)
        self.lap_tracker.lap_completed.connect(self.handle_lap_completed)
        self.lap_tracker.lap_updated.connect(self.handle_lap_updated)
        self.lap_tracker.timing_state_changed.connect(self.handle_timing_state)
        self.lap_tracker.diagnostics_changed.connect(self.handle_diagnostics)
        self.lap_tracker.storage_error.connect(
            lambda message: self._logger.error("Lap storage error: %s", message)
        )

        self.setWindowTitle("Racing Telemetry")
        self.setMinimumSize(900, 640)
        self.setDockOptions(
            QMainWindow.DockOption.AllowNestedDocks
            | QMainWindow.DockOption.AllowTabbedDocks
            | QMainWindow.DockOption.AnimatedDocks
        )

        self._status_timer = QTimer(self)
        self._status_timer.setInterval(1000)
        self._status_timer.timeout.connect(self._refresh_live_diagnostics)

        self._build_actions()
        self._build_menus()
        self._build_toolbar()
        self._build_interface()
        self._restore_startup_layout()
        self._show_stopped_telemetry()
        self._populate_sessions_table()
        self._source_selection_changed()
        self._update_actions()

        if self.settings.fullscreen_at_startup():
            self.showFullScreen()
        elif self.settings.was_maximized():
            self.showMaximized()

    def _build_actions(self) -> None:
        self.import_action = QAction("Import telemetry...", self)
        self.import_action.setShortcut(QKeySequence("Ctrl+I"))
        self.import_action.triggered.connect(self.import_telemetry)

        self.save_recorded_action = QAction("Save recorded session...", self)
        self.save_recorded_action.triggered.connect(self.save_recorded_session)

        self.export_selected_action = QAction("Export selected session...", self)
        self.export_selected_action.triggered.connect(self.export_selected_session)

        self.exit_action = QAction("Exit", self)
        self.exit_action.triggered.connect(self.close)

        self.fullscreen_action = QAction("Fullscreen", self)
        self.fullscreen_action.setShortcut(QKeySequence("F11"))
        self.fullscreen_action.setCheckable(True)
        self.fullscreen_action.triggered.connect(self.toggle_fullscreen)

        self.add_graph_action = QAction("Add graph panel", self)
        self.add_graph_action.setShortcut(QKeySequence("Ctrl+Shift+G"))
        self.add_graph_action.triggered.connect(lambda: self.create_panel_from_template("live_graph"))

        self.add_panel_action = QAction("Add telemetry panel", self)
        self.add_panel_action.triggered.connect(self.show_panel_picker_hint)

        self.reset_layout_action = QAction("Reset layout", self)
        self.reset_layout_action.setShortcut(QKeySequence("Ctrl+0"))
        self.reset_layout_action.triggered.connect(self.reset_layout)

        self.recover_panels_action = QAction("Recover all panels", self)
        self.recover_panels_action.triggered.connect(self.recover_all_panels)

        self.save_layout_action = QAction("Save layout as...", self)
        self.save_layout_action.triggered.connect(self.save_layout_as)

        self.load_layout_action = QAction("Load layout...", self)
        self.load_layout_action.triggered.connect(self.load_layout_from_file)

        self.start_source_action = QAction("Start source", self)
        self.start_source_action.triggered.connect(self.start_selected_source)

        self.stop_source_action = QAction("Stop source", self)
        self.stop_source_action.triggered.connect(self.stop_active_source)

        self.start_recording_action = QAction("Start raw recording", self)
        self.start_recording_action.setShortcut(QKeySequence("Ctrl+R"))
        self.start_recording_action.triggered.connect(self.toggle_recording)

        self.stop_recording_action = QAction("Stop raw recording", self)
        self.stop_recording_action.triggered.connect(self.stop_recording)

        self.open_lap_graph_action = QAction("Open selected lap graph", self)
        self.open_lap_graph_action.triggered.connect(self.open_selected_lap_graph)

        self.settings_action = QAction("Application settings...", self)
        self.settings_action.triggered.connect(self.open_settings_dialog)

        self.open_logs_action = QAction("Open log directory", self)
        self.open_logs_action.triggered.connect(self.open_log_directory)

        self.diagnostics_action = QAction("Diagnostics", self)
        self.diagnostics_action.triggered.connect(self.show_diagnostics)

        self.about_action = QAction("About", self)
        self.about_action.triggered.connect(self.show_about)

        self.edit_layout_action = QAction("Edit layout", self)
        self.edit_layout_action.setShortcut(QKeySequence("Ctrl+Shift+L"))
        self.edit_layout_action.setCheckable(True)
        self.edit_layout_action.triggered.connect(self.toggle_dashboard_edit_mode)

        self.split_horizontal_action = QAction("Split selected tile horizontally", self)
        self.split_horizontal_action.setShortcut(QKeySequence("Ctrl+Shift+H"))
        self.split_horizontal_action.triggered.connect(lambda: self.split_selected_tile("right"))

        self.split_vertical_action = QAction("Split selected tile vertically", self)
        self.split_vertical_action.setShortcut(QKeySequence("Ctrl+Shift+V"))
        self.split_vertical_action.triggered.connect(lambda: self.split_selected_tile("below"))

        self.create_tab_group_action = QAction("Create tab group", self)
        self.create_tab_group_action.setShortcut(QKeySequence("Ctrl+Shift+T"))
        self.create_tab_group_action.triggered.connect(self.create_selected_tab_group)

        self.compact_panel_action = QAction("Toggle compact mode", self)
        self.compact_panel_action.setShortcut(QKeySequence("Ctrl+Shift+C"))
        self.compact_panel_action.triggered.connect(self.toggle_selected_compact_mode)

    def _build_menus(self) -> None:
        file_menu = self.menuBar().addMenu("File")
        file_menu.addAction(self.import_action)
        file_menu.addAction(self.save_recorded_action)
        file_menu.addAction(self.export_selected_action)
        file_menu.addSeparator()
        file_menu.addAction(self.exit_action)

        self.view_menu = self.menuBar().addMenu("View")
        self.view_menu.addAction(self.fullscreen_action)
        self.view_menu.addSeparator()
        self.view_menu.addAction(self.add_graph_action)
        self.add_panel_menu = self.view_menu.addMenu("Add telemetry panel")
        self._populate_add_panel_menu(self.add_panel_menu)
        self.view_menu.addAction(self.reset_layout_action)
        self.view_menu.addAction(self.recover_panels_action)

        dashboard_menu = self.menuBar().addMenu("Dashboard")
        dashboard_menu.addAction(self.edit_layout_action)
        dashboard_menu.addAction(self.split_horizontal_action)
        dashboard_menu.addAction(self.split_vertical_action)
        dashboard_menu.addAction(self.create_tab_group_action)
        dashboard_menu.addAction(self.compact_panel_action)
        dashboard_menu.addSeparator()
        dashboard_add_menu = dashboard_menu.addMenu("Add panel")
        self._populate_add_panel_menu(dashboard_add_menu)
        grids_menu = dashboard_menu.addMenu("Quick grids")
        for title, columns, rows in (
            ("1x1", 1, 1),
            ("2 columns", 2, 1),
            ("3 columns", 3, 1),
            ("2 rows", 1, 2),
            ("2x2 grid", 2, 2),
            ("3x2 grid", 3, 2),
            ("3x3 grid", 3, 3),
        ):
            action = QAction(title, self)
            action.triggered.connect(lambda _checked=False, c=columns, r=rows: self.apply_quick_grid(c, r))
            grids_menu.addAction(action)
        presets_menu = dashboard_menu.addMenu("Telemetry presets")
        for title in ("Live driving compact", "Timing wall", "Analysis workspace", "Ultrawide telemetry"):
            action = QAction(title, self)
            action.triggered.connect(lambda _checked=False, preset=title: self.apply_dashboard_preset(preset))
            presets_menu.addAction(action)

        layouts_menu = self.menuBar().addMenu("Layouts")
        for name in ("Live driving", "Timing", "Analysis", "Diagnostics"):
            action = QAction(name, self)
            action.triggered.connect(lambda _checked=False, layout_name=name: self.apply_builtin_layout(layout_name))
            layouts_menu.addAction(action)
        layouts_menu.addSeparator()
        restore_default_action = QAction("Restore default", self)
        restore_default_action.triggered.connect(self.reset_layout)
        layouts_menu.addAction(restore_default_action)
        layouts_menu.addAction(self.save_layout_action)
        layouts_menu.addAction(self.load_layout_action)

        telemetry_menu = self.menuBar().addMenu("Telemetry")
        telemetry_menu.addAction(self.start_source_action)
        telemetry_menu.addAction(self.stop_source_action)
        telemetry_menu.addSeparator()
        telemetry_menu.addAction(self.start_recording_action)
        telemetry_menu.addAction(self.stop_recording_action)
        telemetry_menu.addSeparator()
        telemetry_menu.addAction(self.open_lap_graph_action)

        settings_menu = self.menuBar().addMenu("Settings")
        settings_menu.addAction(self.settings_action)

        help_menu = self.menuBar().addMenu("Help")
        help_menu.addAction(self.open_logs_action)
        help_menu.addAction(self.diagnostics_action)
        help_menu.addAction(self.about_action)

    def _build_toolbar(self) -> None:
        toolbar = QToolBar("Main", self)
        toolbar.setMovable(False)
        toolbar.addAction(self.import_action)
        toolbar.addAction(self.fullscreen_action)
        toolbar.addAction(self.add_graph_action)
        panel_button = toolbar.addAction("Add telemetry panel")
        panel_button.triggered.connect(lambda: self.add_panel_menu.exec(toolbar.mapToGlobal(toolbar.rect().bottomLeft())))
        self.addToolBar(toolbar)

    def _populate_add_panel_menu(self, menu: QMenu) -> None:
        for group_name, template_ids in TEMPLATE_GROUPS.items():
            group_menu = menu.addMenu(group_name)
            for template_id in template_ids:
                template = PANEL_TEMPLATES[template_id]
                action = QAction(template.title, self)
                action.setStatusTip(template.description)
                action.setToolTip(template.description)
                action.triggered.connect(lambda _checked=False, item=template_id: self.create_panel_from_template(item))
                group_menu.addAction(action)

    def show_panel_picker_hint(self) -> None:
        self.add_panel_menu.exec(self.mapToGlobal(self.rect().center()))

    def _build_interface(self) -> None:
        central = QWidget(self)
        central_layout = QVBoxLayout(central)
        central_layout.setContentsMargins(4, 4, 4, 4)
        central_layout.setSpacing(4)
        source_controls = self._create_source_controls()
        source_controls.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        self.dashboard_workspace = DashboardWorkspace(self)
        self.dashboard_workspace.add_panel_requested.connect(self._add_panel_to_tile_menu)
        self.dashboard_workspace.detach_panel_requested.connect(self.detach_panel)
        self.dashboard_workspace.close_panel_requested.connect(self.close_panel)
        self.dashboard_workspace.compact_panel_requested.connect(self.set_panel_compact_mode)
        central_layout.addWidget(self.dashboard_workspace, stretch=1)
        central.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        central.customContextMenuRequested.connect(lambda position: self.add_panel_menu.exec(central.mapToGlobal(position)))
        self.setCentralWidget(central)
        self.source_controls_dock = self._add_dock(
            "telemetry_source",
            "Telemetry Source",
            source_controls,
            Qt.DockWidgetArea.LeftDockWidgetArea,
        )
        self._add_dock("live_telemetry", "Live Telemetry", self._create_telemetry_widget(), Qt.DockWidgetArea.LeftDockWidgetArea)
        self._add_dock("source_status", "Source Status", self._create_common_status_widget(), Qt.DockWidgetArea.LeftDockWidgetArea)
        self.connection_diagnostics_dock = self._add_dock(
            "connection_diagnostics",
            "Connection Diagnostics",
            self._create_connection_diagnostics_widget(),
            Qt.DockWidgetArea.RightDockWidgetArea,
        )
        self.imported_sessions_dock = self._add_dock(
            "imported_sessions",
            "Imported Sessions",
            self._create_imported_sessions_widget(),
            Qt.DockWidgetArea.BottomDockWidgetArea,
        )
        self.comparison_dock = self._add_dock(
            "comparison_graphs",
            "Comparison Graphs",
            self._create_comparison_widget(),
            Qt.DockWidgetArea.BottomDockWidgetArea,
        )
        self.laps_dock = self._add_dock(
            "laps",
            "Laps",
            self._create_laps_widget(),
            Qt.DockWidgetArea.BottomDockWidgetArea,
        )
        self.add_graph_panel("Live Graphs", panel_id="graph_panel_1", template_id="live_graph")
        self._restore_first_graph_panel_settings()

        for dock in self.docks.values():
            self.view_menu.addAction(dock.toggleViewAction())

    def _add_dock(self, object_name: str, title: str, widget: QWidget, area: Qt.DockWidgetArea) -> QDockWidget:
        if object_name in self.docks:
            self._logger.warning("Attempted to create duplicate dock %s", object_name)
            return self.docks[object_name]
        dock = QDockWidget(title, self)
        dock.setObjectName(object_name)
        dock.setWidget(widget)
        dock.setAllowedAreas(Qt.DockWidgetArea.AllDockWidgetAreas)
        dock.setFeatures(
            QDockWidget.DockWidgetFeature.DockWidgetMovable
            | QDockWidget.DockWidgetFeature.DockWidgetClosable
        )
        dock.setWindowOpacity(1.0)
        dock.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        dock.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        widget.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        widget.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.addDockWidget(area, dock)
        self.docks[object_name] = dock
        self.panel_widgets[object_name] = widget
        self.panel_titles[object_name] = title
        self.panel_registry.register(object_name, "builtin", title, dock, widget, singleton=True)
        install_dock_context_menu(self, dock)
        return dock

    def _create_source_controls(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        group = QGroupBox("Telemetry source")
        grid = QGridLayout(group)

        self.source_combo = QComboBox()
        for source_id, label in SOURCE_LABELS.items():
            self.source_combo.addItem(label, source_id)
        self.source_combo.currentIndexChanged.connect(self._source_selection_changed)

        self.port_input = QSpinBox()
        self.port_input.setRange(1, 65535)
        self.port_input.setValue(self.settings.f1_udp_port())
        self.port_input.valueChanged.connect(self._save_udp_port)

        self.port_help_label = QLabel("UDP port for UDP telemetry sources only.")
        self.port_help_label.setWordWrap(True)

        self.start_button = QPushButton("Start")
        self.stop_button = QPushButton("Stop")
        self.start_button.clicked.connect(self.start_selected_source)
        self.stop_button.clicked.connect(self.stop_active_source)

        self.record_button = QPushButton("Start raw recording")
        self.record_button.clicked.connect(self.toggle_recording)
        self.recording_label = QLabel("Raw recording: off")
        self.recording_count_label = QLabel("Samples: 0")

        self.source_label = QLabel("Source: --")
        self.status_label = QLabel("Status: Stopped")
        self.error_label = QLabel("")
        self.error_label.setWordWrap(True)
        self.error_label.setStyleSheet("color: #ff6b6b;")

        grid.addWidget(QLabel("Source:"), 0, 0)
        grid.addWidget(self.source_combo, 0, 1)
        grid.addWidget(self.start_button, 0, 2)
        grid.addWidget(self.stop_button, 0, 3)
        grid.addWidget(QLabel("UDP port:"), 1, 0)
        grid.addWidget(self.port_input, 1, 1)
        grid.addWidget(self.port_help_label, 1, 2, 1, 2)
        grid.addWidget(self.source_label, 2, 0, 1, 2)
        grid.addWidget(self.status_label, 2, 2, 1, 2)
        grid.addWidget(self.record_button, 3, 0)
        grid.addWidget(self.recording_label, 3, 1)
        grid.addWidget(self.recording_count_label, 3, 2)
        grid.addWidget(self.error_label, 4, 0, 1, 4)
        layout.addWidget(group)
        layout.addStretch(1)
        return widget

    def _create_telemetry_widget(self) -> QWidget:
        widget = QWidget()
        layout = QGridLayout(widget)
        for row, name in enumerate(["Speed", "RPM", "Gear", "Throttle", "Brake"]):
            value_label = QLabel("--")
            value_label.setMinimumWidth(90)
            value_label.setStyleSheet("font-size: 18px; font-weight: bold;")
            layout.addWidget(QLabel(f"{name}:"), row, 0)
            layout.addWidget(value_label, row, 1)
            self.telemetry_labels[name] = value_label
        return widget

    def _create_common_status_widget(self) -> QWidget:
        widget = QWidget()
        layout = QFormLayout(widget)
        fields = {
            "running": "Running:",
            "connection_state": "Connection state:",
            "latest_update": "Latest update:",
            "updates_per_second": "Updates/s:",
            "car_name": "Car:",
            "track_name": "Track:",
            "session_state": "Session state:",
            "latest_error": "Latest error:",
        }
        for key, caption in fields.items():
            label = QLabel("--")
            label.setWordWrap(True)
            layout.addRow(caption, label)
            self.common_labels[key] = label
        return self._scroll(widget)

    def _create_connection_diagnostics_widget(self) -> QWidget:
        wrapper = QWidget()
        layout = QVBoxLayout(wrapper)
        self.f1_group = QGroupBox("F1 UDP diagnostics")
        f1_layout = QFormLayout(self.f1_group)
        f1_fields = {
            "udp_status": "UDP status:",
            "parser_status": "Parser status:",
            "detected_format": "Detected format:",
            "packet_type": "Packet type:",
            "player_car_index": "Player car index:",
            "valid_telemetry_packets": "Valid telemetry packets:",
            "parser_errors": "Parser errors:",
            "packets_received": "Packets received:",
            "latest_packet_size": "Latest packet size:",
            "packet_preview": "Packet preview:",
        }
        for key, caption in f1_fields.items():
            label = QLabel("--")
            label.setWordWrap(True)
            f1_layout.addRow(caption, label)
            self.f1_labels[key] = label

        self.shared_memory_group = QGroupBox("Shared-memory status")
        sm_layout = QFormLayout(self.shared_memory_group)
        for key, caption in {
            "shared_memory": "Shared memory:",
            "game_state": "Game state:",
            "last_error": "Last error:",
            "acc_sector_index": "Raw ACC sector:",
            "acc_raw_current_lap_time_ms": "Raw current lap:",
            "acc_current_lap_time_ms": "ACC current lap:",
            "acc_raw_last_lap_time_ms": "Raw last lap:",
            "acc_last_lap_time_ms": "ACC last lap:",
            "acc_raw_best_lap_time_ms": "Raw best lap:",
            "acc_best_lap_time_ms": "ACC best lap:",
            "acc_split_ms": "ACC split:",
            "acc_last_sector_time_ms": "ACC last sector:",
            "acc_completed_laps": "ACC completed laps:",
            "acc_normalized_position": "ACC normalized position:",
            "acc_car_coordinates": "ACC car coordinates:",
            "acc_in_pit": "ACC pit:",
            "timing_state": "Timing state:",
            "timing_waiting_reason": "Timing reason:",
            "current_session_id": "Current session:",
            "current_active_lap_id": "Active lap:",
            "active_lap_samples": "Active lap samples:",
            "completed_laps_in_memory": "Laps in memory:",
            "database_path": "Database path:",
            "database_available": "Database available:",
            "completed_laps_on_disk": "Laps on disk:",
            "last_save_result": "Last save:",
            "last_save_error": "Save error:",
            "pending_storage_operations": "Pending saves:",
            "last_timing_event": "Last timing event:",
            "current_lap_graph_samples": "Current lap graph samples:",
            "current_lap_graph_start_time": "Current lap graph start:",
            "last_frozen_lap_graph_id": "Last frozen lap graph:",
            "completed_graphs_in_memory": "Completed graphs in memory:",
            "lap_graph_memory_limit": "Graph memory limit:",
            "last_graph_reset_reason": "Graph reset reason:",
        }.items():
            label = QLabel("--")
            label.setWordWrap(True)
            sm_layout.addRow(caption, label)
            self.shared_memory_labels[key] = label

        layout.addWidget(self.f1_group)
        layout.addWidget(self.shared_memory_group)
        layout.addStretch(1)
        return self._scroll(wrapper)

    def _create_imported_sessions_widget(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        self.sessions_table = QTableWidget(0, 9)
        self.sessions_table.setHorizontalHeaderLabels(
            ["Compare", "Session", "Driver", "Game", "Track", "Car", "Lap", "Duration", "Samples"]
        )
        self.sessions_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.sessions_table.itemSelectionChanged.connect(self._update_actions)
        layout.addWidget(self.sessions_table)

        buttons = QHBoxLayout()
        for text, handler in (
            ("Import", self.import_telemetry),
            ("Remove", self.remove_selected_session),
            ("Rename", self.rename_selected_session),
            ("Edit metadata", self.edit_selected_metadata),
            ("Export", self.export_selected_session),
            ("Reveal source file", self.reveal_selected_source_file),
        ):
            button = QPushButton(text)
            button.clicked.connect(handler)
            buttons.addWidget(button)
        layout.addLayout(buttons)
        return widget

    def _create_laps_widget(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)

        timing_group = QGroupBox("Live timing")
        timing_layout = QFormLayout(timing_group)
        self.lap_labels: dict[str, QLabel] = {}
        for key, caption in {
            "current_lap": "Current lap:",
            "current_sector": "Current sector:",
            "current_lap_time": "Current lap time:",
            "last_lap": "Last lap:",
            "best_lap": "Best lap:",
            "timing_scope": "Timing colors:",
        }.items():
            label = QLabel("--")
            label.setWordWrap(True)
            timing_layout.addRow(caption, label)
            self.lap_labels[key] = label
        self.lap_labels["timing_scope"].setText("Purple: fastest among loaded/current-session valid laps")
        layout.addWidget(timing_group)

        self.laps_table = QTableWidget(0, 10)
        self.laps_table.setHorizontalHeaderLabels(
            ["Lap", "Lap time", "Sector 1", "Sector 2", "Sector 3", "Delta", "Valid", "Car", "Track", "Graph"]
        )
        self.laps_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.laps_table.setSortingEnabled(True)
        layout.addWidget(self.laps_table)

        buttons = QHBoxLayout()
        for text, handler in (
            ("Compare selected laps", self.compare_selected_laps),
            ("Open lap graph", self.open_selected_lap_graph),
            ("Open track map", self.open_selected_lap_map),
            ("Delete selected lap", self.delete_selected_lap),
            ("Export selected lap", self.export_selected_lap),
        ):
            button = QPushButton(text)
            button.clicked.connect(handler)
            buttons.addWidget(button)
        layout.addLayout(buttons)
        self._populate_laps_table()
        return widget

    def _create_comparison_widget(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        controls = QHBoxLayout()
        self.comparison_metric_combo = QComboBox()
        for key, label in METRICS.items():
            self.comparison_metric_combo.addItem(label, key)
        self.comparison_axis_combo = QComboBox()
        for label, axis in (("Auto", "auto"), ("Elapsed time", "elapsed_time"), ("Session time", "session_time"), ("Lap time", "lap_time"), ("Lap distance", "lap_distance")):
            self.comparison_axis_combo.addItem(label, axis)
        self.refresh_comparison_button = QPushButton("Refresh comparison")
        self.refresh_comparison_button.clicked.connect(self.refresh_comparison_graph)
        self.allow_track_mismatch_checkbox = QCheckBox("Allow track mismatch")
        controls.addWidget(QLabel("Metric"))
        controls.addWidget(self.comparison_metric_combo)
        controls.addWidget(QLabel("X axis"))
        controls.addWidget(self.comparison_axis_combo)
        controls.addWidget(self.allow_track_mismatch_checkbox)
        controls.addWidget(self.refresh_comparison_button)
        layout.addLayout(controls)

        if pg is None:
            self.comparison_plot = None
            layout.addWidget(QLabel("pyqtgraph is not available."))
        else:
            self.comparison_plot = pg.PlotWidget()
            self.comparison_plot.addLegend()
            layout.addWidget(self.comparison_plot)
        return widget

    def _scroll(self, widget: QWidget) -> QScrollArea:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(widget)
        return scroll

    def add_graph_panel(
        self,
        title: str | None = None,
        config: dict | None = None,
        panel_id: str | None = None,
        template_id: str = "graph",
    ) -> str:
        if panel_id is None:
            panel_id = self._next_panel_id("graph_panel")
        elif panel_id in self.docks:
            self._logger.warning("Graph panel already exists: %s", panel_id)
            return panel_id
        self.graph_counter = max(self.graph_counter, self._numeric_suffix(panel_id))
        panel_title = title or f"Graph Panel {self.graph_counter}"
        panel = GraphPanel(panel_title, self.settings.graph_refresh_ms(), self.settings.graph_history_limit(), self)
        if config:
            panel.restore_settings_state(config)
        self.graph_panels.append(panel)
        dock = self._add_dock(panel_id, panel_title, panel, Qt.DockWidgetArea.RightDockWidgetArea)
        record = self.panel_registry.get(panel_id)
        if record is not None:
            record.panel_type = "graph"
            record.singleton = False
        self.panel_templates[panel_id] = template_id
        dock.visibilityChanged.connect(self._update_actions)
        if hasattr(self, "view_menu"):
            self.view_menu.addAction(dock.toggleViewAction())
        return panel_id

    def _restore_first_graph_panel_settings(self) -> None:
        states = self.settings.graph_panels_state()
        if not states:
            return
        self.graph_panels[0].restore_settings_state(states[0])

    def _next_panel_id(self, prefix: str) -> str:
        counter = self._panel_type_counters.get(prefix, 0)
        while True:
            counter += 1
            candidate = f"{prefix}_{counter}"
            if candidate not in self.docks and candidate not in self.detached_windows:
                self._panel_type_counters[prefix] = counter
                return candidate

    @staticmethod
    def _numeric_suffix(panel_id: str) -> int:
        try:
            return int(panel_id.rsplit("_", 1)[1])
        except (IndexError, ValueError):
            return 0

    def create_panel_from_template(self, template_id: str, tile_id: str | None = None) -> str | None:
        template = PANEL_TEMPLATES.get(template_id)
        if template is None:
            self._logger.warning("Unknown panel template ignored: %s", template_id)
            return None
        if template.panel_type == "builtin":
            panel_id = template.template_id
            dock = self.docks.get(panel_id)
            if dock is not None:
                dock.show()
                dock.raise_()
                return panel_id
            return None
        if template.singleton:
            existing = self._find_template_panel(template_id)
            if existing:
                if existing in self.docks:
                    self.docks[existing].show()
                    self.docks[existing].raise_()
                return existing

        prefix = template.template_id
        panel_id = self._next_panel_id(prefix)
        if template.panel_type == "graph":
            widget = self._create_graph_panel_widget(template.title, template.default_config)
        elif template.panel_type == "current_lap_graph":
            widget = self._create_current_lap_graph_widget(template.title, template.default_config)
        else:
            widget = self._create_template_widget(template, panel_id)
        if widget is None:
            return None
        self._register_workspace_panel(panel_id, template, widget, tile_id)
        return panel_id

    def _register_workspace_panel(self, panel_id: str, template: PanelTemplate, widget: QWidget, tile_id: str | None = None) -> None:
        widget.setWindowTitle(template.title)
        self.panel_widgets[panel_id] = widget
        self.panel_titles[panel_id] = template.title
        self.panel_templates[panel_id] = template.template_id
        self.panel_registry.register(
            panel_id,
            template.panel_type,
            template.title,
            None,
            widget,
            singleton=template.singleton,
            location="workspace",
        )
        self.dashboard_workspace.add_panel(panel_id, template.title, widget, tile_id=tile_id)

    def _create_graph_panel_widget(self, title: str, config: dict | None) -> GraphPanel:
        panel = GraphPanel(title, self.settings.graph_refresh_ms(), self.settings.graph_history_limit(), self)
        panel.setMinimumSize(120, 90)
        if config:
            panel.restore_settings_state(config)
        self.graph_panels.append(panel)
        return panel

    def _create_current_lap_graph_widget(self, title: str, config: dict | None) -> GraphPanel:
        panel = GraphPanel(title, self.settings.graph_refresh_ms(), self.settings.graph_history_limit(), self)
        panel.setMinimumSize(120, 90)
        if config:
            panel.restore_settings_state(config)
        if self.lap_tracker.active_lap is not None:
            panel.replace_samples(self.lap_tracker.active_lap.samples)
        self.current_lap_graph_panels.append(panel)
        return panel

    def _find_template_panel(self, template_id: str) -> str | None:
        for panel_id, saved_template_id in self.panel_templates.items():
            if saved_template_id == template_id and (panel_id in self.docks or panel_id in self.panel_widgets):
                return panel_id
        return None

    def _create_template_widget(self, template: PanelTemplate, panel_id: str) -> QWidget | None:
        if template.panel_type == "stacked_graph":
            return self._create_stacked_graph_widget(template)
        if template.panel_type == "live_values":
            return self._create_template_live_values_widget()
        if template.panel_type == "live_lap_timing":
            return self._create_live_lap_timing_widget()
        if template.panel_type == "sector_timing":
            return self._create_sector_timing_widget()
        if template.panel_type == "lap_history":
            return self._create_lap_history_template_widget()
        if template.panel_type == "session_history":
            return self._create_session_history_widget()
        if template.panel_type == "best_laps":
            return self._create_best_laps_widget()
        if template.panel_type == "lap_comparison":
            return self._create_lap_comparison_template_widget()
        if template.panel_type == "time_delta_graph":
            return self._create_time_delta_template_widget()
        if template.panel_type == "track_map":
            return self._create_track_map_widget()
        self._logger.warning("Unsupported panel type ignored: %s", template.panel_type)
        return None

    def _create_stacked_graph_widget(self, template: PanelTemplate) -> QWidget:
        wrapper = QWidget(self)
        layout = QVBoxLayout(wrapper)
        for graph_config in template.default_config.get("graphs", []):
            panel = GraphPanel(graph_config.get("title", template.title), self.settings.graph_refresh_ms(), self.settings.graph_history_limit(), wrapper)
            state = {
                "metrics": graph_config.get("metrics", []),
                "x_mode": template.default_config.get("x_mode", "follow_live"),
                "recent_window": template.default_config.get("recent_window", 30),
                "y_mode": "metric_default",
                "manual_y": graph_config.get("manual_y", [0.0, 100.0]),
                "settings_hidden": template.default_config.get("settings_hidden", True),
            }
            panel.restore_settings_state(state)
            self.graph_panels.append(panel)
            layout.addWidget(panel)
        return wrapper

    def _create_template_live_values_widget(self) -> QWidget:
        widget = QWidget(self)
        layout = QGridLayout(widget)
        labels: dict[str, QLabel] = {}
        rows = [
            ("speed", "Speed"),
            ("rpm", "RPM"),
            ("gear", "Gear"),
            ("throttle", "Throttle"),
            ("brake", "Brake"),
            ("clutch", "Clutch"),
            ("steering", "Steering"),
            ("lap", "Current lap"),
            ("sector", "Current sector"),
            ("lap_time", "Current lap time"),
            ("last_lap", "Last lap"),
            ("best_lap", "Best lap"),
            ("delta", "Delta"),
        ]
        for row, (key, title) in enumerate(rows):
            value = QLabel("--")
            layout.addWidget(QLabel(f"{title}:"), row, 0)
            layout.addWidget(value, row, 1)
            labels[key] = value
        self.live_value_panels.append(labels)
        return widget

    def _create_track_map_widget(self) -> TrackMapPanel:
        panel = TrackMapPanel("Track map", self)
        panel.setMinimumSize(160, 120)
        if self.lap_tracker.active_lap is not None:
            panel.replace_samples(self.lap_tracker.active_lap.samples)
        elif self.recording_samples:
            panel.replace_samples(self.recording_samples)
        self.track_map_panels.append(panel)
        return panel

    def _create_live_lap_timing_widget(self) -> QWidget:
        table = QTableWidget(1, 7, self)
        table.setHorizontalHeaderLabels(["Lap", "Lap time", "Sector 1", "Sector 2", "Sector 3", "Delta", "Status"])
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        for column, value in enumerate(["--", "--", "--", "--", "--", "--", "Current"]):
            table.setItem(0, column, QTableWidgetItem(value))
        self.live_lap_tables.append(table)
        return table

    def _create_sector_timing_widget(self) -> QWidget:
        table = QTableWidget(3, 4, self)
        table.setHorizontalHeaderLabels(["Sector", "Time", "Delta", "Status"])
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        for row in range(3):
            for column, value in enumerate([f"S{row + 1}", "--", "--", "Waiting"]):
                table.setItem(row, column, QTableWidgetItem(value))
        self.sector_timing_tables.append(table)
        return table

    def _create_lap_history_template_widget(self) -> QWidget:
        table = QTableWidget(len(self.saved_laps), 12, self)
        table.setHorizontalHeaderLabels(["Date", "Session", "Driver", "Track", "Car", "Lap", "Lap time", "S1", "S2", "S3", "Valid", "Graph"])
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        for row, lap in enumerate(self.saved_laps):
            sectors = [format_time_ms(sector.time_ms) for sector in lap.sectors[:3]]
            while len(sectors) < 3:
                sectors.append("--")
            values = [
                lap.started_at,
                lap.session_id,
                lap.driver_name or "--",
                display_track_name(lap.track),
                display_car_name(lap.car),
                str(lap.lap_number),
                format_time_ms(lap.lap_time_ms),
                *sectors,
                "Yes" if lap.valid else "No",
                graph_availability(lap),
            ]
            for column, value in enumerate(values):
                table.setItem(row, column, QTableWidgetItem(value))
        return table

    def _create_best_laps_widget(self) -> QWidget:
        laps = sorted([lap for lap in self.saved_laps if lap.complete and lap.valid and lap.lap_time_ms is not None], key=lambda lap: lap.lap_time_ms or 0)
        table = QTableWidget(len(laps), 8, self)
        table.setHorizontalHeaderLabels(["Rank", "Driver", "Lap time", "S1", "S2", "S3", "Car", "Delta"])
        fastest = laps[0].lap_time_ms if laps else None
        for row, lap in enumerate(laps):
            sectors = [format_time_ms(sector.time_ms) for sector in lap.sectors[:3]]
            while len(sectors) < 3:
                sectors.append("--")
            delta = "--" if fastest is None or lap.lap_time_ms is None else f"+{(lap.lap_time_ms - fastest) / 1000.0:.3f}s"
            values = [str(row + 1), lap.driver_name or "--", format_time_ms(lap.lap_time_ms), *sectors, display_car_name(lap.car), delta]
            for column, value in enumerate(values):
                table.setItem(row, column, QTableWidgetItem(value))
        return table

    def _create_session_history_widget(self) -> QWidget:
        widget = QWidget(self)
        layout = QVBoxLayout(widget)
        summary_table = QTableWidget(0, 6, self)
        summary_table.setHorizontalHeaderLabels(["Track", "Car", "Game", "Date/time", "Best lap", "Laps"])
        summary_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        summary_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        detail_table = QTableWidget(0, 8, self)
        detail_table.setHorizontalHeaderLabels(["Lap", "Lap time", "S1", "S2", "S3", "Delta", "Valid", "Notes"])
        detail_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        detail_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        summary_table.itemSelectionChanged.connect(lambda: self._populate_session_detail_table(summary_table, detail_table))
        layout.addWidget(summary_table)
        layout.addWidget(detail_table)
        self.session_history_tables.append((summary_table, detail_table))
        self._populate_session_history_table(summary_table)
        return widget

    def _create_lap_comparison_template_widget(self) -> QWidget:
        widget = QWidget(self)
        layout = QVBoxLayout(widget)
        layout.addWidget(QLabel("Select saved laps in Lap history, then use Compare selected laps."))
        layout.addWidget(self._create_comparison_widget())
        return widget

    def _create_time_delta_template_widget(self) -> QWidget:
        widget = QWidget(self)
        layout = QVBoxLayout(widget)
        layout.addWidget(QLabel("Time delta is available after choosing compatible saved laps."))
        if pg is None:
            layout.addWidget(QLabel("pyqtgraph is not available."))
        else:
            plot = pg.PlotWidget()
            plot.setLabel("left", "Delta", units="s")
            plot.setLabel("bottom", "Lap position")
            plot.addLine(y=0.0, pen=pg.mkPen("#888888", width=1))
            layout.addWidget(plot)
        return widget

    def start_selected_source(self) -> None:
        if self.active_source is not None:
            return

        source_id = self.source_combo.currentData()
        source_class = SOURCE_TYPES[source_id]
        kwargs = {}
        if source_class is F12018TelemetrySource:
            port = self.port_input.value()
            self.settings.set_f1_udp_port(port)
            kwargs["port"] = port

        self.error_label.setText("")
        self._reset_source_diagnostics()
        self.active_source = source_class(**kwargs)
        self.active_source.sample_received.connect(self.handle_telemetry_sample)
        self.active_source.status_changed.connect(self.handle_source_status)
        self.active_source.error_occurred.connect(self.handle_source_error)
        self.active_source.diagnostics_changed.connect(self.handle_diagnostics)
        self.source_label.setText(f"Source: {SOURCE_LABELS[source_id]}")
        self.lap_tracker.start_session(
            game=SOURCE_LABELS[source_id],
            track=None,
            car=None,
        )
        self.common_labels["running"].setText("Starting")
        self.common_labels["connection_state"].setText("Starting")
        self._logger.info("Starting telemetry source: %s", SOURCE_LABELS[source_id])
        self.active_source.start()
        self._status_timer.start()

        if self.active_source.state() == SourceState.ERROR and not self.active_source.is_running():
            self._detach_source(self.active_source)
            self.active_source = None
        self._update_controls()
        self._update_actions()

    def stop_active_source(self) -> None:
        if self.active_source is None:
            self._update_controls()
            return
        source = self.active_source
        self._logger.info("Stopping telemetry source: %s", source.display_name)
        source.stop()
        self.lap_tracker.stop_session()
        self._populate_session_history_panels()
        self._detach_source(source)
        self.active_source = None
        self._status_timer.stop()
        self.status_label.setText("Status: Stopped")
        self.common_labels["running"].setText("No")
        self.common_labels["connection_state"].setText("Stopped")
        self._update_controls()
        self._update_actions()

    def handle_telemetry_sample(self, sample: TelemetrySample) -> None:
        self._last_sample_wall_time = time.time()
        self._sample_count_window += 1
        self.telemetry_labels["Speed"].setText(f"{sample.speed_kmh:.0f} km/h")
        self.telemetry_labels["RPM"].setText(f"{sample.rpm} rpm")
        self.telemetry_labels["Gear"].setText(format_gear(sample.gear))
        self.telemetry_labels["Throttle"].setText(f"{sample.throttle_percent:.0f}%")
        self.telemetry_labels["Brake"].setText(f"{sample.brake_percent:.0f}%")
        self.common_labels["latest_update"].setText(time.strftime("%H:%M:%S", time.localtime(sample.timestamp or time.time())))
        self.common_labels["car_name"].setText(display_car_name(sample.car_name))
        self.common_labels["track_name"].setText(display_track_name(sample.track_name))
        self.common_labels["session_state"].setText(sample.session_state or "--")
        self._update_active_reference(sample)
        for panel in self.graph_panels:
            panel.add_sample(sample)
        for panel in self.track_map_panels:
            if panel.live_updates:
                panel.add_sample(sample)
        self._update_live_lap_template_panels(sample)
        self._update_sector_template_panels(sample)
        self.lap_tracker.process_sample(sample)
        self._update_live_value_template_panels(sample)
        if self.is_recording:
            self.recording_samples.append(sample)
            self.recording_count_label.setText(f"Samples: {len(self.recording_samples)}")

    def _update_live_value_template_panels(self, sample: TelemetrySample) -> None:
        for labels in self.live_value_panels:
            labels["speed"].setText(f"{sample.speed_kmh:.0f} km/h")
            labels["rpm"].setText(f"{sample.rpm} rpm")
            labels["gear"].setText(format_gear(sample.gear))
            labels["throttle"].setText(f"{max(0.0, sample.throttle_percent):.0f}%")
            labels["brake"].setText(f"{max(0.0, sample.brake_percent):.0f}%")
            labels["clutch"].setText("--" if sample.clutch_percent is None else f"{max(0.0, sample.clutch_percent):.0f}%")
            labels["steering"].setText("--" if sample.steering is None else f"{sample.steering:.1f}")
            labels["lap"].setText("--" if sample.lap_number is None else str(sample.lap_number))
            labels["sector"].setText("--" if sample.current_sector_index is None else f"S{sample.current_sector_index + 1}")
            labels["lap_time"].setText(format_time_ms(sample.current_lap_time_ms))
            labels["last_lap"].setText(format_time_ms(sample.last_lap_time_ms))
            labels["best_lap"].setText(format_time_ms(sample.best_lap_time_ms))
            labels["delta"].setText(format_delta_ms(live_lap_delta_ms(self.lap_tracker.active_lap, self.saved_laps)))

    def _update_live_lap_template_panels(self, sample: TelemetrySample) -> None:
        for table in self.live_lap_tables:
            row = table.rowCount() - 1
            values = [
                "--" if sample.lap_number is None else str(sample.lap_number),
                format_time_ms(sample.current_lap_time_ms),
                "--",
                "--",
                "--",
                format_delta_ms(live_lap_delta_ms(self.lap_tracker.active_lap, self.saved_laps)),
                "Invalid" if sample.invalid_lap else ("Pit" if sample.in_pit else "Current"),
            ]
            for column, value in enumerate(values):
                item = table.item(row, column)
                if item is None:
                    table.setItem(row, column, QTableWidgetItem(value))
                elif item.text() != value:
                    item.setText(value)

    def _update_sector_template_panels(self, sample: TelemetrySample) -> None:
        current = sample.current_sector_index
        for table in self.sector_timing_tables:
            for row in range(table.rowCount()):
                status = "Current" if current == row else self.lap_tracker.waiting_reason
                time_value = "Running" if current == row else "--"
                for column, value in enumerate([f"S{row + 1}", time_value, format_delta_ms(live_lap_delta_ms(self.lap_tracker.active_lap, self.saved_laps)), status]):
                    item = table.item(row, column)
                    if item is None:
                        table.setItem(row, column, QTableWidgetItem(value))
                    elif item.text() != value:
                        item.setText(value)

    def handle_source_status(self, status: str) -> None:
        self.status_label.setText(f"Status: {status}")
        self.common_labels["connection_state"].setText(status)
        self.common_labels["running"].setText("Yes" if self.active_source and self.active_source.is_running() else "No")
        self._logger.info("Source status: %s", status)

    def handle_source_error(self, message: str) -> None:
        self.error_label.setText(message)
        self.common_labels["latest_error"].setText(message or "--")
        self._logger.error("Telemetry source error: %s", message)

    def handle_diagnostics(self, diagnostics: dict) -> None:
        for key, value in diagnostics.items():
            text = str(value)
            if key in self.f1_labels:
                self.f1_labels[key].setText(f"{value} bytes" if key == "latest_packet_size" and isinstance(value, int) else text)
            if key in self.shared_memory_labels:
                self.shared_memory_labels[key].setText(text or "--")
            if key == "car_name":
                self.common_labels["car_name"].setText(display_car_name(text))
            if key == "track_name":
                self.common_labels["track_name"].setText(display_track_name(text))
            if key == "game_state":
                self.common_labels["session_state"].setText(text or "--")
            if key == "updates_per_second":
                self.common_labels["updates_per_second"].setText(text or "--")
            if key == "last_error":
                self.common_labels["latest_error"].setText(text or "--")

    def handle_timing_state(self, state: str, reason: str) -> None:
        text = reason or state
        if hasattr(self, "lap_labels"):
            self.lap_labels["timing_scope"].setText(text)
        for table in self.sector_timing_tables:
            for row in range(table.rowCount()):
                item = table.item(row, 3)
                if item is None:
                    table.setItem(row, 3, QTableWidgetItem(text))
                elif item.text() in {"Waiting", "--"} or state in {"WAITING_FOR_LAP", "WAITING_FOR_SESSION", "IN_PITS"}:
                    item.setText(text)

    def import_telemetry(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(
            self,
            "Import telemetry",
            self.settings.import_directory(),
            "Telemetry files (*.csv *.json);;CSV files (*.csv);;JSON files (*.json)",
        )
        if not path:
            return
        self.settings.set_import_directory(str(Path(path).parent))
        metadata = self._metadata_dialog(Path(path).stem)
        if metadata is None:
            return
        self.import_action.setEnabled(False)
        self.error_label.setText(f"Importing {Path(path).name}...")
        self._import_thread = QThread(self)
        self._import_worker = ImportWorker(path, metadata)
        self._import_worker.moveToThread(self._import_thread)
        self._import_thread.started.connect(self._import_worker.run)
        self._import_worker.imported.connect(self._finish_import)
        self._import_worker.failed.connect(self._fail_import)
        self._import_worker.imported.connect(self._import_thread.quit)
        self._import_worker.failed.connect(self._import_thread.quit)
        self._import_thread.finished.connect(self._import_worker.deleteLater)
        self._import_thread.finished.connect(self._import_thread.deleteLater)
        self._import_thread.start()

    def _finish_import(self, session: TelemetrySession) -> None:
        self.sessions.append(session)
        self.session_store.save(self.sessions)
        self._populate_sessions_table()
        self.import_action.setEnabled(True)
        self.error_label.setText("")
        QMessageBox.information(self, "Import complete", f"Imported {session.sample_count} samples.")

    def _fail_import(self, message: str) -> None:
        self.import_action.setEnabled(True)
        self._show_error("Import failed", message)

    def _metadata_dialog(self, default_name: str, session: TelemetrySession | None = None) -> dict | None:
        dialog = QDialog(self)
        dialog.setWindowTitle("Telemetry metadata")
        layout = QFormLayout(dialog)
        fields: dict[str, QLineEdit] = {}
        defaults = {
            "session_name": session.session_name if session else default_name,
            "driver_name": session.driver_name if session else "",
            "game": session.game if session else "",
            "track": session.track if session else "",
            "car": session.car if session else "",
            "notes": session.notes if session else "",
        }
        for key, value in defaults.items():
            edit = QLineEdit(value)
            layout.addRow(key.replace("_", " ").title(), edit)
            fields[key] = edit
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addRow(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        return {key: edit.text().strip() for key, edit in fields.items()}

    def _populate_sessions_table(self) -> None:
        self.sessions_table.setRowCount(len(self.sessions))
        for row, session in enumerate(self.sessions):
            compare_item = QTableWidgetItem()
            compare_item.setCheckState(Qt.CheckState.Unchecked)
            self.sessions_table.setItem(row, 0, compare_item)
            values = [
                session.session_name,
                session.driver_name,
                session.game,
                display_track_name(session.track),
                display_car_name(session.car),
                session.lap_label,
                f"{session.duration:.1f}s",
                str(session.sample_count),
            ]
            for column, value in enumerate(values, start=1):
                self.sessions_table.setItem(row, column, QTableWidgetItem(value))
        self.sessions_table.resizeColumnsToContents()
        self._update_actions()

    def _populate_session_history_panels(self) -> None:
        for summary_table, detail_table in self.session_history_tables:
            self._populate_session_history_table(summary_table)
            self._populate_session_detail_table(summary_table, detail_table)

    def _populate_session_history_table(self, table: QTableWidget) -> None:
        summaries = self.lap_tracker.storage.load_session_summaries()
        table.setRowCount(len(summaries))
        for row, summary in enumerate(summaries):
            values = [
                display_track_name(summary.track),
                display_car_name(summary.car),
                summary.game or "--",
                summary.started_at or "--",
                format_time_ms(summary.best_lap_time_ms),
                str(summary.lap_count),
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole, summary.session_id)
                table.setItem(row, column, item)
        table.resizeColumnsToContents()

    def _populate_session_detail_table(self, summary_table: QTableWidget, detail_table: QTableWidget) -> None:
        selected_rows = sorted({index.row() for index in summary_table.selectedIndexes()})
        session_id = None
        if selected_rows:
            item = summary_table.item(selected_rows[0], 0)
            session_id = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        session_laps = [lap for lap in self.saved_laps if lap.session_id == session_id] if session_id else []
        session_laps.sort(key=lambda lap: lap.lap_number)
        detail_table.setRowCount(len(session_laps))
        for row, lap in enumerate(session_laps):
            sectors = {sector.sector_number: sector for sector in lap.sectors}
            delta = format_delta_ms(completed_lap_delta_ms(lap, session_laps), best_label=True)
            values = [
                str(lap.lap_number),
                format_time_ms(lap.lap_time_ms),
                format_time_ms(sectors.get(1).time_ms if sectors.get(1) else None),
                format_time_ms(sectors.get(2).time_ms if sectors.get(2) else None),
                format_time_ms(sectors.get(3).time_ms if sectors.get(3) else None),
                delta,
                "Valid" if lap.valid else "Invalid",
                lap.notes or "--",
            ]
            for column, value in enumerate(values):
                detail_table.setItem(row, column, QTableWidgetItem(value))
        detail_table.resizeColumnsToContents()

    def _populate_laps_table(self) -> None:
        if not hasattr(self, "laps_table"):
            return
        self.laps_table.setSortingEnabled(False)
        self.laps_table.setRowCount(len(self.saved_laps))
        for row, lap in enumerate(self.saved_laps):
            sectors = {sector.sector_number: sector for sector in lap.sectors}
            values = [
                str(lap.lap_number),
                format_time_ms(lap.lap_time_ms),
                format_time_ms(sectors.get(1).time_ms if sectors.get(1) else None),
                format_time_ms(sectors.get(2).time_ms if sectors.get(2) else None),
                format_time_ms(sectors.get(3).time_ms if sectors.get(3) else None),
                format_delta_ms(completed_lap_delta_ms(lap, self.saved_laps), best_label=True),
                "Valid" if lap.valid else "Invalid",
                display_car_name(lap.car),
                display_track_name(lap.track),
                graph_availability(lap),
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole, lap.id)
                if not lap.valid:
                    item.setForeground(Qt.GlobalColor.gray)
                self.laps_table.setItem(row, column, item)
        self.laps_table.setSortingEnabled(True)
        self.laps_table.resizeColumnsToContents()

    def handle_lap_updated(self, lap: LapResult) -> None:
        if not hasattr(self, "lap_labels"):
            return
        self.lap_labels["current_lap"].setText(str(lap.lap_number))
        current_sample = lap.samples[-1] if lap.samples else None
        if current_sample is not None:
            sector = current_sample.current_sector_index
            self.lap_labels["current_sector"].setText("--" if sector is None else str(int(sector) + 1))
            self.lap_labels["current_lap_time"].setText(format_time_ms(current_sample.current_lap_time_ms))
            self.lap_labels["last_lap"].setText(format_time_ms(current_sample.last_lap_time_ms))
            self.lap_labels["best_lap"].setText(format_time_ms(current_sample.best_lap_time_ms))
        self._update_live_lap_tables_from_lap(lap, complete=False)
        self._update_sector_tables_from_lap(lap)
        self._update_current_lap_graph_panels(lap)

    def handle_lap_completed(self, lap: LapResult) -> None:
        if any(existing.id == lap.id for existing in self.saved_laps):
            self._logger.warning("Duplicate completed lap ignored by UI history: lap_id=%s", lap.id)
            return
        self.saved_laps.insert(0, lap)
        self._logger.info("[Timing] Lap added to history: lap=%s lap_id=%s", lap.lap_number, lap.id)
        self.lap_labels["last_lap"].setText(format_time_ms(lap.lap_time_ms))
        valid_times = [item.lap_time_ms for item in self.saved_laps if item.valid and item.lap_time_ms is not None]
        self.lap_labels["best_lap"].setText(format_time_ms(min(valid_times) if valid_times else None))
        self._update_live_lap_tables_from_lap(lap, complete=True)
        self._refresh_completed_live_lap_rows()
        self._populate_laps_table()
        self._populate_session_history_panels()
        self._show_sector_feedback(lap)
        self._update_current_lap_graph_panels(self.lap_tracker.active_lap)

    def _update_active_reference(self, sample: TelemetrySample) -> None:
        if not self.reference_overlay_enabled or not sample.track_name or not sample.car_name or not sample.source_name:
            return
        if (
            self.active_reference_lap is not None
            and self.active_reference_lap.game == sample.source_name
            and self.active_reference_lap.track_id == sample.track_name
            and self.active_reference_lap.car_id == sample.car_name
        ):
            return
        reference = self.lap_tracker.storage.best_reference_lap(sample.source_name, sample.track_name, sample.car_name)
        self.active_reference_lap = reference
        if reference is not None:
            self.status_label.setText(
                f"Reference: {reference.player_name or reference.source} {format_time_ms(reference.lap_time_ms)}"
            )
            self._show_reference_summary_panel(reference)

    def _show_reference_summary_panel(self, reference: ReferenceLap) -> None:
        key = reference.id
        if self._reference_panel_key == key:
            return
        self._reference_panel_key = key
        widget = QWidget(self)
        layout = QVBoxLayout(widget)
        title = QLabel(f"Reference: {reference.player_name or reference.source}")
        detail = QLabel(
            " | ".join(
                [
                    reference.game or "--",
                    reference.track_display_name or display_track_name(reference.track_id),
                    reference.car_display_name or display_car_name(reference.car_id),
                    format_time_ms(reference.lap_time_ms),
                    "Telemetry available" if reference.telemetry_points else "Lap time only",
                ]
            )
        )
        title.setWordWrap(True)
        detail.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(detail)
        panel_id = self._next_panel_id("reference_summary")
        template = PanelTemplate(panel_id, "Active reference", "reference_summary", "Best matching local reference lap.")
        self._register_workspace_panel(panel_id, template, widget)

    def _show_sector_feedback(self, lap: LapResult) -> None:
        reference = self.active_reference_lap
        if reference is None:
            compatible = [
                item
                for item in self.saved_laps
                if item.id != lap.id and item.valid and item.complete and item.track == lap.track and item.car == lap.car
            ]
            reference = min(compatible, key=lambda item: item.lap_time_ms or 10**12, default=None)
        messages = sector_feedback(lap, reference)
        if messages:
            self.status_label.setText(messages[0])

    def _update_current_lap_graph_panels(self, lap: LapResult | None) -> None:
        samples = lap.samples if lap is not None else []
        for panel in self.current_lap_graph_panels:
            panel.replace_samples(samples)

    def _update_live_lap_tables_from_lap(self, lap: LapResult, complete: bool) -> None:
        sectors = {sector.sector_number: sector for sector in lap.sectors}
        current_sample = lap.samples[-1] if lap.samples else None
        state = LIVE_LAP_STATE_COMPLETED if complete else LIVE_LAP_STATE_CURRENT
        row_values = [
            str(lap.lap_number),
            format_time_ms(lap.lap_time_ms if complete else (current_sample.current_lap_time_ms if current_sample else None)),
            format_time_ms(sectors.get(1).time_ms if sectors.get(1) else None),
            format_time_ms(sectors.get(2).time_ms if sectors.get(2) else None),
            format_time_ms(sectors.get(3).time_ms if sectors.get(3) else None),
            format_delta_ms(
                completed_lap_delta_ms(lap, self.saved_laps) if complete else live_lap_delta_ms(lap, self.saved_laps),
                best_label=complete,
            ),
            "Invalid" if not lap.valid else ("Complete" if complete else "Current"),
        ]
        for table in self.live_lap_tables:
            sorting_was_enabled = table.isSortingEnabled()
            table.setSortingEnabled(False)
            try:
                row = self._live_lap_row_for_update(table, lap.lap_number, state)
                self._write_live_lap_row(table, row, row_values, lap.lap_number, state, sectors)
                if complete:
                    self._ensure_live_lap_current_placeholder(table)
            finally:
                table.setSortingEnabled(sorting_was_enabled)

    def _refresh_completed_live_lap_rows(self) -> None:
        laps_by_number = {str(lap.lap_number): lap for lap in self.saved_laps}
        for table in self.live_lap_tables:
            sorting_was_enabled = table.isSortingEnabled()
            table.setSortingEnabled(False)
            try:
                rows = [
                    row
                    for row in range(table.rowCount())
                    if self._live_lap_row_state(table, row) == LIVE_LAP_STATE_COMPLETED
                ]
                for row in rows:
                    lap_item = table.item(row, 0)
                    lap = laps_by_number.get(lap_item.text() if lap_item is not None else "")
                    if lap is None:
                        continue
                    sectors = {sector.sector_number: sector for sector in lap.sectors}
                    values = [
                        str(lap.lap_number),
                        format_time_ms(lap.lap_time_ms),
                        format_time_ms(sectors.get(1).time_ms if sectors.get(1) else None),
                        format_time_ms(sectors.get(2).time_ms if sectors.get(2) else None),
                        format_time_ms(sectors.get(3).time_ms if sectors.get(3) else None),
                        format_delta_ms(completed_lap_delta_ms(lap, self.saved_laps), best_label=True),
                        "Invalid" if not lap.valid else "Complete",
                    ]
                    self._write_live_lap_row(table, row, values, lap.lap_number, LIVE_LAP_STATE_COMPLETED, sectors)
            finally:
                table.setSortingEnabled(sorting_was_enabled)

    def _live_lap_row_state(self, table: QTableWidget, row: int) -> str | None:
        item = table.item(row, 0)
        if item is None:
            return None
        state = item.data(LIVE_LAP_STATE_ROLE)
        return str(state) if state is not None else None

    def _live_lap_row_number(self, table: QTableWidget, row: int) -> int | None:
        item = table.item(row, 0)
        if item is None:
            return None
        value = item.data(LIVE_LAP_NUMBER_ROLE)
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _is_empty_live_lap_row(self, table: QTableWidget, row: int) -> bool:
        if self._live_lap_row_number(table, row) is not None or self._live_lap_row_state(table, row) is not None:
            return False
        lap_item = table.item(row, 0)
        return lap_item is None or lap_item.text() in {"", "--", "\u2014"}

    def _find_live_lap_row(self, table: QTableWidget, lap_number: int, state: str) -> int | None:
        for row in range(table.rowCount()):
            if self._live_lap_row_number(table, row) == lap_number and self._live_lap_row_state(table, row) == state:
                return row
        if state == LIVE_LAP_STATE_COMPLETED:
            for row in range(table.rowCount()):
                if self._live_lap_row_number(table, row) == lap_number and self._live_lap_row_state(table, row) == LIVE_LAP_STATE_CURRENT:
                    return row
        return None

    def _live_lap_row_for_update(self, table: QTableWidget, lap_number: int, state: str) -> int:
        existing = self._find_live_lap_row(table, lap_number, state)
        if existing is not None:
            return existing
        for row in range(table.rowCount()):
            if self._is_empty_live_lap_row(table, row):
                return row
        row = table.rowCount()
        table.insertRow(row)
        return row

    def _write_live_lap_row(
        self,
        table: QTableWidget,
        row: int,
        values: list[str],
        lap_number: int,
        state: str,
        sectors: dict[int, object],
    ) -> None:
        for column, value in enumerate(values):
            item = table.item(row, column)
            if item is None:
                item = QTableWidgetItem(value)
                table.setItem(row, column, item)
            elif item.text() != value:
                item.setText(value)
            item.setData(LIVE_LAP_NUMBER_ROLE, lap_number)
            item.setData(LIVE_LAP_STATE_ROLE, state)
            if column in (2, 3, 4):
                sector = sectors.get(column - 1)
                self._style_timing_item(item, sector.comparison_status if sector else "UNAVAILABLE")

    def _ensure_live_lap_current_placeholder(self, table: QTableWidget) -> None:
        for row in range(table.rowCount()):
            if self._live_lap_row_state(table, row) == LIVE_LAP_STATE_CURRENT:
                return
            if self._is_empty_live_lap_row(table, row):
                return
        row = table.rowCount()
        table.insertRow(row)
        for column, value in enumerate(["--", "--", "--", "--", "--", "--", "Current"]):
            table.setItem(row, column, QTableWidgetItem(value))

    def _update_sector_tables_from_lap(self, lap: LapResult) -> None:
        sectors = {sector.sector_number: sector for sector in lap.sectors}
        current_sample = lap.samples[-1] if lap.samples else None
        current = current_sample.current_sector_index if current_sample else None
        completed_total = sum(sector.time_ms or 0 for sector in lap.sectors)
        running_ms = None
        if current_sample and current_sample.current_lap_time_ms is not None and current is not None:
            running_ms = max(0, current_sample.current_lap_time_ms - completed_total)
        for table in self.sector_timing_tables:
            for row in range(table.rowCount()):
                sector = sectors.get(row + 1)
                if sector and sector.time_ms is not None:
                    time_value = format_time_ms(sector.time_ms)
                    status = sector.comparison_status or sector.timing_source
                elif current == row and running_ms is not None:
                    time_value = f"Running {format_time_ms(running_ms)}"
                    status = "Current"
                else:
                    time_value = format_time_ms(None)
                    status = "Waiting"
                delta = format_delta_ms(live_lap_delta_ms(lap, self.saved_laps))
                for column, value in enumerate([f"S{row + 1}", time_value, delta, status]):
                    item = table.item(row, column)
                    if item is None:
                        item = QTableWidgetItem(value)
                        table.setItem(row, column, item)
                    elif item.text() != value:
                        item.setText(value)
                    if column in (1, 3):
                        self._style_timing_item(item, status)

    def _style_timing_item(self, item: QTableWidgetItem, status: str | None) -> None:
        status_key = (status or "NEUTRAL").upper()
        colors = {
            "PURPLE": ("#6f2dbd", "#ffffff", "Fastest sector in current session"),
            "GREEN": ("#1b7f3a", "#ffffff", "Faster than compatible personal reference"),
            "YELLOW": ("#9f7a10", "#101010", "Slower than compatible personal reference"),
            "NEUTRAL": ("#2f3542", "#f1f2f6", "No compatible reference"),
            "INVALID": ("#4b4b4b", "#d0d0d0", "Invalid sector"),
            "UNAVAILABLE": ("#20242b", "#b8bec8", "Sector timing unavailable"),
        }
        background, foreground, tooltip = colors.get(status_key, colors["NEUTRAL"])
        item.setBackground(QBrush(QColor(background)))
        item.setForeground(QBrush(QColor(foreground)))
        item.setToolTip(tooltip)

    def selected_lap_rows(self) -> list[int]:
        if not hasattr(self, "laps_table"):
            return []
        return sorted({index.row() for index in self.laps_table.selectedIndexes()})

    def selected_laps(self) -> list[LapResult]:
        rows = self.selected_lap_rows()
        return [self.saved_laps[row] for row in rows if 0 <= row < len(self.saved_laps)]

    def open_selected_lap_graph(self) -> None:
        laps = self.selected_laps()
        if not laps:
            self._show_error("Lap graph unavailable", "Select a lap in Lap history first.")
            return
        panel = self.open_lap_graph(laps[0])
        if panel is None:
            self._show_error("Lap graph unavailable", "This lap has summary data only; no in-memory telemetry graph is available.")

    def open_lap_graph(self, lap: LapResult) -> GraphPanel | None:
        if not lap.samples and lap.telemetry_series is None:
            return None
        title = f"Lap {lap.lap_number} graph"
        panel = GraphPanel(title, self.settings.graph_refresh_ms(), self.settings.graph_history_limit(), self)
        panel.restore_settings_state(
            {
                "metrics": ["speed_kmh", "throttle_percent", "brake_percent"],
                "x_mode": "full_session",
                "y_mode": "metric_default",
                "settings_hidden": True,
            }
        )
        panel.replace_samples(lap.samples)
        self.saved_lap_graph_panels.append(panel)
        panel_id = self._next_panel_id("saved_lap_graph")
        template = PanelTemplate(panel_id, title, "saved_lap_graph", "Frozen completed lap graph.")
        self._register_workspace_panel(panel_id, template, panel)
        return panel

    def open_selected_lap_map(self) -> None:
        laps = self.selected_laps()
        if not laps:
            self._show_error("Lap map unavailable", "Select a lap in Lap history first.")
            return
        panel = self.open_lap_map(laps[0])
        if panel is None:
            self._show_error("Lap map unavailable", "This lap does not contain saved car position coordinates.")

    def open_lap_map(self, lap: LapResult) -> TrackMapPanel | None:
        if not any(sample.world_position_x is not None and sample.world_position_z is not None for sample in lap.samples):
            return None
        title = f"Lap {lap.lap_number} track map"
        panel = TrackMapPanel(title, self, live_updates=False)
        panel.replace_samples(lap.samples)
        self.track_map_panels.append(panel)
        panel_id = self._next_panel_id("saved_lap_map")
        template = PanelTemplate(panel_id, title, "track_map", "Frozen completed lap trajectory.")
        self._register_workspace_panel(panel_id, template, panel)
        return panel

    def open_lap_pedal_overlay(self, lap: LapResult, comparison: LapResult | ReferenceLap | None = None) -> QWidget | None:
        if not lap.samples:
            return None
        comparison = comparison or self.active_reference_lap
        widget = QWidget(self)
        layout = QVBoxLayout(widget)
        sectors = {sector.sector_number: sector for sector in lap.sectors}
        summary = QLabel(
            " | ".join(
                [
                    f"Lap {lap.lap_number}",
                    f"Time {format_time_ms(lap.lap_time_ms)}",
                    f"S1 {format_time_ms(sectors.get(1).time_ms if sectors.get(1) else None)}",
                    f"S2 {format_time_ms(sectors.get(2).time_ms if sectors.get(2) else None)}",
                    f"S3 {format_time_ms(sectors.get(3).time_ms if sectors.get(3) else None)}",
                    f"Delta {format_delta_ms(completed_lap_delta_ms(lap, self.saved_laps), best_label=True)}",
                ]
            )
        )
        summary.setWordWrap(True)
        layout.addWidget(summary)
        if pg is None:
            layout.addWidget(QLabel("pyqtgraph is not available."))
        else:
            plot = pg.PlotWidget()
            plot.setLabel("left", "Pedals", units="%")
            plot.setLabel("bottom", "Lap distance / progress")
            plot.addLegend()
            overlay = []
            if comparison is not None:
                from telemetry.telemetry_overlay import build_lap_overlay

                overlay = build_lap_overlay(lap, comparison, metrics=["throttle_percent", "brake_percent"])
            if overlay:
                for series in overlay:
                    own_pen = pg.mkPen("#1f77b4" if series.metric == "throttle_percent" else "#d62728", width=2)
                    ref_pen = pg.mkPen("#8ecaff" if series.metric == "throttle_percent" else "#ff9a9a", width=1, style=Qt.PenStyle.DashLine)
                    label = "Throttle" if series.metric == "throttle_percent" else "Brake"
                    plot.plot(series.axis, series.main, pen=own_pen, name=f"Own {label}")
                    plot.plot(series.axis, series.comparison, pen=ref_pen, name=f"Ref {label}")
            else:
                from telemetry.telemetry_points import lap_to_points, point_axis

                points = lap_to_points(lap)
                axes = [point_axis(point) for point in points]
                if any(axis is not None for axis in axes):
                    base = next(axis for axis in axes if axis is not None)
                    x = [0.0 if axis is None else float(axis) - float(base) for axis in axes]
                    plot.plot(x, [point.throttle_percent or 0.0 for point in points], pen=pg.mkPen("#1f77b4", width=2), name="Throttle")
                    plot.plot(x, [point.brake_percent or 0.0 for point in points], pen=pg.mkPen("#d62728", width=2), name="Brake")
            layout.addWidget(plot)
        panel_id = self._next_panel_id("lap_pedal_overlay")
        title = f"Lap {lap.lap_number} pedals"
        template = PanelTemplate(panel_id, title, "lap_pedal_overlay", "Frozen completed lap pedal overlay.")
        self._register_workspace_panel(panel_id, template, widget)
        return widget

    def compare_selected_laps(self) -> None:
        laps = self.selected_laps()
        if len(laps) < 2:
            self._show_error("Lap comparison unavailable", "Select at least two saved laps.")
            return
        if self.comparison_plot is None:
            return
        try:
            assert_laps_comparable(laps)
        except ValueError as error:
            self._show_error("Lap comparison unavailable", str(error))
            return
        grid = common_position_grid(laps)
        if grid.size == 0:
            self._show_error("Lap comparison unavailable", "Selected laps do not contain lap distance or normalized position.")
            return
        metric = self.comparison_metric_combo.currentData() or "speed_kmh"
        self.comparison_plot.clear()
        self.comparison_plot.addLegend()
        for index, lap in enumerate(laps):
            y = aligned_metric(lap, metric, grid)
            if y.size:
                self.comparison_plot.plot(grid, y, pen=pg.mkPen(PENS[index % len(PENS)], width=2), name=f"Lap {lap.lap_number}")
        for position in sector_marker_positions(laps[0]):
            self.comparison_plot.addLine(x=position, pen=pg.mkPen("#888888", style=Qt.PenStyle.DashLine))
        delta_x, delta_y = time_delta(laps[0], laps[1])
        if delta_x.size and delta_y.size:
            self.comparison_plot.plot(delta_x, delta_y, pen=pg.mkPen("#ffffff", width=1), name="Time delta: comparison - reference")
        self.open_lap_pedal_overlay(laps[0], laps[1])

    def delete_selected_lap(self) -> None:
        laps = self.selected_laps()
        if not laps:
            return
        reply = QMessageBox.question(self, "Delete lap", "Delete selected saved lap?")
        if reply != QMessageBox.StandardButton.Yes:
            return
        for lap in laps:
            self.lap_tracker.storage.delete_lap(lap.id)
            self.saved_laps = [item for item in self.saved_laps if item.id != lap.id]
        self._populate_laps_table()

    def export_selected_lap(self) -> None:
        laps = self.selected_laps()
        if not laps:
            return
        lap = laps[0]
        path, _filter = QFileDialog.getSaveFileName(self, "Export lap", self.settings.export_directory(), "Telemetry lap (*.json)")
        if not path:
            return
        data = {
            "lap_number": lap.lap_number,
            "lap_time_ms": lap.lap_time_ms,
            "valid": lap.valid,
            "complete": lap.complete,
            "track": lap.track,
            "car": lap.car,
            "sectors": [asdict(sector) for sector in lap.sectors],
            "samples": [{field: getattr(sample, field) for field in TelemetrySample.__dataclass_fields__} for sample in lap.samples],
        }
        Path(path).write_text(json.dumps(data, indent=2), encoding="utf-8")

    def selected_session_rows(self) -> list[int]:
        return sorted({index.row() for index in self.sessions_table.selectedIndexes()})

    def comparison_sessions(self) -> list[TelemetrySession]:
        sessions: list[TelemetrySession] = []
        for row, session in enumerate(self.sessions):
            item = self.sessions_table.item(row, 0)
            if item and item.checkState() == Qt.CheckState.Checked:
                sessions.append(session)
        if self.is_recording and self.recording_samples:
            sessions.append(self._recorded_session_preview())
        return sessions

    def refresh_comparison_graph(self) -> None:
        if self.comparison_plot is None:
            return
        sessions = self.comparison_sessions()
        if len(sessions) < 2:
            self._show_error("Comparison unavailable", "Select at least two sessions for comparison.")
            return
        metric = self.comparison_metric_combo.currentData()
        axis = self.comparison_axis_combo.currentData()
        if axis == "auto":
            axis = preferred_axis(sessions)
        try:
            series_list = build_comparison_series(
                sessions,
                metric,
                axis,
                allow_track_mismatch=self.allow_track_mismatch_checkbox.isChecked(),
            )
        except ValueError as error:
            self._show_error("Comparison unavailable", str(error))
            return
        self.comparison_plot.clear()
        self.comparison_plot.addLegend()
        for index, series in enumerate(series_list):
            self.comparison_plot.plot(series.x, series.y, pen=pg.mkPen(PENS[index % len(PENS)], width=2), name=series.name)
        if len(sessions) >= 2 and metric == "speed_kmh":
            delta = speed_delta(sessions[0], sessions[1], axis)
            self.comparison_plot.plot(delta.x, delta.y, pen=pg.mkPen("#ffffff", width=1, style=Qt.PenStyle.DashLine), name=delta.name)

    def toggle_recording(self) -> None:
        if self.is_recording:
            self.stop_recording()
        else:
            self.start_recording()

    def start_recording(self) -> None:
        self.recording_samples.clear()
        self.is_recording = True
        self.record_button.setText("Stop raw recording")
        self.recording_label.setText("Raw recording: on")
        self.recording_count_label.setText("Samples: 0")
        self._update_actions()

    def stop_recording(self) -> None:
        self.is_recording = False
        self.record_button.setText("Start raw recording")
        self.recording_label.setText("Raw recording: off")
        self._update_actions()

    def save_recorded_session(self) -> None:
        if not self.recording_samples:
            self._show_error("No recording", "There are no recorded samples to save.")
            return
        path, _filter = QFileDialog.getSaveFileName(self, "Save recorded session", self.settings.export_directory(), "Telemetry session (*.json)")
        if not path:
            return
        session = self._recorded_session_preview()
        self.session_store.save_session_file(session, path)
        self.sessions.append(session)
        self.session_store.save(self.sessions)
        self._populate_sessions_table()

    def _recorded_session_preview(self) -> TelemetrySession:
        source_name = self.source_combo.currentText()
        return TelemetrySession(
            source_type="live_recording",
            session_name=f"Recorded {time.strftime('%Y-%m-%d %H:%M:%S')}",
            game=source_name,
            samples=list(self.recording_samples),
        )

    def remove_selected_session(self) -> None:
        rows = self.selected_session_rows()
        if not rows:
            return
        if self.settings.confirm_remove_sessions():
            reply = QMessageBox.question(self, "Remove session", "Remove selected session from the application?")
            if reply != QMessageBox.StandardButton.Yes:
                return
        for row in reversed(rows):
            del self.sessions[row]
        self.session_store.save(self.sessions)
        self._populate_sessions_table()

    def rename_selected_session(self) -> None:
        rows = self.selected_session_rows()
        if not rows:
            return
        session = self.sessions[rows[0]]
        metadata = self._metadata_dialog(session.session_name, session)
        if metadata is None:
            return
        for key, value in metadata.items():
            setattr(session, key, value)
        self.session_store.save(self.sessions)
        self._populate_sessions_table()

    def edit_selected_metadata(self) -> None:
        self.rename_selected_session()

    def export_selected_session(self) -> None:
        rows = self.selected_session_rows()
        if not rows:
            return
        session = self.sessions[rows[0]]
        path, _filter = QFileDialog.getSaveFileName(self, "Export session", self.settings.export_directory(), "Telemetry session (*.json)")
        if path:
            self.session_store.save_session_file(session, path)

    def reveal_selected_source_file(self) -> None:
        rows = self.selected_session_rows()
        if not rows:
            return
        path = Path(self.sessions[rows[0]].source_filename)
        if path.exists():
            QFileDialog.getOpenFileName(self, "Source file", str(path.parent))
        else:
            self._show_error("Source file unavailable", "The original source file no longer exists.")

    def toggle_fullscreen(self, checked: bool | None = None) -> None:
        if checked is None:
            checked = not self.isFullScreen()
        if checked:
            self.showFullScreen()
        else:
            self.showNormal()
        self.fullscreen_action.setChecked(self.isFullScreen())

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape and self.isFullScreen():
            self.toggle_fullscreen(False)
            event.accept()
            return
        if event.key() == Qt.Key.Key_Space and QApplication.focusWidget() and isinstance(QApplication.focusWidget().parent(), GraphPanel):
            parent = QApplication.focusWidget().parent()
            parent.pause_button.toggle()
            event.accept()
            return
        super().keyPressEvent(event)

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if hasattr(self, "fullscreen_action"):
            self.fullscreen_action.setChecked(self.isFullScreen())

    def save_layout_as(self) -> None:
        path, _filter = QFileDialog.getSaveFileName(self, "Save layout", str(settings_dir() / "layouts"), "Layout files (*.json)")
        if path:
            self._write_layout(Path(path))

    def load_layout_from_file(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(self, "Load layout", str(settings_dir() / "layouts"), "Layout files (*.json)")
        if path:
            self._read_layout(Path(path))

    def _write_layout(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = self._layout_snapshot()
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def _read_layout(self, path: Path) -> bool:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError) as error:
            self._logger.warning("Failed to load layout: %s", error)
            self._show_error("Layout unavailable", f"Could not load layout: {error}")
            return False
        if not self._restore_layout_data(data, notify=True):
            return False
        return True

    def apply_builtin_layout(self, name: str) -> None:
        for dock_id in list(self.detached_windows):
            self.dock_panel_back(dock_id)
        for template_id in BUILTIN_LAYOUTS.get(name, []):
            self.create_panel_from_template(template_id)
        for dock in self.docks.values():
            dock.show()
            dock.setFloating(False)
        if name == "Live driving":
            self.imported_sessions_dock.hide()
            self.comparison_dock.hide()
        elif name == "Analysis":
            if self.active_source is None:
                self.docks["live_telemetry"].hide()
        elif name == "Diagnostics":
            for dock_id, dock in self.docks.items():
                dock.setVisible(dock_id in {"source_status", "connection_diagnostics"})
        self._ensure_window_visible()

    def reset_layout(self) -> None:
        for dock_id in list(self.detached_windows):
            self.dock_panel_back(dock_id)
        for dock in self.docks.values():
            dock.setFloating(False)
        self.apply_builtin_layout("Default")
        self.resize(1100, 760)

    def _restore_startup_layout(self) -> None:
        if self._reset_layout_requested:
            self._logger.info("Skipping saved layout because --reset-layout was requested")
            self.reset_layout()
            return
        if not self.settings.restore_layout_at_startup():
            return
        data = self.settings.load_dashboard_layout()
        if data is None:
            if self.settings.load_state() or self.settings.load_geometry():
                self._logger.warning("Legacy raw Qt layout state was ignored and cleared")
                self.settings.clear_legacy_window_layout()
            self.reset_layout()
            return
        if not self._restore_layout_data(data, notify=False):
            self._logger.warning("Saved dashboard layout was invalid; default layout loaded")
            self.reset_layout()

    def _layout_snapshot(self) -> dict:
        return {
            "schema_version": LAYOUT_SCHEMA_VERSION,
            "geometry": self._byte_array_to_text(self.saveGeometry()),
            "dock_state": self._byte_array_to_text(self.saveState()),
            "workspace": self.dashboard_workspace.snapshot(),
            "dashboard_edit_mode": self.edit_layout_action.isChecked(),
            "panels": [
                {
                    "panel_id": record.panel_id,
                    "panel_type": record.panel_type,
                    "template_id": self.panel_templates.get(record.panel_id, record.panel_type),
                    "title": record.title,
                    "visible": record.dock.isVisible() if record.dock is not None else record.panel_id in self.dashboard_workspace.panel_to_tile,
                    "detached": record.panel_id in self.detached_windows,
                    "location": record.location,
                    "config": self._panel_config(record.panel_id),
                }
                for record in self.panel_registry.records()
            ],
            "detached": [
                {
                    "panel_id": panel_id,
                    "geometry": self._byte_array_to_text(window.saveGeometry()),
                    "maximized": window.isMaximized(),
                }
                for panel_id, window in self.detached_windows.items()
            ],
        }

    def _panel_config(self, panel_id: str) -> dict:
        dock = self.docks.get(panel_id)
        widget = dock.widget() if dock is not None else self.panel_widgets.get(panel_id)
        if isinstance(widget, GraphPanel):
            return widget.settings_state()
        return {}

    def _restore_layout_data(self, data: dict, notify: bool) -> bool:
        self._layout_restore_count += 1
        if data.get("schema_version") != LAYOUT_SCHEMA_VERSION:
            self._logger.warning("Unsupported layout schema: %s", data.get("schema_version"))
            if notify:
                self._show_error("Layout unavailable", "The layout file is outdated or incompatible.")
            return False
        panels = data.get("panels", [])
        if not isinstance(panels, list):
            return False
        panel_ids = [item.get("panel_id") for item in panels if isinstance(item, dict)]
        if len(panel_ids) != len(set(panel_ids)):
            self._logger.warning("Duplicate panel IDs in saved layout: %s", panel_ids)
            return False

        for item in panels:
            if not isinstance(item, dict):
                continue
            panel_id = str(item.get("panel_id", ""))
            template_id = str(item.get("template_id", ""))
            if not panel_id or panel_id in self.docks:
                continue
            template = PANEL_TEMPLATES.get(template_id)
            if template is None:
                self._logger.warning("Ignoring unknown panel type in saved layout: %s", template_id)
                continue
            if template.panel_type == "builtin":
                continue
            if template.panel_type == "graph":
                widget = self._create_graph_panel_widget(item.get("title") or template.title, item.get("config") or template.default_config)
            elif template.panel_type == "current_lap_graph":
                widget = self._create_current_lap_graph_widget(item.get("title") or template.title, item.get("config") or template.default_config)
            else:
                widget = self._create_template_widget(template, panel_id)
            if widget is not None:
                self._register_workspace_panel(panel_id, template, widget)

        geometry_text = data.get("geometry")
        dock_state_text = data.get("dock_state")
        if isinstance(geometry_text, str) and geometry_text:
            self.restoreGeometry(self._text_to_byte_array(geometry_text))
        if isinstance(dock_state_text, str) and dock_state_text:
            if not self.restoreState(self._text_to_byte_array(dock_state_text)):
                self._logger.warning("Versioned dock state could not be restored")
                return False
        self._dock_all_qt_floating_widgets()
        workspace_data = data.get("workspace")
        if isinstance(workspace_data, dict):
            workspace_widgets = {
                panel_id: (self.panel_titles.get(panel_id, panel_id), widget)
                for panel_id, widget in self.panel_widgets.items()
                if panel_id not in self.docks
            }
            if not self.dashboard_workspace.restore(workspace_data, workspace_widgets):
                self._logger.warning("Dashboard workspace tree could not be restored")
                return False
        self.edit_layout_action.setChecked(bool(data.get("dashboard_edit_mode", False)))
        self.dashboard_workspace.set_edit_mode(self.edit_layout_action.isChecked())
        for detached in data.get("detached", []):
            if not isinstance(detached, dict):
                continue
            panel_id = str(detached.get("panel_id", ""))
            if panel_id in self.panel_widgets and panel_id not in self.detached_windows:
                self.detach_panel(panel_id, show=False)
                window = self.detached_windows.get(panel_id)
                if window is not None and isinstance(detached.get("geometry"), str):
                    window.restoreGeometry(self._text_to_byte_array(detached["geometry"]))
                    if detached.get("maximized"):
                        window.showMaximized()
                    else:
                        window.show()
        for item in panels:
            if isinstance(item, dict) and item.get("panel_id") in self.docks:
                self.docks[item["panel_id"]].setVisible(bool(item.get("visible", True)) and item["panel_id"] not in self.detached_windows)
        self._ensure_window_visible()
        return True

    def _dock_all_qt_floating_widgets(self) -> None:
        for dock in self.docks.values():
            if dock.isFloating():
                self._logger.warning("Recovered native floating dock during restore: %s", dock.objectName())
                dock.setFloating(False)

    @staticmethod
    def _byte_array_to_text(value: QByteArray) -> str:
        return bytes(value.toBase64()).decode("ascii")

    @staticmethod
    def _text_to_byte_array(value: str) -> QByteArray:
        return QByteArray.fromBase64(value.encode("ascii"))

    def _ensure_window_visible(self) -> None:
        screen = QApplication.primaryScreen()
        if screen and not screen.availableGeometry().intersects(self.frameGeometry()):
            self.move(screen.availableGeometry().topLeft())
        for window in self.detached_windows.values():
            if screen and not screen.availableGeometry().intersects(window.frameGeometry()):
                window.move(screen.availableGeometry().topLeft())

    def detach_panel(self, dock_id: str, show: bool = True) -> None:
        if dock_id in self.detached_windows:
            if show:
                self.detached_windows[dock_id].show()
                self.detached_windows[dock_id].raise_()
            return
        dock = self.docks.get(dock_id)
        if dock is not None:
            widget = dock.widget()
            if widget is None:
                return
            dock.setWidget(QWidget())
            dock.hide()
            title = dock.windowTitle()
        else:
            widget = self.dashboard_workspace.remove_panel(dock_id)
            if widget is None:
                return
            title = self.panel_titles.get(dock_id, dock_id)
        window = DetachedPanelWindow(self, dock, widget, panel_id=dock_id, title=title)
        window.setWindowOpacity(1.0)
        self.detached_windows[dock_id] = window
        if show:
            window.show()
        self._ensure_window_visible()

    def dock_panel_back(self, dock_id: str, tile_id: str | None = None) -> None:
        window = self.detached_windows.pop(dock_id, None)
        if window is None:
            dock = self.docks.get(dock_id)
            if dock is not None:
                dock.show()
            return
        widget = window.takeCentralWidget()
        dock = self.docks.get(dock_id)
        if widget is not None and dock is not None:
            dock.setWidget(widget)
            dock.show()
            dock.raise_()
        elif widget is not None:
            self.dashboard_workspace.add_panel(dock_id, self.panel_titles.get(dock_id, dock_id), widget, tile_id=tile_id)
        window.hide()
        window.deleteLater()

    def recover_all_panels(self) -> None:
        for dock_id in list(self.detached_windows):
            self.dock_panel_back(dock_id)
        for dock in self.docks.values():
            dock.show()
        self._ensure_window_visible()

    def close_panel(self, panel_id: str) -> None:
        if panel_id in self.detached_windows:
            self.dock_panel_back(panel_id)
        widget = self.dashboard_workspace.remove_panel(panel_id)
        dock = self.docks.get(panel_id)
        if dock is not None:
            dock.hide()
            return
        if widget is not None:
            if isinstance(widget, GraphPanel) and widget in self.graph_panels:
                self.graph_panels.remove(widget)
            if isinstance(widget, GraphPanel) and widget in self.current_lap_graph_panels:
                self.current_lap_graph_panels.remove(widget)
            if isinstance(widget, GraphPanel) and widget in self.saved_lap_graph_panels:
                self.saved_lap_graph_panels.remove(widget)
            if isinstance(widget, TrackMapPanel) and widget in self.track_map_panels:
                self.track_map_panels.remove(widget)
            widget.deleteLater()
        self.panel_registry.remove(panel_id)
        self.panel_widgets.pop(panel_id, None)
        self.panel_titles.pop(panel_id, None)
        self.panel_templates.pop(panel_id, None)

    def toggle_dashboard_edit_mode(self, checked: bool) -> None:
        self.dashboard_workspace.set_edit_mode(checked)

    def split_selected_tile(self, direction: str) -> str | None:
        tile = self.dashboard_workspace.first_tile()
        return self.dashboard_workspace.split_tile(tile.tile_id, direction)

    def create_selected_tab_group(self) -> None:
        tile = self.dashboard_workspace.first_tile()
        if tile.tabs.count() == 0:
            self._add_panel_to_tile_menu(tile.tile_id)

    def toggle_selected_compact_mode(self) -> None:
        tile = self.dashboard_workspace.first_tile()
        panel_id = tile.current_panel_id()
        if panel_id:
            widget = self.panel_widgets.get(panel_id)
            self.set_panel_compact_mode(panel_id, not bool(widget and widget.property("compact_mode")))

    def set_panel_compact_mode(self, panel_id: str, compact: bool) -> None:
        widget = self.panel_widgets.get(panel_id)
        if widget is None:
            return
        widget.setProperty("compact_mode", compact)
        if isinstance(widget, GraphPanel):
            widget.set_compact_mode(compact)
        else:
            margins = (2, 2, 2, 2) if compact else (6, 6, 6, 6)
            layout = widget.layout()
            if layout is not None:
                layout.setContentsMargins(*margins)

    def _add_panel_to_tile_menu(self, tile_id: str) -> None:
        menu = QMenu(self)
        for group_name, template_ids in TEMPLATE_GROUPS.items():
            group_menu = menu.addMenu(group_name)
            for template_id in template_ids:
                template = PANEL_TEMPLATES[template_id]
                action = QAction(template.title, self)
                action.triggered.connect(lambda _checked=False, item=template_id, target=tile_id: self.create_panel_from_template(item, target))
                group_menu.addAction(action)
        menu.exec(self.mapToGlobal(self.rect().center()))

    def apply_quick_grid(self, columns: int, rows: int) -> None:
        self.dashboard_workspace.quick_grid(columns, rows)

    def apply_dashboard_preset(self, name: str) -> None:
        if name == "Live driving compact":
            self.dashboard_workspace.quick_grid(2, 2)
            for template in ("pedals_graph", "speed_rpm_graph", "live_values", "sector_timing"):
                panel_id = self.create_panel_from_template(template)
                if panel_id:
                    self.set_panel_compact_mode(panel_id, template in {"live_values", "sector_timing"})
        elif name == "Timing wall":
            self.dashboard_workspace.quick_grid(3, 3)
            for template in ("live_values", "sector_timing", "sector_timing", "sector_timing", "best_laps", "live_lap_timing"):
                panel_id = self.create_panel_from_template(template)
                if panel_id:
                    self.set_panel_compact_mode(panel_id, template != "live_lap_timing")
        elif name == "Analysis workspace":
            self.dashboard_workspace.quick_grid(2, 3)
            for template in ("lap_comparison", "lap_history", "pedals_graph", "sector_timing", "time_delta_graph"):
                self.create_panel_from_template(template)
        elif name == "Ultrawide telemetry":
            self.dashboard_workspace.quick_grid(4, 2)
            for template in ("live_values", "pedals_graph", "speed_rpm_graph", "live_lap_timing", "sector_timing", "source_status", "connection_diagnostics"):
                self.create_panel_from_template(template)

    def open_settings_dialog(self) -> None:
        dialog = SettingsDialog(self.settings, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.port_input.setValue(self.settings.f1_udp_port())
            for panel in self.graph_panels:
                panel.refresh_timer.setInterval(self.settings.graph_refresh_ms())

    def open_log_directory(self) -> None:
        QMessageBox.information(self, "Log directory", str(logs_dir()))

    def show_diagnostics(self) -> None:
        from app.diagnostics import collect_diagnostics

        QMessageBox.information(self, "Diagnostics", "\n".join(collect_diagnostics(self.port_input.value())))

    def show_about(self) -> None:
        QMessageBox.about(self, "About Racing Telemetry", "Racing Telemetry\nOffline telemetry dashboard.")

    def select_source(self, source_id: str) -> None:
        index = self.source_combo.findData(source_id)
        if index >= 0:
            self.source_combo.setCurrentIndex(index)

    def _save_udp_port(self, port: int) -> None:
        if self.active_source is None and self.source_combo.currentData() == "f1_2018":
            self.settings.set_f1_udp_port(port)

    def _source_selection_changed(self) -> None:
        if self.active_source is not None:
            self.stop_active_source()
        source_id = self.source_combo.currentData()
        self.source_label.setText(f"Source: {SOURCE_LABELS[source_id]}")
        source_settings = self.settings.source_settings(source_id)
        if source_settings.udp_port is not None:
            self.port_input.setValue(source_settings.udp_port)
        self._reset_source_diagnostics()
        self._update_controls()

    def _reset_source_diagnostics(self) -> None:
        for label in [*self.f1_labels.values(), *self.shared_memory_labels.values()]:
            label.setText("--")
        for key in self.common_labels:
            self.common_labels[key].setText("--")
        self.common_labels["connection_state"].setText("Stopped")

    def _show_stopped_telemetry(self) -> None:
        self.telemetry_labels["Speed"].setText("0 km/h")
        self.telemetry_labels["RPM"].setText("0 rpm")
        self.telemetry_labels["Gear"].setText("N")
        self.telemetry_labels["Throttle"].setText("0%")
        self.telemetry_labels["Brake"].setText("0%")
        self.common_labels["running"].setText("No")
        self.common_labels["connection_state"].setText("Stopped")

    def _update_controls(self) -> None:
        is_running = self.active_source is not None
        source_id = self.source_combo.currentData()
        is_udp = source_id == "f1_2018"
        self.start_button.setEnabled(not is_running)
        self.stop_button.setEnabled(is_running)
        self.source_combo.setEnabled(not is_running)
        self.port_input.setEnabled(is_udp and not is_running)
        self.port_help_label.setVisible(is_udp)
        self.f1_group.setVisible(is_udp)
        self.shared_memory_group.setVisible(source_id in {"assetto_corsa", "assetto_corsa_competizione"})

    def _update_actions(self) -> None:
        is_running = self.active_source is not None
        has_selection = bool(getattr(self, "sessions_table", None) and self.selected_session_rows())
        self.start_source_action.setEnabled(not is_running)
        self.stop_source_action.setEnabled(is_running)
        self.stop_recording_action.setEnabled(self.is_recording)
        self.save_recorded_action.setEnabled(bool(self.recording_samples))
        self.export_selected_action.setEnabled(has_selection)
        self.open_lap_graph_action.setEnabled(bool(getattr(self, "laps_table", None) and self.selected_lap_rows()))

    def _refresh_live_diagnostics(self) -> None:
        if self.active_source is None:
            return
        now = time.monotonic()
        elapsed = max(0.001, now - self._rate_window_started)
        self.common_labels["updates_per_second"].setText(f"{self._sample_count_window / elapsed:.1f}")
        self._sample_count_window = 0
        self._rate_window_started = now
        if self._last_sample_wall_time and time.time() - self._last_sample_wall_time >= 1.0 and self.active_source.state() == SourceState.CONNECTED:
            self.common_labels["connection_state"].setText("No telemetry received")

    def _detach_source(self, source) -> None:
        for signal, handler in (
            (source.sample_received, self.handle_telemetry_sample),
            (source.status_changed, self.handle_source_status),
            (source.error_occurred, self.handle_source_error),
            (source.diagnostics_changed, self.handle_diagnostics),
        ):
            try:
                signal.disconnect(handler)
            except (RuntimeError, TypeError):
                pass

    def _show_error(self, title: str, message: str) -> None:
        self._logger.error("%s: %s", title, message)
        self.error_label.setText(message)
        if os.environ.get("QT_QPA_PLATFORM") == "offscreen":
            return
        QMessageBox.warning(self, title, message)

    def closeEvent(self, event: QCloseEvent) -> None:
        self.stop_active_source()
        self.settings.save_dashboard_layout(self._layout_snapshot())
        self.settings.clear_legacy_window_layout()
        self.settings.set_graph_panels_state([panel.settings_state() for panel in self.graph_panels])
        self.settings.save_was_maximized(self.isMaximized())
        self.settings.sync()
        self.session_store.save(self.sessions)
        for window in self.detached_windows.values():
            window.hide()
        event.accept()


def graph_availability(lap: LapResult) -> str:
    if lap.telemetry_series is not None:
        return "Available in memory"
    if lap.samples:
        return "Raw samples"
    if lap.complete:
        return "Summary only"
    return "Unavailable"


class SettingsDialog(QDialog):
    def __init__(self, settings: AppSettings, parent=None) -> None:
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle("Application settings")
        layout = QFormLayout(self)

        self.port_spin = QSpinBox()
        self.port_spin.setRange(1, 65535)
        self.port_spin.setValue(settings.f1_udp_port())

        self.data_dir = QLineEdit(settings.data_directory())
        self.import_dir = QLineEdit(settings.import_directory())
        self.export_dir = QLineEdit(settings.export_directory())

        self.refresh_spin = QSpinBox()
        self.refresh_spin.setRange(33, 2000)
        self.refresh_spin.setValue(settings.graph_refresh_ms())

        self.history_spin = QSpinBox()
        self.history_spin.setRange(100, 50000)
        self.history_spin.setValue(settings.graph_history_limit())

        self.restore_layout = QCheckBox()
        self.restore_layout.setChecked(settings.restore_layout_at_startup())
        self.confirm_remove = QCheckBox()
        self.confirm_remove.setChecked(settings.confirm_remove_sessions())
        self.fullscreen_startup = QCheckBox()
        self.fullscreen_startup.setChecked(settings.fullscreen_at_startup())

        layout.addRow("F1 2018 UDP port", self.port_spin)
        layout.addRow("Data directory", self.data_dir)
        layout.addRow("Import directory", self.import_dir)
        layout.addRow("Export directory", self.export_dir)
        layout.addRow("Graph refresh (ms)", self.refresh_spin)
        layout.addRow("Maximum live graph history", self.history_spin)
        layout.addRow("Restore previous layout", self.restore_layout)
        layout.addRow("Confirm before removing sessions", self.confirm_remove)
        layout.addRow("Start in fullscreen", self.fullscreen_startup)

        reset_button = QPushButton("Reset settings")
        reset_button.clicked.connect(self._reset_settings)
        layout.addRow(reset_button)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def accept(self) -> None:
        self.settings.set_f1_udp_port(self.port_spin.value())
        self.settings.set_import_directory(self.import_dir.text())
        self.settings.set_export_directory(self.export_dir.text())
        self.settings.set_graph_refresh_ms(self.refresh_spin.value())
        self.settings.set_graph_history_limit(self.history_spin.value())
        self.settings.set_restore_layout_at_startup(self.restore_layout.isChecked())
        self.settings.set_confirm_remove_sessions(self.confirm_remove.isChecked())
        self.settings.set_fullscreen_at_startup(self.fullscreen_startup.isChecked())
        self.settings.sync()
        super().accept()

    def _reset_settings(self) -> None:
        self.settings.reset()
        self.port_spin.setValue(DEFAULT_F1_UDP_PORT)


class ImportWorker(QObject):
    imported = Signal(object)
    failed = Signal(str)

    def __init__(self, path: str, metadata: dict) -> None:
        super().__init__()
        self.path = path
        self.metadata = metadata

    def run(self) -> None:
        try:
            self.imported.emit(import_telemetry_file(self.path, self.metadata))
        except TelemetryImportError as error:
            self.failed.emit(str(error))
        except Exception as error:  # pragma: no cover - defensive worker boundary.
            logging.getLogger(__name__).exception("Unexpected import failure")
            self.failed.emit(f"Unexpected import failure: {error}")
