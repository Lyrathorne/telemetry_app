from __future__ import annotations

from collections import deque

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from models import METRICS, TelemetrySample, sample_metric_value

try:
    import pyqtgraph as pg
except ImportError:  # pragma: no cover
    pg = None


PENS = ["#1f77b4", "#d62728", "#2ca02c", "#ff7f0e", "#9467bd", "#17becf", "#8c564b"]


class GraphPanel(QWidget):
    def __init__(self, title: str, refresh_ms: int, history_limit: int, parent=None) -> None:
        super().__init__(parent)
        self.title = title
        self.history_limit = history_limit
        self.samples: deque[TelemetrySample] = deque(maxlen=history_limit)
        self.curves = {}
        self.paused = False

        layout = QVBoxLayout(self)
        controls = QHBoxLayout()

        self.metric_combo = QComboBox()
        for key, label in METRICS.items():
            self.metric_combo.addItem(label, key)
        self.metric_combo.setCurrentIndex(0)
        self.metric_combo.currentIndexChanged.connect(self._ensure_metric_curve)

        self.add_metric_button = QPushButton("Add metric")
        self.add_metric_button.clicked.connect(self._ensure_metric_curve)

        self.pause_button = QPushButton("Pause")
        self.pause_button.setCheckable(True)
        self.pause_button.toggled.connect(self._set_paused)

        self.clear_button = QPushButton("Clear")
        self.clear_button.clicked.connect(self.clear_history)

        self.legend_checkbox = QCheckBox("Legend")
        self.legend_checkbox.setChecked(True)
        self.legend_checkbox.toggled.connect(self._toggle_legend)

        self.auto_y_checkbox = QCheckBox("Auto Y")
        self.auto_y_checkbox.setChecked(True)
        self.auto_y_checkbox.toggled.connect(self._apply_y_range)

        self.y_min = QDoubleSpinBox()
        self.y_min.setRange(-100000.0, 100000.0)
        self.y_min.setValue(0.0)
        self.y_max = QDoubleSpinBox()
        self.y_max.setRange(-100000.0, 100000.0)
        self.y_max.setValue(300.0)
        self.y_min.valueChanged.connect(self._apply_y_range)
        self.y_max.valueChanged.connect(self._apply_y_range)

        self.history_spin = QSpinBox()
        self.history_spin.setRange(100, 50000)
        self.history_spin.setValue(history_limit)
        self.history_spin.valueChanged.connect(self._set_history_limit)

        for widget in (
            QLabel("Metric"),
            self.metric_combo,
            self.add_metric_button,
            self.pause_button,
            self.clear_button,
            self.legend_checkbox,
            self.auto_y_checkbox,
        ):
            controls.addWidget(widget)

        layout.addLayout(controls)
        range_layout = QFormLayout()
        range_layout.addRow("Fixed Y min", self.y_min)
        range_layout.addRow("Fixed Y max", self.y_max)
        range_layout.addRow("History", self.history_spin)
        layout.addLayout(range_layout)

        if pg is None:
            self.plot_widget = None
            layout.addWidget(QLabel("pyqtgraph is not available."))
        else:
            self.plot_widget = pg.PlotWidget()
            self.plot_widget.setLabel("bottom", "Elapsed samples")
            self.plot_widget.addLegend()
            layout.addWidget(self.plot_widget)

        self.refresh_timer = QTimer(self)
        self.refresh_timer.setInterval(refresh_ms)
        self.refresh_timer.timeout.connect(self.refresh_plot)
        self.refresh_timer.start()
        self._ensure_metric_curve()

    def add_sample(self, sample: TelemetrySample) -> None:
        self.samples.append(sample)

    def clear_history(self) -> None:
        self.samples.clear()
        self.refresh_plot()

    def selected_metrics(self) -> list[str]:
        return list(self.curves)

    def set_selected_metrics(self, metrics: list[str]) -> None:
        self.curves.clear()
        if self.plot_widget is not None:
            self.plot_widget.clear()
            self.plot_widget.addLegend()
        for metric in metrics:
            self._add_curve(metric)

    def refresh_plot(self) -> None:
        if self.paused or self.plot_widget is None:
            return
        x = list(range(len(self.samples)))
        for metric, curve in self.curves.items():
            y = [sample_metric_value(sample, metric) for sample in self.samples]
            curve.setData(x, [value if value is not None else 0.0 for value in y])
        self._apply_y_range()

    def _ensure_metric_curve(self) -> None:
        metric = self.metric_combo.currentData()
        if metric not in self.curves:
            self._add_curve(metric)

    def _add_curve(self, metric: str) -> None:
        if self.plot_widget is None:
            self.curves[metric] = None
            return
        pen = pg.mkPen(PENS[len(self.curves) % len(PENS)], width=2)
        self.curves[metric] = self.plot_widget.plot(pen=pen, name=METRICS.get(metric, metric))

    def _set_paused(self, paused: bool) -> None:
        self.paused = paused
        self.pause_button.setText("Resume" if paused else "Pause")

    def _toggle_legend(self, visible: bool) -> None:
        if self.plot_widget is not None and self.plot_widget.plotItem.legend is not None:
            self.plot_widget.plotItem.legend.setVisible(visible)

    def _apply_y_range(self) -> None:
        if self.plot_widget is None:
            return
        if self.auto_y_checkbox.isChecked():
            self.plot_widget.enableAutoRange(axis="y", enable=True)
        else:
            self.plot_widget.enableAutoRange(axis="y", enable=False)
            self.plot_widget.setYRange(self.y_min.value(), self.y_max.value())

    def _set_history_limit(self, value: int) -> None:
        self.history_limit = value
        self.samples = deque(self.samples, maxlen=value)
