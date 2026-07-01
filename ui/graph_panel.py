from __future__ import annotations

import time
from dataclasses import dataclass

import numpy as np
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
    QSizePolicy,
)

from models import METRICS, TelemetrySample, sample_metric_value

try:
    import pyqtgraph as pg
except ImportError:  # pragma: no cover
    pg = None


PENS = ["#1f77b4", "#d62728", "#2ca02c", "#ff7f0e", "#9467bd", "#17becf", "#8c564b"]
DEFAULT_RENDER_INTERVAL_MS = 50
MAX_RENDER_POINTS = 5000

X_AXIS_MODES = {
    "full_session": "Full session",
    "follow_live": "Follow live",
    "recent_window": "Recent window",
    "manual": "Manual range",
}

Y_AXIS_MODES = {
    "auto": "Automatic range",
    "metric_default": "Metric default range",
    "manual": "Manual range",
}

METRIC_GROUPS = {
    "speed_kmh": "speed",
    "rpm": "rpm",
    "throttle_percent": "pedals",
    "brake_percent": "pedals",
    "clutch_percent": "pedals",
    "gear": "gear",
    "steering": "steering",
}

METRIC_DEFAULT_RANGES = {
    "speed_kmh": (0.0, 300.0),
    "rpm": (0.0, 12000.0),
    "throttle_percent": (0.0, 100.0),
    "brake_percent": (0.0, 100.0),
    "clutch_percent": (0.0, 100.0),
    "gear": (-1.0, 8.0),
    "steering": (-100.0, 100.0),
}

METRIC_UNITS = {
    "speed_kmh": "km/h",
    "rpm": "rpm",
    "throttle_percent": "%",
    "brake_percent": "%",
    "clutch_percent": "%",
    "gear": "gear",
    "steering": "steering",
}


@dataclass(slots=True)
class GraphDiagnostics:
    telemetry_samples: int = 0
    rendered_frames: int = 0
    last_render_ms: float = 0.0
    latest_latency_ms: float = 0.0
    visible_samples: int = 0
    rendered_points: int = 0


