from __future__ import annotations

import numpy as np
from PySide6.QtWidgets import QLabel, QSizePolicy, QVBoxLayout, QWidget

from models import TelemetrySample

try:
    import pyqtgraph as pg
except ImportError:  # pragma: no cover
    pg = None


MAX_MAP_POINTS = 5000


class TrackMapPanel(QWidget):
    def __init__(self, title: str = "Track map", parent=None, live_updates: bool = True) -> None:
        super().__init__(parent)
        self.title = title
        self.live_updates = live_updates
        self.samples: list[TelemetrySample] = []
        self.rendered_points = 0

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        self.status_label = QLabel("Waiting for car position")
        layout.addWidget(self.status_label)

        if pg is None:
            self.plot_widget = None
            self.path_curve = None
            self.latest_point = None
            layout.addWidget(QLabel("pyqtgraph is not available."))
        else:
            self.plot_widget = pg.PlotWidget()
            self.plot_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
            self.plot_widget.setMinimumSize(220, 180)
            self.plot_widget.setLabel("bottom", "World X", units="m")
            self.plot_widget.setLabel("left", "World Z", units="m")
            self.plot_widget.showGrid(x=True, y=True, alpha=0.2)
            self.plot_widget.getViewBox().setAspectLocked(True)
            self.path_curve = self.plot_widget.plot(pen=pg.mkPen("#45aaf2", width=2))
            self.latest_point = pg.ScatterPlotItem(size=8, brush=pg.mkBrush("#ffffff"), pen=pg.mkPen("#45aaf2", width=1))
            self.plot_widget.addItem(self.latest_point)
            layout.addWidget(self.plot_widget, stretch=1)

    def add_sample(self, sample: TelemetrySample) -> None:
        self.samples.append(sample)
        self.refresh_plot()

    def replace_samples(self, samples: list[TelemetrySample]) -> None:
        self.samples = list(samples)
        self.refresh_plot()

    def refresh_plot(self) -> None:
        if self.plot_widget is None or self.path_curve is None or self.latest_point is None:
            return
        points = trajectory_points(self.samples)
        if points.size == 0:
            self.path_curve.setData([], [])
            self.latest_point.setData([], [])
            self.rendered_points = 0
            self.status_label.setText("Waiting for car position")
            return
        points = downsample_points(points, MAX_MAP_POINTS)
        x = points[:, 0]
        z = points[:, 1]
        self.path_curve.setData(x, z)
        self.latest_point.setData([float(x[-1])], [float(z[-1])])
        self.rendered_points = int(points.shape[0])
        self.status_label.setText(f"Trajectory points: {self.rendered_points}")


def trajectory_points(samples: list[TelemetrySample]) -> np.ndarray:
    points = [
        (float(sample.world_position_x), float(sample.world_position_z))
        for sample in samples
        if sample.world_position_x is not None and sample.world_position_z is not None
    ]
    if not points:
        return np.empty((0, 2), dtype=float)
    return np.array(points, dtype=float)


def downsample_points(points: np.ndarray, max_points: int) -> np.ndarray:
    if points.shape[0] <= max_points:
        return points
    indexes = np.linspace(0, points.shape[0] - 1, max_points, dtype=int)
    return points[indexes]
