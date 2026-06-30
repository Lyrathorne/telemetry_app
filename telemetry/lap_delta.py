from __future__ import annotations

from bisect import bisect_left

from models import LapResult, TelemetrySample
from telemetry.timing_status import same_scope


def best_reference_lap(laps: list[LapResult], lap: LapResult | None = None) -> LapResult | None:
    candidates = [
        candidate
        for candidate in laps
        if candidate.complete
        and candidate.valid
        and candidate.lap_time_ms is not None
        and (lap is None or same_scope(candidate, lap))
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda candidate: int(candidate.lap_time_ms or 0))


def completed_lap_delta_ms(lap: LapResult, laps: list[LapResult]) -> int | None:
    reference = best_reference_lap(laps, lap)
    if reference is None or lap.lap_time_ms is None:
        return None
    return int(lap.lap_time_ms) - int(reference.lap_time_ms or 0)


def live_lap_delta_ms(current_lap: LapResult | None, laps: list[LapResult]) -> int | None:
    if current_lap is None or not current_lap.samples:
        return None
    sample = current_lap.samples[-1]
    reference = best_reference_lap(laps, current_lap)
    if reference is None or reference.lap_time_ms is None or sample.current_lap_time_ms is None:
        return None
    reference_elapsed_ms = reference_elapsed_at_sample(reference, sample)
    if reference_elapsed_ms is None:
        return int(sample.current_lap_time_ms) - int(reference.lap_time_ms)
    return int(sample.current_lap_time_ms) - int(reference_elapsed_ms)


def reference_elapsed_at_sample(reference: LapResult, sample: TelemetrySample) -> int | None:
    series = reference.telemetry_series
    if series is None or not series.elapsed_time_s:
        return None
    if sample.lap_distance is not None and any(value is not None for value in series.lap_distance_m):
        return interpolate_elapsed_ms(series.lap_distance_m, series.elapsed_time_s, float(sample.lap_distance))
    if sample.normalized_track_position is not None and any(value is not None for value in series.normalized_position):
        return interpolate_elapsed_ms(series.normalized_position, series.elapsed_time_s, float(sample.normalized_track_position))
    return None


def interpolate_elapsed_ms(axis_values: list[float | None], elapsed_s: list[float], target: float) -> int | None:
    points = [
        (float(axis), float(elapsed))
        for axis, elapsed in zip(axis_values, elapsed_s)
        if axis is not None
    ]
    if len(points) < 2:
        return None
    points.sort(key=lambda pair: pair[0])
    axes = [axis for axis, _elapsed in points]
    if target <= axes[0]:
        return int(points[0][1] * 1000)
    if target >= axes[-1]:
        return int(points[-1][1] * 1000)
    index = bisect_left(axes, target)
    left_axis, left_elapsed = points[index - 1]
    right_axis, right_elapsed = points[index]
    span = right_axis - left_axis
    if span <= 0:
        return int(right_elapsed * 1000)
    ratio = (target - left_axis) / span
    return int((left_elapsed + (right_elapsed - left_elapsed) * ratio) * 1000)


def format_delta_ms(delta_ms: int | None, best_label: bool = False) -> str:
    if delta_ms is None:
        return "Unavailable"
    if best_label and delta_ms == 0:
        return "Best"
    sign = "+" if delta_ms >= 0 else "-"
    return f"{sign}{abs(delta_ms) / 1000.0:.3f}"
