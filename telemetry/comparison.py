from __future__ import annotations

from dataclasses import dataclass

from models import TelemetrySample, TelemetrySession, sample_metric_value, sample_x_value


@dataclass(slots=True)
class ComparisonSeries:
    name: str
    x: list[float]
    y: list[float]


def preferred_axis(sessions: list[TelemetrySession]) -> str:
    if sessions and all(any(sample.lap_distance is not None for sample in session.samples) for session in sessions):
        return "lap_distance"
    if sessions and all(any(sample.lap_time is not None for sample in session.samples) for session in sessions):
        return "lap_time"
    return "elapsed_time"


def build_comparison_series(
    sessions: list[TelemetrySession],
    metric: str,
    axis: str | None = None,
    allow_track_mismatch: bool = False,
) -> list[ComparisonSeries]:
    if not sessions:
        return []
    if not allow_track_mismatch:
        tracks = {session.track for session in sessions if session.track}
        if len(tracks) > 1:
            raise ValueError("Selected sessions have different track metadata.")

    selected_axis = axis or preferred_axis(sessions)
    return [_series_for_session(session, metric, selected_axis) for session in sessions]


def _series_for_session(session: TelemetrySession, metric: str, axis: str) -> ComparisonSeries:
    if not session.samples:
        return ComparisonSeries(name=session.session_name, x=[], y=[])

    first_timestamp = session.samples[0].timestamp
    x_values: list[float] = []
    y_values: list[float] = []
    for sample in session.samples:
        x_value = sample_x_value(sample, axis, first_timestamp)
        y_value = sample_metric_value(sample, metric)
        if x_value is None or y_value is None:
            continue
        x_values.append(float(x_value))
        y_values.append(float(y_value))

    name_parts = [session.session_name]
    if session.driver_name:
        name_parts.append(session.driver_name)
    if session.lap_label:
        name_parts.append(session.lap_label)
    return ComparisonSeries(name=" - ".join(name_parts), x=x_values, y=y_values)


def speed_delta(a: TelemetrySession, b: TelemetrySession, axis: str | None = None) -> ComparisonSeries:
    selected_axis = axis or preferred_axis([a, b])
    series_a = _series_for_session(a, "speed_kmh", selected_axis)
    series_b = _series_for_session(b, "speed_kmh", selected_axis)
    count = min(len(series_a.x), len(series_b.x))
    x = series_a.x[:count]
    y = [series_a.y[index] - series_b.y[index] for index in range(count)]
    return ComparisonSeries(name="Speed delta", x=x, y=y)


def sample_has_metric(sample: TelemetrySample, metric: str) -> bool:
    return sample_metric_value(sample, metric) is not None
