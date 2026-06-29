from __future__ import annotations

import base64
import json
import logging
import time
from pathlib import Path

from PySide6.QtCore import QByteArray, QObject, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QAction, QCloseEvent, QKeyEvent, QKeySequence
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
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from app.paths import logs_dir, settings_dir
from app.settings import AppSettings, DEFAULT_F1_UDP_PORT
from models import METRICS, TelemetrySample, TelemetrySession, format_gear
from telemetry import SOURCE_LABELS, SOURCE_TYPES
from telemetry.base import SourceState
from telemetry.comparison import build_comparison_series, preferred_axis, speed_delta
from telemetry.f1_2018 import F12018TelemetrySource
from telemetry.importer import TelemetryImportError, import_telemetry_file
from telemetry.session_store import SessionStore
from ui.graph_panel import GraphPanel, PENS

try:
    import pyqtgraph as pg
except ImportError:  # pragma: no cover
    pg = None


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.settings = AppSettings()
        self.session_store = SessionStore()
        self.sessions: list[TelemetrySession] = self.session_store.load()
        self.active_source = None
        self.telemetry_labels: dict[str, QLabel] = {}
        self.common_labels: dict[str, QLabel] = {}
        self.f1_labels: dict[str, QLabel] = {}
        self.shared_memory_labels: dict[str, QLabel] = {}
        self.docks: dict[str, QDockWidget] = {}
        self.graph_panels: list[GraphPanel] = []
        self.graph_counter = 0
        self.recording_samples: list[TelemetrySample] = []
        self.is_recording = False
        self._last_sample_wall_time = 0.0
        self._sample_count_window = 0
        self._rate_window_started = time.monotonic()
        self._logger = logging.getLogger(__name__)

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
        self._load_saved_layout()
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
        self.add_graph_action.triggered.connect(self.add_graph_panel)

        self.reset_layout_action = QAction("Reset layout", self)
        self.reset_layout_action.setShortcut(QKeySequence("Ctrl+0"))
        self.reset_layout_action.triggered.connect(self.reset_layout)

        self.save_layout_action = QAction("Save layout as...", self)
        self.save_layout_action.triggered.connect(self.save_layout_as)

        self.load_layout_action = QAction("Load layout...", self)
        self.load_layout_action.triggered.connect(self.load_layout_from_file)

        self.start_source_action = QAction("Start source", self)
        self.start_source_action.triggered.connect(self.start_selected_source)

        self.stop_source_action = QAction("Stop source", self)
        self.stop_source_action.triggered.connect(self.stop_active_source)

        self.start_recording_action = QAction("Start recording", self)
        self.start_recording_action.setShortcut(QKeySequence("Ctrl+R"))
        self.start_recording_action.triggered.connect(self.toggle_recording)

        self.stop_recording_action = QAction("Stop recording", self)
        self.stop_recording_action.triggered.connect(self.stop_recording)

        self.settings_action = QAction("Application settings...", self)
        self.settings_action.triggered.connect(self.open_settings_dialog)

        self.open_logs_action = QAction("Open log directory", self)
        self.open_logs_action.triggered.connect(self.open_log_directory)

        self.diagnostics_action = QAction("Diagnostics", self)
        self.diagnostics_action.triggered.connect(self.show_diagnostics)

        self.about_action = QAction("About", self)
        self.about_action.triggered.connect(self.show_about)

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
        self.view_menu.addAction(self.reset_layout_action)

        layouts_menu = self.menuBar().addMenu("Layouts")
        for name in ("Default", "Live Driving", "Telemetry Analysis"):
            action = QAction(name, self)
            action.triggered.connect(lambda _checked=False, layout_name=name: self.apply_builtin_layout(layout_name))
            layouts_menu.addAction(action)
        layouts_menu.addSeparator()
        layouts_menu.addAction(self.save_layout_action)
        layouts_menu.addAction(self.load_layout_action)

        telemetry_menu = self.menuBar().addMenu("Telemetry")
        telemetry_menu.addAction(self.start_source_action)
        telemetry_menu.addAction(self.stop_source_action)
        telemetry_menu.addSeparator()
        telemetry_menu.addAction(self.start_recording_action)
        telemetry_menu.addAction(self.stop_recording_action)

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
        self.addToolBar(toolbar)

    def _build_interface(self) -> None:
        self.setCentralWidget(self._create_source_controls())
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
        self.add_graph_panel("Live Graphs")
        self._restore_graph_panel_settings()

        for dock in self.docks.values():
            self.view_menu.addAction(dock.toggleViewAction())

    def _add_dock(self, object_name: str, title: str, widget: QWidget, area: Qt.DockWidgetArea) -> QDockWidget:
        dock = QDockWidget(title, self)
        dock.setObjectName(object_name)
        dock.setWidget(widget)
        dock.setAllowedAreas(Qt.DockWidgetArea.AllDockWidgetAreas)
        dock.setFeatures(
            QDockWidget.DockWidgetFeature.DockWidgetMovable
            | QDockWidget.DockWidgetFeature.DockWidgetFloatable
            | QDockWidget.DockWidgetFeature.DockWidgetClosable
        )
        self.addDockWidget(area, dock)
        self.docks[object_name] = dock
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

        self.record_button = QPushButton("Start recording")
        self.record_button.clicked.connect(self.toggle_recording)
        self.recording_label = QLabel("Recording: off")
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
        for key, caption in {"shared_memory": "Shared memory:", "game_state": "Game state:", "last_error": "Last error:"}.items():
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

    def add_graph_panel(self, title: str | None = None) -> None:
        self.graph_counter += 1
        panel_title = title or f"Graph Panel {self.graph_counter}"
        panel = GraphPanel(panel_title, self.settings.graph_refresh_ms(), self.settings.graph_history_limit(), self)
        self.graph_panels.append(panel)
        dock_id = f"graph_panel_{self.graph_counter}"
        dock = self._add_dock(dock_id, panel_title, panel, Qt.DockWidgetArea.RightDockWidgetArea)
        dock.visibilityChanged.connect(self._update_actions)
        if hasattr(self, "view_menu"):
            self.view_menu.addAction(dock.toggleViewAction())

    def _restore_graph_panel_settings(self) -> None:
        states = self.settings.graph_panels_state()
        if not states:
            return
        while len(self.graph_panels) < len(states):
            self.add_graph_panel()
        for panel, state in zip(self.graph_panels, states):
            panel.restore_settings_state(state)

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
        self.common_labels["car_name"].setText(sample.car_name or "--")
        self.common_labels["track_name"].setText(sample.track_name or "--")
        self.common_labels["session_state"].setText(sample.session_state or "--")
        for panel in self.graph_panels:
            panel.add_sample(sample)
        if self.is_recording:
            self.recording_samples.append(sample)
            self.recording_count_label.setText(f"Samples: {len(self.recording_samples)}")

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
                self.common_labels["car_name"].setText(text or "--")
            if key == "track_name":
                self.common_labels["track_name"].setText(text or "--")
            if key == "game_state":
                self.common_labels["session_state"].setText(text or "--")
            if key == "updates_per_second":
                self.common_labels["updates_per_second"].setText(text or "--")
            if key == "last_error":
                self.common_labels["latest_error"].setText(text or "--")

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
                session.track,
                session.car,
                session.lap_label,
                f"{session.duration:.1f}s",
                str(session.sample_count),
            ]
            for column, value in enumerate(values, start=1):
                self.sessions_table.setItem(row, column, QTableWidgetItem(value))
        self.sessions_table.resizeColumnsToContents()
        self._update_actions()

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
        self.record_button.setText("Stop recording")
        self.recording_label.setText("Recording: on")
        self.recording_count_label.setText("Samples: 0")
        self._update_actions()

    def stop_recording(self) -> None:
        self.is_recording = False
        self.record_button.setText("Start recording")
        self.recording_label.setText("Recording: off")
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
        data = {
            "geometry": bytes(self.saveGeometry().toBase64()).decode("ascii"),
            "state": bytes(self.saveState().toBase64()).decode("ascii"),
        }
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def _read_layout(self, path: Path) -> bool:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            geometry = QByteArray.fromBase64(data["geometry"].encode("ascii"))
            state = QByteArray.fromBase64(data["state"].encode("ascii"))
        except (OSError, KeyError, json.JSONDecodeError, TypeError) as error:
            self._logger.exception("Failed to load layout")
            self._show_error("Layout unavailable", f"Could not load layout: {error}")
            return False
        self.restoreGeometry(geometry)
        if not self.restoreState(state):
            self._show_error("Layout unavailable", "The layout file is outdated or incompatible.")
            return False
        self._ensure_window_visible()
        return True

    def apply_builtin_layout(self, name: str) -> None:
        for dock in self.docks.values():
            dock.show()
        if name == "Live Driving":
            self.imported_sessions_dock.hide()
            self.comparison_dock.hide()
        elif name == "Telemetry Analysis":
            if self.active_source is None:
                self.docks["live_telemetry"].hide()
        self._ensure_window_visible()

    def reset_layout(self) -> None:
        self.apply_builtin_layout("Default")
        self.resize(1100, 760)

    def _load_saved_layout(self) -> None:
        if not self.settings.restore_layout_at_startup():
            return
        geometry = self.settings.load_geometry()
        state = self.settings.load_state()
        if geometry:
            self.restoreGeometry(geometry)
        if state and not self.restoreState(state):
            self._logger.warning("Saved dock state could not be restored")
        self._ensure_window_visible()

    def _ensure_window_visible(self) -> None:
        screen = QApplication.primaryScreen()
        if screen and not screen.availableGeometry().intersects(self.frameGeometry()):
            self.move(screen.availableGeometry().topLeft())

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
        QMessageBox.warning(self, title, message)

    def closeEvent(self, event: QCloseEvent) -> None:
        self.stop_active_source()
        self.settings.save_geometry(self.saveGeometry())
        self.settings.save_state(self.saveState())
        self.settings.set_graph_panels_state([panel.settings_state() for panel in self.graph_panels])
        self.settings.save_was_maximized(self.isMaximized())
        self.settings.sync()
        self.session_store.save(self.sessions)
        event.accept()


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
