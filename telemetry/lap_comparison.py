from __future__ import annotations

import numpy as np

from models import LapResult, TelemetrySample, sample_metric_value


def assert_laps_comparable(laps: list[LapResult], allow_track_mismatch: bool = False) -> None:
    tracks = {lap.track for lap in laps if lap.track}
    if len(tracks) > 1 and not allow_track_mismatch:
        raise ValueError("Laps from different tracks cannot be compared silently.")


def lap_position(sample: TelemetrySample) -> float | None:
    if sample.lap_distance is not None:
        return float(sample.lap_distance)
    if sample.normalized_track_position is not None:
        return float(sample.normalized_track_position)
    return None


def lap_positions(lap: LapResult) -> list[float | None]:
    positions = [lap_position(sample) for sample in lap.samples]
    base = next((position for position in positions if position is not None), None)
    if base is None:
        return positions
    return [None if position is None else max(0.0, float(position) - float(base)) for position in positions]


def aligned_metric(lap: LapResult, metric: str, grid: np.ndarray) -> np.ndarray:
    positions = lap_positions(lap)
    pairs = [
        (position, sample_metric_value(sample, metric))
        for position, sample in zip(positions, lap.samples)
    ]
    clean = [(x, y) for x, y in pairs if x is not None and y is not None]
    if len(clean) < 2:
        return np.array([])
    x = np.array([item[0] for item in clean], dtype=float)
    y = np.array([item[1] for item in clean], dtype=float)
    x, unique_indices = np.unique(x, return_index=True)
    y = y[unique_indices]
    if x.size < 2:
        return np.array([])
    return np.interp(grid, x, y)


def common_position_grid(laps: list[LapResult], points: int = 500) -> np.ndarray:
    maxima = []
    for lap in laps:
        positions = lap_positions(lap)
        positions = [position for position in positions if position is not None]
        if positions:
            maxima.append(max(positions))
    if not maxima:
        return np.array([])
    end = min(maxima)
    if end <= 0:
        return np.array([])
    return np.linspace(0.0, end, points)


def elapsed_lap_times(lap: LapResult, grid: np.ndarray) -> np.ndarray:
    pairs = []
    positions = lap_positions(lap)
    for position, sample in zip(positions, lap.samples):
        lap_time = sample.lap_time
        if lap_time is None and sample.current_lap_time_ms is not None:
            lap_time = sample.current_lap_time_ms / 1000.0
        if position is not None and lap_time is not None:
            pairs.append((position, float(lap_time)))
    if len(pairs) < 2:
        return np.array([])
    x = np.array([item[0] for item in pairs], dtype=float)
    y = np.array([item[1] for item in pairs], dtype=float)
    x, unique_indices = np.unique(x, return_index=True)
    y = y[unique_indices]
    return np.interp(grid, x, y)


def time_delta(reference: LapResult, comparison: LapResult, points: int = 500) -> tuple[np.ndarray, np.ndarray]:
    grid = common_position_grid([reference, comparison], points)
    if grid.size == 0:
        return grid, np.array([])
    reference_time = elapsed_lap_times(reference, grid)
    comparison_time = elapsed_lap_times(comparison, grid)
    if reference_time.size == 0 or comparison_time.size == 0:
        return grid, np.array([])
    return grid, comparison_time - reference_time


def sector_marker_positions(lap: LapResult) -> list[float]:
    if not lap.samples:
        return []
    base = next((lap_position(sample) for sample in lap.samples if lap_position(sample) is not None), 0.0) or 0.0
    positions = []
    for sector in lap.sectors:
        if sector.end_distance_m is not None:
            positions.append(max(0.0, float(sector.end_distance_m) - float(base)))
    return positions
