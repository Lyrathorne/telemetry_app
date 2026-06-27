from collections import deque

from PySide6.QtGui import QCloseEvent, QIntValidator
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from models import TelemetrySample, format_gear
from telemetry import SOURCE_LABELS, SOURCE_TYPES
from telemetry.f1_2018 import F12018TelemetrySource

try:
    import pyqtgraph as pg
except ImportError:  # pragma: no cover - the app still runs without graphs.
    pg = None


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()

        self.setWindowTitle("Racing Telemetry")
        self.setMinimumSize(760, 640)

        self.active_source = None
        self.telemetry_labels: dict[str, QLabel] = {}
        self.common_labels: dict[str, QLabel] = {}
        self.f1_labels: dict[str, QLabel] = {}
        self.shared_memory_labels: dict[str, QLabel] = {}
        self.history_limit = 600
        self.sample_index = 0
        self.speed_history = deque(maxlen=self.history_limit)
        self.rpm_history = deque(maxlen=self.history_limit)
        self.sample_history = deque(maxlen=self.history_limit)
        self.speed_curve = None
        self.rpm_curve = None

        self._build_interface()
        self._show_stopped_telemetry()
        self._update_controls()
        self._source_selection_changed()

    def _build_interface(self) -> None:
        central_widget = QWidget()
        main_layout = QVBoxLayout()

        self.source_combo = QComboBox()
        for source_id, label in SOURCE_LABELS.items():
            self.source_combo.addItem(label, source_id)

        self.source_combo.currentIndexChanged.connect(self._source_selection_changed)

        self.port_input = QLineEdit("20777")
        self.port_input.setValidator(QIntValidator(1, 65535, self))

        self.start_button = QPushButton("Start")
        self.stop_button = QPushButton("Stop")
        self.start_button.clicked.connect(self.start_selected_source)
        self.stop_button.clicked.connect(self.stop_active_source)

        source_layout = QGridLayout()
        source_layout.addWidget(QLabel("Source:"), 0, 0)
        source_layout.addWidget(self.source_combo, 0, 1)
        source_layout.addWidget(QLabel("F1 UDP port:"), 1, 0)
        source_layout.addWidget(self.port_input, 1, 1)
        source_layout.addWidget(self.start_button, 0, 2)
        source_layout.addWidget(self.stop_button, 1, 2)

        self.source_label = QLabel("Source: --")
        self.status_label = QLabel("Status: Stopped")
        self.error_label = QLabel("")
        self.error_label.setWordWrap(True)
        self.error_label.setStyleSheet("color: #b00020;")

        main_layout.addLayout(source_layout)
        main_layout.addWidget(self.source_label)
        main_layout.addWidget(self.status_label)
        main_layout.addLayout(self._create_telemetry_layout())
        main_layout.addWidget(self._create_common_status_group())
        main_layout.addWidget(self._create_graph_group())
        main_layout.addWidget(self._create_f1_diagnostics_group())
        main_layout.addWidget(self._create_shared_memory_group())
        main_layout.addWidget(self.error_label)

        central_widget.setLayout(main_layout)
        self.setCentralWidget(central_widget)

    def _create_telemetry_layout(self) -> QGridLayout:
        layout = QGridLayout()
        telemetry_names = ["Speed", "RPM", "Gear", "Throttle", "Brake"]

        for row, name in enumerate(telemetry_names):
            name_label = QLabel(f"{name}:")
            value_label = QLabel("--")
            name_label.setStyleSheet("font-size: 15px;")
            value_label.setStyleSheet("font-size: 15px; font-weight: bold;")
            layout.addWidget(name_label, row, 0)
            layout.addWidget(value_label, row, 1)
            self.telemetry_labels[name] = value_label

        return layout

    def _create_common_status_group(self) -> QGroupBox:
        group = QGroupBox("Source status")
        layout = QFormLayout()
        fields = {
            "running": "Running:",
            "latest_update": "Latest update:",
            "car_name": "Car:",
            "track_name": "Track:",
            "session_state": "Session state:",
        }

        for key, caption in fields.items():
            label = QLabel("--")
            layout.addRow(caption, label)
            self.common_labels[key] = label

        group.setLayout(layout)
        return group

    def _create_graph_group(self) -> QGroupBox:
        group = QGroupBox("Live graphs")
        layout = QVBoxLayout()

        if pg is None:
            layout.addWidget(QLabel("pyqtgraph is not available."))
        else:
            self.speed_plot = pg.PlotWidget()
            self.speed_plot.setMinimumHeight(140)
            self.speed_plot.setLabel("left", "Speed", units="km/h")
            self.speed_plot.setLabel("bottom", "Samples")
            self.speed_curve = self.speed_plot.plot(pen=pg.mkPen("#1f77b4", width=2))

            self.rpm_plot = pg.PlotWidget()
            self.rpm_plot.setMinimumHeight(140)
            self.rpm_plot.setLabel("left", "RPM")
            self.rpm_plot.setLabel("bottom", "Samples")
            self.rpm_curve = self.rpm_plot.plot(pen=pg.mkPen("#d62728", width=2))

            layout.addWidget(self.speed_plot)
            layout.addWidget(self.rpm_plot)

        group.setLayout(layout)
        return group

    def _create_f1_diagnostics_group(self) -> QGroupBox:
        self.f1_group = QGroupBox("F1 UDP diagnostics")
        layout = QFormLayout()
        fields = {
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

        for key, caption in fields.items():
            label = QLabel("--")
            if key == "packet_preview":
                label.setWordWrap(True)
                label.setStyleSheet("font-family: Consolas, monospace;")
            layout.addRow(caption, label)
            self.f1_labels[key] = label

        self.f1_group.setLayout(layout)
        return self.f1_group

    def _create_shared_memory_group(self) -> QGroupBox:
        self.shared_memory_group = QGroupBox("Shared-memory status")
        layout = QFormLayout()
        fields = {
            "shared_memory": "Shared memory:",
            "game_state": "Game state:",
            "last_error": "Last error:",
        }

        for key, caption in fields.items():
            label = QLabel("--")
            label.setWordWrap(True)
            layout.addRow(caption, label)
            self.shared_memory_labels[key] = label

        self.shared_memory_group.setLayout(layout)
        return self.shared_memory_group

    def start_selected_source(self) -> None:
        if self.active_source is not None:
            return

        source_id = self.source_combo.currentData()
        source_class = SOURCE_TYPES[source_id]
        kwargs = {}

        if source_class is F12018TelemetrySource:
            port = self._get_udp_port()
            if port is None:
                return
            kwargs["port"] = port

        self._clear_history()
        self.error_label.setText("")
        self._reset_source_diagnostics()
        self.active_source = source_class(**kwargs)
        self.active_source.sample_received.connect(self.handle_telemetry_sample)
        self.active_source.status_changed.connect(self.handle_source_status)
        self.active_source.error_occurred.connect(self.handle_source_error)
        self.active_source.diagnostics_changed.connect(self.handle_diagnostics)
        self.source_label.setText(f"Source: {SOURCE_LABELS[source_id]}")
        self.common_labels["running"].setText("Yes")
        self.active_source.start()
        self._update_controls()

    def stop_active_source(self) -> None:
        if self.active_source is None:
            self._update_controls()
            return

        source = self.active_source
        source.stop()
        source.sample_received.disconnect(self.handle_telemetry_sample)
        source.status_changed.disconnect(self.handle_source_status)
        source.error_occurred.disconnect(self.handle_source_error)
        source.diagnostics_changed.disconnect(self.handle_diagnostics)
        self.active_source = None
        self.status_label.setText("Status: Stopped")
        self.common_labels["running"].setText("No")
        self._update_controls()

    def handle_telemetry_sample(self, sample: TelemetrySample) -> None:
        self.telemetry_labels["Speed"].setText(f"{sample.speed_kmh:.0f} km/h")
        self.telemetry_labels["RPM"].setText(f"{sample.rpm} rpm")
        self.telemetry_labels["Gear"].setText(format_gear(sample.gear))
        self.telemetry_labels["Throttle"].setText(f"{sample.throttle_percent:.0f}%")
        self.telemetry_labels["Brake"].setText(f"{sample.brake_percent:.0f}%")

        self.common_labels["latest_update"].setText("Received")
        self.common_labels["car_name"].setText(sample.car_name or "--")
        self.common_labels["track_name"].setText(sample.track_name or "--")
        self.common_labels["session_state"].setText(sample.session_state or "--")

        self.sample_index += 1
        self.sample_history.append(self.sample_index)
        self.speed_history.append(sample.speed_kmh)
        self.rpm_history.append(sample.rpm)
        self._update_graphs()

    def handle_source_status(self, status: str) -> None:
        self.status_label.setText(f"Status: {status}")

    def handle_source_error(self, message: str) -> None:
        self.error_label.setText(message)

    def handle_diagnostics(self, diagnostics: dict) -> None:
        for key, value in diagnostics.items():
            text = str(value)

            if key in self.f1_labels:
                if key == "latest_packet_size" and isinstance(value, int):
                    text = f"{value} bytes"
                self.f1_labels[key].setText(text)

            if key in self.shared_memory_labels:
                self.shared_memory_labels[key].setText(text or "--")

            if key == "car_name":
                self.common_labels["car_name"].setText(text or "--")

            if key == "track_name":
                self.common_labels["track_name"].setText(text or "--")

            if key == "game_state":
                self.common_labels["session_state"].setText(text or "--")

    def _source_selection_changed(self) -> None:
        if self.active_source is not None:
            self.stop_active_source()

        source_id = self.source_combo.currentData()
        self.source_label.setText(f"Source: {SOURCE_LABELS[source_id]}")
        self._reset_source_diagnostics()
        self._update_controls()

    def _get_udp_port(self) -> int | None:
        port_text = self.port_input.text().strip()

        if not port_text:
            self.handle_source_error("Please enter a UDP port number.")
            return None

        try:
            port = int(port_text)
        except ValueError:
            self.handle_source_error("UDP port must be a number.")
            return None

        if port < 1 or port > 65535:
            self.handle_source_error("UDP port must be between 1 and 65535.")
            return None

        return port

    def _reset_source_diagnostics(self) -> None:
        for label in self.f1_labels.values():
            label.setText("--")

        for label in self.shared_memory_labels.values():
            label.setText("--")

        self.common_labels["latest_update"].setText("--")
        self.common_labels["car_name"].setText("--")
        self.common_labels["track_name"].setText("--")
        self.common_labels["session_state"].setText("--")

    def _clear_history(self) -> None:
        self.sample_index = 0
        self.sample_history.clear()
        self.speed_history.clear()
        self.rpm_history.clear()
        self._update_graphs()

    def _update_graphs(self) -> None:
        if self.speed_curve is not None:
            self.speed_curve.setData(list(self.sample_history), list(self.speed_history))

        if self.rpm_curve is not None:
            self.rpm_curve.setData(list(self.sample_history), list(self.rpm_history))

    def _show_stopped_telemetry(self) -> None:
        self.telemetry_labels["Speed"].setText("0 km/h")
        self.telemetry_labels["RPM"].setText("0 rpm")
        self.telemetry_labels["Gear"].setText("N")
        self.telemetry_labels["Throttle"].setText("0%")
        self.telemetry_labels["Brake"].setText("0%")
        self.common_labels["running"].setText("No")

    def _update_controls(self) -> None:
        is_running = self.active_source is not None
        source_id = self.source_combo.currentData()
        is_f1 = source_id == "f1_2018"

        self.start_button.setEnabled(not is_running)
        self.stop_button.setEnabled(is_running)
        self.source_combo.setEnabled(not is_running)
        self.port_input.setEnabled(is_f1 and not is_running)
        self.f1_group.setVisible(is_f1)
        self.shared_memory_group.setVisible(
            source_id in {"assetto_corsa", "assetto_corsa_competizione"}
        )

    def closeEvent(self, event: QCloseEvent) -> None:
        self.stop_active_source()
        event.accept()
