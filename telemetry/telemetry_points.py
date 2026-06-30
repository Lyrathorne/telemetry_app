from __future__ import annotations

from models import LapResult, TelemetryPoint, TelemetrySample


def sample_to_point(sample: TelemetrySample, first_timestamp: float | None = None) -> TelemetryPoint:
    timestamp = float(sample.timestamp)
    if first_timestamp is not None:
        timestamp = max(0.0, timestamp - float(first_timestamp))
    return TelemetryPoint(
        timestamp=timestamp,
        lap_time=sample.current_lap_time_ms / 1000.0 if sample.current_lap_time_ms is not None else sample.lap_time,
        lap_progress=sample.normalized_track_position,
        distance=sample.lap_distance,
        speed_kmh=sample.speed_kmh,
        throttle_percent=sample.throttle_percent,
        brake_percent=sample.brake_percent,
        steering=sample.steering,
        gear=sample.gear,
        rpm=sample.rpm,
    )


def lap_to_points(lap: LapResult) -> list[TelemetryPoint]:
    first_timestamp = lap.samples[0].timestamp if lap.samples else None
    return [sample_to_point(sample, first_timestamp) for sample in lap.samples]


def point_axis(point: TelemetryPoint) -> float | None:
    if point.distance is not None:
        return float(point.distance)
    if point.lap_progress is not None:
        return float(point.lap_progress)
    return None


def point_metric(point: TelemetryPoint, metric: str) -> float | None:
    value = getattr(point, metric, None)
    return None if value is None else float(value)
