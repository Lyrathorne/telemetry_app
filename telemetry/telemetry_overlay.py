from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from models import LapResult, ReferenceLap, TelemetryPoint
from telemetry.telemetry_points import lap_to_points, point_axis, point_metric


@dataclass(slots=True)
class OverlaySeries:
    metric: str
    axis: list[float]
    main: list[float]
    comparison: list[float]


def build_lap_overlay(
    main_lap: LapResult,
    comparison: LapResult | ReferenceLap,
    metrics: list[str] | None = None,
    points: int = 500,
) -> list[OverlaySeries]:
    metrics = metrics or ["speed_kmh", "throttle_percent", "brake_percent", "steering"]
    main_points = lap_to_points(main_lap)
    comparison_points = comparison.telemetry_points if isinstance(comparison, ReferenceLap) else lap_to_points(comparison)
    return build_point_overlay(main_points, comparison_points, metrics, points)


def build_point_overlay(
    main_points: list[TelemetryPoint],
    comparison_points: list[TelemetryPoint],
    metrics: list[str],
    points: int = 500,
) -> list[OverlaySeries]:
    grid = common_axis_grid([main_points, comparison_points], points)
    if grid.size == 0:
        return []
    series: list[OverlaySeries] = []
    for metric in metrics:
        main_values = aligned_metric(main_points, metric, grid)
        comparison_values = aligned_metric(comparison_points, metric, grid)
        if main_values.size == 0 or comparison_values.size == 0:
            continue
        series.append(
            OverlaySeries(
                metric=metric,
                axis=grid.tolist(),
                main=main_values.tolist(),
                comparison=comparison_values.tolist(),
            )
        )
    return series


def common_axis_grid(point_sets: list[list[TelemetryPoint]], points: int) -> np.ndarray:
    maxima = []
    for point_set in point_sets:
        axes = normalized_axes(point_set)
        if axes:
            maxima.append(max(axes))
    if not maxima:
        return np.array([])
    end = min(maxima)
    if end <= 0:
        return np.array([])
    return np.linspace(0.0, end, points)


def aligned_metric(points: list[TelemetryPoint], metric: str, grid: np.ndarray) -> np.ndarray:
    axes = normalized_axes(points)
    clean = [
        (axis, point_metric(point, metric))
        for axis, point in zip(axes, points)
    ]
    clean = [(axis, value) for axis, value in clean if value is not None]
    if len(clean) < 2:
        return np.array([])
    x = np.array([item[0] for item in clean], dtype=float)
    y = np.array([item[1] for item in clean], dtype=float)
    x, unique = np.unique(x, return_index=True)
    y = y[unique]
    if x.size < 2:
        return np.array([])
    return np.interp(grid, x, y)


def normalized_axes(points: list[TelemetryPoint]) -> list[float]:
    raw = [point_axis(point) for point in points]
    base = next((axis for axis in raw if axis is not None), None)
    if base is None:
        return []
    return [max(0.0, float(axis) - float(base)) if axis is not None else 0.0 for axis in raw]