class GraphPanel(QWidget):
    def __init__(self, title: str, refresh_ms: int, history_limit: int, parent=None) -> None:
        super().__init__(parent)
        self.title = title
        self.render_interval_ms = max(33, refresh_ms or DEFAULT_RENDER_INTERVAL_MS)
        self.max_render_points = max(500, history_limit or MAX_RENDER_POINTS)
        self.samples: list[TelemetrySample] = []
        self._received_monotonic: list[float] = []
        self.curves = {}
        self.metric_visible: dict[str, bool] = {}
        self.paused = False
        self.first_x_value: float | None = None
        self.latest_displayed_x: float | None = None
        self.latest_sample_x: float | None = None
        self.diagnostics = GraphDiagnostics()

        self._build_ui()
        self.refresh_timer = QTimer(self)
        self.refresh_timer.setInterval(self.render_interval_ms)
        self.refresh_timer.timeout.connect(self.refresh_plot)
        self.refresh_timer.start()
        self.reset_default_metrics()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        graph_toolbar = QHBoxLayout()
        self.settings_toggle_button = QPushButton("Hide graph settings")
        self.settings_toggle_button.setCheckable(True)
        self.settings_toggle_button.toggled.connect(self.set_settings_hidden)
        self.toolbar_pause_button = QPushButton("Pause")
        self.toolbar_pause_button.setCheckable(True)
        self.toolbar_pause_button.toggled.connect(self._set_paused)
        self.toolbar_live_button = QPushButton("Return to live")
        self.toolbar_live_button.clicked.connect(self.return_to_live)
        self.toolbar_reset_button = QPushButton("Reset view")
        self.toolbar_reset_button.clicked.connect(self.reset_view)
        self.export_image_button = QPushButton("Export image")
        self.export_image_button.clicked.connect(self.export_image)
        for widget in (
            self.settings_toggle_button,
            self.toolbar_pause_button,
            self.toolbar_live_button,
            self.toolbar_reset_button,
            self.export_image_button,
        ):
            graph_toolbar.addWidget(widget)
        graph_toolbar.addStretch(1)
        layout.addLayout(graph_toolbar)

        self.settings_container = QWidget()
        settings_layout = QVBoxLayout(self.settings_container)
        settings_layout.setContentsMargins(0, 0, 0, 0)
        settings_layout.setSpacing(4)

        metric_layout = QGridLayout()
        self.metric_combo = QComboBox()
        for key, label in METRICS.items():
            self.metric_combo.addItem(f"{label} ({METRIC_UNITS.get(key, '')})", key)

        self.add_metric_button = QPushButton("Add >")
        self.add_metric_button.clicked.connect(self.add_selected_metric)
        self.remove_metric_button = QPushButton("< Remove")
        self.remove_metric_button.clicked.connect(self.remove_selected_metric)
        self.clear_metrics_button = QPushButton("Clear metrics")
        self.clear_metrics_button.clicked.connect(self.clear_metrics)
        self.reset_metrics_button = QPushButton("Reset metrics")
        self.reset_metrics_button.clicked.connect(self.reset_default_metrics)

        self.metric_list = QListWidget()
        self.metric_list.itemChanged.connect(self._metric_check_changed)
        self.metric_list.setMinimumHeight(82)

        metric_layout.addWidget(QLabel("Available metrics"), 0, 0)
        metric_layout.addWidget(self.metric_combo, 1, 0)
        metric_layout.addWidget(self.add_metric_button, 1, 1)
        metric_layout.addWidget(QLabel("Displayed metrics"), 0, 2)
        metric_layout.addWidget(self.metric_list, 1, 2, 4, 1)
        metric_layout.addWidget(self.remove_metric_button, 2, 0, 1, 2)
        metric_layout.addWidget(self.clear_metrics_button, 3, 0, 1, 2)
        metric_layout.addWidget(self.reset_metrics_button, 4, 0, 1, 2)
        settings_layout.addLayout(metric_layout)

        controls = QHBoxLayout()
        self.pause_button = QPushButton("Pause graph")
        self.pause_button.setCheckable(True)
        self.pause_button.toggled.connect(self._set_paused)
        self.clear_visual_button = QPushButton("Clear visual view")
        self.clear_visual_button.clicked.connect(self.clear_visual_view)
        self.new_session_button = QPushButton("Start new graph session")
        self.new_session_button.clicked.connect(self.start_new_graph_session)
        self.return_live_button = QPushButton("Return to live")
        self.return_live_button.clicked.connect(self.return_to_live)
        self.legend_checkbox = QCheckBox("Legend")
        self.legend_checkbox.setChecked(True)
        self.legend_checkbox.toggled.connect(self._toggle_legend)
        for widget in (
            self.pause_button,
            self.clear_visual_button,
            self.new_session_button,
            self.return_live_button,
            self.legend_checkbox,
        ):
            controls.addWidget(widget)
        settings_layout.addLayout(controls)

        axis_layout = QFormLayout()
        self.x_mode_combo = QComboBox()
        for mode, label in X_AXIS_MODES.items():
            self.x_mode_combo.addItem(label, mode)
        self.x_mode_combo.setCurrentIndex(self.x_mode_combo.findData("follow_live"))
        self.x_mode_combo.currentIndexChanged.connect(self.refresh_plot)
        self.recent_window_seconds = QSpinBox()
        self.recent_window_seconds.setRange(1, 3600)
        self.recent_window_seconds.setValue(30)
        self.recent_window_seconds.valueChanged.connect(self.refresh_plot)
        self.x_min = QDoubleSpinBox()
        self.x_min.setRange(0.0, 86400.0)
        self.x_min.setDecimals(3)
        self.x_max = QDoubleSpinBox()
        self.x_max.setRange(0.001, 86400.0)
        self.x_max.setDecimals(3)
        self.x_max.setValue(10.0)
        self.x_min.valueChanged.connect(self.refresh_plot)
        self.x_max.valueChanged.connect(self.refresh_plot)
        self.reset_x_button = QPushButton("Reset X axis")
        self.reset_x_button.clicked.connect(self.reset_x_axis)

        self.y_mode_combo = QComboBox()
        for mode, label in Y_AXIS_MODES.items():
            self.y_mode_combo.addItem(label, mode)
        self.y_mode_combo.setCurrentIndex(self.y_mode_combo.findData("metric_default"))
        self.y_mode_combo.currentIndexChanged.connect(self.refresh_plot)
        self.include_zero_checkbox = QCheckBox("Include zero")
        self.include_zero_checkbox.setChecked(True)
        self.include_zero_checkbox.toggled.connect(self.refresh_plot)
        self.y_min = QDoubleSpinBox()
        self.y_min.setRange(-100000.0, 100000.0)
        self.y_min.setDecimals(3)
        self.y_max = QDoubleSpinBox()
        self.y_max.setRange(-100000.0, 100000.0)
        self.y_max.setDecimals(3)
        self.y_max.setValue(300.0)
        self.y_min.valueChanged.connect(self.refresh_plot)
        self.y_max.valueChanged.connect(self.refresh_plot)
        self.reset_y_button = QPushButton("Reset Y axis")
        self.reset_y_button.clicked.connect(self.reset_y_axis)

        axis_layout.addRow("X axis", self.x_mode_combo)
        axis_layout.addRow("Recent X window", self.recent_window_seconds)
        axis_layout.addRow("Manual X min", self.x_min)
        axis_layout.addRow("Manual X max", self.x_max)
        axis_layout.addRow(self.reset_x_button)
        axis_layout.addRow("Y axis", self.y_mode_combo)
        axis_layout.addRow(self.include_zero_checkbox)
        axis_layout.addRow("Manual Y min", self.y_min)
        axis_layout.addRow("Manual Y max", self.y_max)
        axis_layout.addRow(self.reset_y_button)
        settings_layout.addLayout(axis_layout)
        layout.addWidget(self.settings_container)

        self.error_label = QLabel("")
        self.error_label.setWordWrap(True)
        self.error_label.setStyleSheet("color: #ff6b6b;")
        self.stats_label = QLabel("Samples: 0 | Render: -- ms | Latency: -- ms")
        self.stats_label.setWordWrap(True)
        layout.addWidget(self.error_label)
        layout.addWidget(self.stats_label)

        if pg is None:
            self.plot_widget = None
            layout.addWidget(QLabel("pyqtgraph is not available."))
        else:
            self.plot_widget = pg.PlotWidget()
            self.plot_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
            self.plot_widget.setMinimumSize(280, 180)
            self.plot_widget.setLabel("bottom", "Lap distance", units="m")
            self.plot_widget.showGrid(x=True, y=True, alpha=0.25)
            self.legend = self.plot_widget.addLegend(offset=(10, 10))
            layout.addWidget(self.plot_widget, stretch=1)

    def add_sample(self, sample: TelemetrySample) -> None:
        if self.first_x_value is None:
            self.first_x_value = self._sample_absolute_x(sample)
        self.samples.append(sample)
        self._received_monotonic.append(time.monotonic())
        self.latest_sample_x = self._sample_x(sample)
        self.diagnostics.telemetry_samples += 1

    def add_selected_metric(self) -> bool:
        return self.add_metric(str(self.metric_combo.currentData()))

    def add_metric(self, metric: str) -> bool:
        self.error_label.setText("")
        if metric in self.curves:
            self.error_label.setText(f"{METRICS.get(metric, metric)} is already displayed.")
            return False
        if not self._metric_is_compatible(metric):
            self.error_label.setText("Use another graph panel for metrics with different units.")
            return False

        self.metric_visible[metric] = True
        if self.plot_widget is None:
            self.curves[metric] = None
        else:
            pen = pg.mkPen(PENS[len(self.curves) % len(PENS)], width=2)
            curve = self.plot_widget.plot(
                pen=pen,
                name=METRICS.get(metric, metric),
            )
            curve.setClipToView(True)
            curve.setDownsampling(auto=False, method="peak")
            self.curves[metric] = curve
        item = QListWidgetItem(METRICS.get(metric, metric))
        item.setData(Qt.ItemDataRole.UserRole, metric)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(Qt.CheckState.Checked)
        self.metric_list.addItem(item)
        self._apply_axis_labels()
        self.refresh_plot()
        return True

    def remove_selected_metric(self) -> None:
        item = self.metric_list.currentItem()
        if item is not None:
            self.remove_metric(str(item.data(Qt.ItemDataRole.UserRole)))

    def remove_metric(self, metric: str) -> bool:
        if metric not in self.curves:
            return False
        curve = self.curves.pop(metric)
        self.metric_visible.pop(metric, None)
        if self.plot_widget is not None and curve is not None:
            self.plot_widget.removeItem(curve)
            if self.legend is not None:
                self.legend.removeItem(METRICS.get(metric, metric))
        for row in range(self.metric_list.count()):
            item = self.metric_list.item(row)
            if item.data(Qt.ItemDataRole.UserRole) == metric:
                self.metric_list.takeItem(row)
                break
        self._apply_axis_labels()
        self.refresh_plot()
        return True

    def clear_metrics(self) -> None:
        for metric in list(self.curves):
            self.remove_metric(metric)

    def reset_default_metrics(self) -> None:
        self.clear_metrics()
        self.add_metric("speed_kmh")

    def selected_metrics(self) -> list[str]:
        return list(self.curves)

    def set_selected_metrics(self, metrics: list[str]) -> None:
        self.clear_metrics()
        for metric in metrics:
            self.add_metric(metric)

    def clear_visual_view(self) -> None:
        if self.plot_widget is None:
            return
        for curve in self.curves.values():
            if curve is not None:
                curve.setData([], [])
        self.latest_displayed_x = None

    def start_new_graph_session(self) -> None:
        self.samples.clear()
        self._received_monotonic.clear()
        self.first_x_value = None
        self.latest_sample_x = None
        self.latest_displayed_x = None
        self.diagnostics = GraphDiagnostics()
        self.clear_visual_view()
        self.reset_x_axis()

    def return_to_live(self) -> None:
        self.x_mode_combo.setCurrentIndex(self.x_mode_combo.findData("follow_live"))
        self.refresh_plot()

    def refresh_plot(self) -> None:
        if self.paused or self.plot_widget is None:
            self._update_stats_label()
            return

        started = time.perf_counter()
        self._apply_axis_labels()
        visible_x, visible_indices = self._visible_x_values()
        if visible_x.size == 0:
            self._apply_x_range()
            self._apply_y_range([])
            self._update_stats_label()
            return

        displayed_metrics = [metric for metric, visible in self.metric_visible.items() if visible]
        rendered_points = 0
        y_arrays_for_range: list[np.ndarray] = []
        for metric, curve in self.curves.items():
            if curve is None:
                continue
            if not self.metric_visible.get(metric, True):
                curve.setData([], [])
                continue
            y_values = np.array(
                [sample_metric_value(self.samples[index], metric) for index in visible_indices],
                dtype=float,
            )
            mask = np.isfinite(y_values)
            x_values = visible_x[mask]
            y_values = y_values[mask]
            display_x, display_y = downsample_xy(x_values, y_values, MAX_RENDER_POINTS)
            curve.setData(display_x, display_y)
            rendered_points += int(display_x.size)
            if display_y.size:
                y_arrays_for_range.append(display_y)

        self._apply_x_range()
        self._apply_y_range(displayed_metrics, y_arrays_for_range)
        self.latest_displayed_x = float(visible_x[-1])
        if self._received_monotonic:
            self.diagnostics.latest_latency_ms = (time.monotonic() - self._received_monotonic[-1]) * 1000.0
        self.diagnostics.rendered_frames += 1
        self.diagnostics.visible_samples = int(visible_x.size)
        self.diagnostics.rendered_points = rendered_points
        self.diagnostics.last_render_ms = (time.perf_counter() - started) * 1000.0
        self._update_stats_label()

    def raw_sample_count(self) -> int:
        return len(self.samples)

    def replace_samples(self, samples: list[TelemetrySample]) -> None:
        self.samples = list(samples)
        self._received_monotonic = [time.monotonic()] * len(self.samples)
        self.first_x_value = None
        self.latest_displayed_x = None
        self.latest_sample_x = None
        self.refresh_plot()

    def visible_sample_count(self) -> int:
        return self.diagnostics.visible_samples

    def _visible_x_values(self) -> tuple[np.ndarray, np.ndarray]:
        if not self.samples:
            return np.array([], dtype=float), np.array([], dtype=int)

        all_x = np.array([self._sample_x(sample) for sample in self.samples], dtype=float)
        indices = np.arange(all_x.size)
        mode = self.x_mode_combo.currentData()
        if mode in {"follow_live", "recent_window"}:
            latest = float(all_x[-1])
            minimum = max(0.0, latest - float(self.recent_window_seconds.value()))
            mask = all_x >= minimum
            return all_x[mask], indices[mask]
        if mode == "manual":
            minimum, maximum = self.manual_x_range()
            if minimum is None or maximum is None:
                return all_x, indices
            mask = (all_x >= minimum) & (all_x <= maximum)
            return all_x[mask], indices[mask]
        return all_x, indices

    def manual_x_range(self) -> tuple[float | None, float | None]:
        minimum = self.x_min.value()
        maximum = self.x_max.value()
        if minimum >= maximum:
            self.error_label.setText("Manual X minimum must be smaller than maximum.")
            return None, None
        self.error_label.setText("")
        return minimum, maximum

    def manual_y_range(self) -> tuple[float | None, float | None]:
        minimum = self.y_min.value()
        maximum = self.y_max.value()
        if minimum >= maximum:
            self.error_label.setText("Manual Y minimum must be smaller than maximum.")
            return None, None
        self.error_label.setText("")
        return minimum, maximum

    def reset_x_axis(self) -> None:
        self.x_min.setValue(0.0)
        self.x_max.setValue(10.0)
        self.x_mode_combo.setCurrentIndex(self.x_mode_combo.findData("follow_live"))
        if self.plot_widget is not None:
            self.plot_widget.setXRange(0.0, 10.0, padding=0.0)

    def reset_y_axis(self) -> None:
        self.y_min.setValue(0.0)
        self.y_max.setValue(300.0)
        self.y_mode_combo.setCurrentIndex(self.y_mode_combo.findData("metric_default"))
        self.refresh_plot()

    def _apply_x_range(self) -> None:
        if self.plot_widget is None:
            return
        mode = self.x_mode_combo.currentData()
        if not self.samples:
            self.plot_widget.setXRange(0.0, 10.0, padding=0.0)
            return
        latest = self._sample_x(self.samples[-1])
        if mode == "full_session":
            self.plot_widget.setXRange(0.0, max(10.0, latest), padding=0.0)
        elif mode == "follow_live":
            window = float(self.recent_window_seconds.value())
            self.plot_widget.setXRange(max(0.0, latest - window), max(window, latest), padding=0.0)
        elif mode == "recent_window":
            window = float(self.recent_window_seconds.value())
            self.plot_widget.setXRange(max(0.0, latest - window), latest, padding=0.0)
        elif mode == "manual":
            minimum, maximum = self.manual_x_range()
            if minimum is not None and maximum is not None:
                self.plot_widget.setXRange(minimum, maximum, padding=0.0)

    def _apply_y_range(self, metrics: list[str] | None = None, y_arrays: list[np.ndarray] | None = None) -> None:
        if self.plot_widget is None:
            return
        metrics = metrics if metrics is not None else [m for m, visible in self.metric_visible.items() if visible]
        y_arrays = y_arrays or []
        mode = self.y_mode_combo.currentData()
        if mode == "manual":
            minimum, maximum = self.manual_y_range()
            if minimum is not None and maximum is not None:
                self.plot_widget.setYRange(minimum, maximum, padding=0.0)
            return
        if mode == "metric_default" and metrics:
            minimum, maximum = combined_metric_default_range(metrics)
            self.plot_widget.setYRange(minimum, maximum, padding=0.0)
            return
        minimum, maximum = auto_y_range(metrics, y_arrays, self.include_zero_checkbox.isChecked())
        self.plot_widget.setYRange(minimum, maximum, padding=0.0)

    def _apply_axis_labels(self) -> None:
        if self.plot_widget is None:
            return
        metrics = self.selected_metrics()
        if len(metrics) == 1:
            label = f"{METRICS.get(metrics[0], metrics[0])} ({METRIC_UNITS.get(metrics[0], '')})"
        elif metrics:
            group = METRIC_GROUPS.get(metrics[0], "value")
            label = group.title()
        else:
            label = "Value"
        self.plot_widget.setLabel("left", label)
        if self._uses_distance_axis():
            self.plot_widget.setLabel("bottom", "Lap distance", units="m")
        else:
            self.plot_widget.setLabel("bottom", "Session time", units="s")

    def _metric_check_changed(self, item: QListWidgetItem) -> None:
        metric = str(item.data(Qt.ItemDataRole.UserRole))
        self.metric_visible[metric] = item.checkState() == Qt.CheckState.Checked
        self.refresh_plot()

    def _metric_is_compatible(self, metric: str) -> bool:
        if not self.curves:
            return True
        first_metric = next(iter(self.curves))
        return METRIC_GROUPS.get(first_metric) == METRIC_GROUPS.get(metric)

    def _set_paused(self, paused: bool) -> None:
        self.paused = paused
        for button in (self.pause_button, self.toolbar_pause_button):
            if button.isChecked() != paused:
                button.blockSignals(True)
                button.setChecked(paused)
                button.blockSignals(False)
        self.pause_button.setText("Resume graph" if paused else "Pause graph")
        self.toolbar_pause_button.setText("Resume" if paused else "Pause")
        if not paused:
            self.refresh_plot()

    def _toggle_legend(self, visible: bool) -> None:
        if self.plot_widget is not None and self.legend is not None:
            self.legend.setVisible(visible)

    def _sample_absolute_x(self, sample: TelemetrySample) -> float:
        if sample.lap_distance is not None:
            return max(0.0, float(sample.lap_distance))
        if sample.session_time is not None:
            return max(0.0, float(sample.session_time))
        return max(0.0, float(sample.timestamp))

    def _sample_x(self, sample: TelemetrySample) -> float:
        absolute = self._sample_absolute_x(sample)
        if sample.lap_distance is not None:
            return absolute
        if self.first_x_value is None:
            return 0.0
        return max(0.0, absolute - self.first_x_value)

    def _uses_distance_axis(self) -> bool:
        return any(sample.lap_distance is not None for sample in self.samples)

    def _update_stats_label(self) -> None:
        latency = "--" if self.diagnostics.latest_latency_ms == 0.0 else f"{self.diagnostics.latest_latency_ms:.1f}"
        self.stats_label.setText(
            "Samples: "
            f"{len(self.samples)} | Visible: {self.diagnostics.visible_samples} | "
            f"Rendered points: {self.diagnostics.rendered_points} | "
            f"Render: {self.diagnostics.last_render_ms:.2f} ms | Latency: {latency} ms"
        )

    def settings_state(self) -> dict:
        return {
            "metrics": self.selected_metrics(),
            "visible": dict(self.metric_visible),
            "x_mode": self.x_mode_combo.currentData(),
            "recent_window": self.recent_window_seconds.value(),
            "manual_x": [self.x_min.value(), self.x_max.value()],
            "y_mode": self.y_mode_combo.currentData(),
            "manual_y": [self.y_min.value(), self.y_max.value()],
            "include_zero": self.include_zero_checkbox.isChecked(),
            "legend": self.legend_checkbox.isChecked(),
            "settings_hidden": self.settings_toggle_button.isChecked(),
            "compact": bool(self.property("compact_mode")),
        }

    def restore_settings_state(self, state: dict) -> None:
        metrics = [metric for metric in state.get("metrics", []) if metric in METRICS]
        if metrics:
            self.set_selected_metrics(metrics)
        for metric, visible in state.get("visible", {}).items():
            if metric in self.metric_visible:
                self.metric_visible[metric] = bool(visible)
                for row in range(self.metric_list.count()):
                    item = self.metric_list.item(row)
                    if item.data(Qt.ItemDataRole.UserRole) == metric:
                        item.setCheckState(Qt.CheckState.Checked if visible else Qt.CheckState.Unchecked)
        self._set_combo_data(self.x_mode_combo, state.get("x_mode"), "follow_live")
        self.recent_window_seconds.setValue(int(state.get("recent_window", 30)))
        manual_x = state.get("manual_x", [0.0, 10.0])
        if len(manual_x) == 2 and float(manual_x[0]) < float(manual_x[1]):
            self.x_min.setValue(float(manual_x[0]))
            self.x_max.setValue(float(manual_x[1]))
        self._set_combo_data(self.y_mode_combo, state.get("y_mode"), "metric_default")
        manual_y = state.get("manual_y", [0.0, 300.0])
        if len(manual_y) == 2 and float(manual_y[0]) < float(manual_y[1]):
            self.y_min.setValue(float(manual_y[0]))
            self.y_max.setValue(float(manual_y[1]))
        self.include_zero_checkbox.setChecked(bool(state.get("include_zero", True)))
        self.legend_checkbox.setChecked(bool(state.get("legend", True)))
        self.set_settings_hidden(bool(state.get("settings_hidden", False)))
        self.settings_toggle_button.setChecked(bool(state.get("settings_hidden", False)))
        if "compact" in state:
            self.set_compact_mode(bool(state.get("compact")))

    @staticmethod
    def _set_combo_data(combo: QComboBox, value, default) -> None:
        index = combo.findData(value)
        if index < 0:
            index = combo.findData(default)
        if index >= 0:
            combo.setCurrentIndex(index)

    def set_settings_hidden(self, hidden: bool) -> None:
        self.settings_container.setVisible(not hidden)
        self.settings_toggle_button.setText("Show settings" if hidden else "Hide graph settings")
        if self.plot_widget is not None:
            self.plot_widget.updateGeometry()

    def set_compact_mode(self, compact: bool) -> None:
        self.setProperty("compact_mode", compact)
        layout = self.layout()
        if layout is not None:
            margin = 2 if compact else 6
            layout.setContentsMargins(margin, margin, margin, margin)
            layout.setSpacing(2 if compact else 6)
        if compact:
            self.settings_toggle_button.setChecked(True)
            self.legend_checkbox.setChecked(False)
        self.stats_label.setVisible(not compact)
        self.error_label.setVisible(not compact)
        for widget in (
            self.export_image_button,
            self.pause_button,
            self.clear_visual_button,
            self.new_session_button,
            self.return_live_button,
        ):
            widget.setVisible(not compact)
        if self.plot_widget is not None:
            self.plot_widget.setMinimumSize(96 if compact else 280, 72 if compact else 180)

    def reset_view(self) -> None:
        self.reset_x_axis()
        self.reset_y_axis()
        self.refresh_plot()

    def export_image(self) -> None:
        self.error_label.setText("Use the operating system screenshot tools or export data from a saved lap/session.")


def combined_metric_default_range(metrics: list[str]) -> tuple[float, float]:
    ranges = [METRIC_DEFAULT_RANGES.get(metric, (0.0, 100.0)) for metric in metrics]
    return min(item[0] for item in ranges), max(item[1] for item in ranges)


def auto_y_range(metrics: list[str], y_arrays: list[np.ndarray], include_zero: bool) -> tuple[float, float]:
    if not y_arrays:
        return combined_metric_default_range(metrics) if metrics else (0.0, 100.0)
    values = np.concatenate([array for array in y_arrays if array.size])
    if values.size == 0:
        return combined_metric_default_range(metrics) if metrics else (0.0, 100.0)
    minimum = float(np.nanmin(values))
    maximum = float(np.nanmax(values))
    if include_zero or any(metric in {"speed_kmh", "rpm", "throttle_percent", "brake_percent", "clutch_percent"} for metric in metrics):
        minimum = min(0.0, minimum)
    if all(metric in {"throttle_percent", "brake_percent", "clutch_percent"} for metric in metrics):
        return 0.0, 100.0
    if all(metric in {"speed_kmh", "rpm", "gear"} for metric in metrics):
        minimum = max(0.0 if "gear" not in metrics else -1.0, minimum)
    if minimum == maximum:
        maximum = minimum + 1.0
    padding = max((maximum - minimum) * 0.08, 1.0)
    return minimum, maximum + padding


def downsample_xy(x: np.ndarray, y: np.ndarray, max_points: int) -> tuple[np.ndarray, np.ndarray]:
    if x.size <= max_points or max_points < 4:
        return x, y
    bucket_count = max_points // 2
    edges = np.linspace(0, x.size, bucket_count + 1, dtype=int)
    out_x = []
    out_y = []
    for start, end in zip(edges[:-1], edges[1:]):
        if end <= start:
            continue
        segment_y = y[start:end]
        segment_x = x[start:end]
        min_index = int(np.argmin(segment_y))
        max_index = int(np.argmax(segment_y))
        for index in sorted({min_index, max_index}):
            out_x.append(float(segment_x[index]))
            out_y.append(float(segment_y[index]))
    return np.array(out_x, dtype=float), np.array(out_y, dtype=float)
