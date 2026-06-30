from __future__ import annotations

import csv
import json
from pathlib import Path

from models import ReferenceLap, TelemetryPoint
from telemetry.display_names import display_car_name, display_track_name
from telemetry.importer import TelemetryImportError, normalize_control, parse_number, read_text_with_fallbacks


REFERENCE_ALIASES = {
    "time": "timestamp",
    "timestamp": "timestamp",
    "lap_time": "lap_time",
    "progress": "lap_progress",
    "lap_progress": "lap_progress",
    "normalized_track_position": "lap_progress",
    "distance": "distance",
    "lap_distance": "distance",
    "speed": "speed_kmh",
    "speed_kmh": "speed_kmh",
    "speed_kph": "speed_kmh",
    "throttle": "throttle_percent",
    "throttle_percent": "throttle_percent",
    "brake": "brake_percent",
    "brake_percent": "brake_percent",
    "steering": "steering",
    "gear": "gear",
    "rpm": "rpm",
}


def import_reference_lap(path: str | Path) -> ReferenceLap:
    file_path = Path(path)
    if file_path.suffix.lower() == ".json":
        return import_reference_json(file_path)
    if file_path.suffix.lower() == ".csv":
        return import_reference_csv(file_path)
    raise TelemetryImportError(f"Unsupported reference telemetry format: {file_path.suffix or 'unknown'}")


def import_reference_json(path: str | Path) -> ReferenceLap:
    file_path = Path(path)
    try:
        raw = json.loads(file_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise TelemetryImportError(f"Could not read reference JSON: {error}") from error
    if not isinstance(raw, dict):
        raise TelemetryImportError("Reference JSON must be an object with telemetry_points or samples.")
    points_raw = raw.get("telemetry_points", raw.get("samples", []))
    points = [point_from_mapping(item) for item in points_raw if isinstance(item, dict)]
    validate_points(points)
    track = str(raw.get("track_id") or raw.get("track") or "")
    car = str(raw.get("car_id") or raw.get("car") or "")
    return ReferenceLap(
        game=str(raw.get("game") or ""),
        track_id=track,
        track_display_name=str(raw.get("track_display_name") or display_track_name(track)),
        car_id=car,
        car_display_name=str(raw.get("car_display_name") or display_car_name(car)),
        lap_time_ms=optional_lap_time_ms(raw.get("lap_time_ms") or raw.get("lap_time")),
        source=str(raw.get("source") or "json"),
        player_name=str(raw.get("player_name") or raw.get("driver_name") or ""),
        telemetry_points=points,
        metadata={key: value for key, value in raw.items() if key not in {"telemetry_points", "samples"}},
    )


def import_reference_csv(path: str | Path) -> ReferenceLap:
    file_path = Path(path)
    text = read_text_with_fallbacks(file_path)
    reader = csv.DictReader(text.splitlines())
    if not reader.fieldnames:
        raise TelemetryImportError("Reference CSV must contain headers.")
    mapping = {field: REFERENCE_ALIASES.get(field.strip().lower().replace(" ", "_")) for field in reader.fieldnames}
    points = []
    for row in reader:
        values = {target: parse_number(row.get(source)) for source, target in mapping.items() if target}
        if values:
            points.append(point_from_mapping(values))
    validate_points(points)
    return ReferenceLap(source="csv", player_name=file_path.stem, telemetry_points=points)


def point_from_mapping(data: dict) -> TelemetryPoint:
    throttle = optional_float(data.get("throttle_percent") or data.get("throttle"))
    brake = optional_float(data.get("brake_percent") or data.get("brake"))
    return TelemetryPoint(
        timestamp=float(data.get("timestamp") or data.get("time") or 0.0),
        lap_time=optional_float(data.get("lap_time")),
        lap_progress=optional_float(data.get("lap_progress") or data.get("progress") or data.get("normalized_track_position")),
        distance=optional_float(data.get("distance") or data.get("lap_distance")),
        speed_kmh=optional_float(data.get("speed_kmh") or data.get("speed_kph") or data.get("speed")),
        throttle_percent=normalize_control(throttle) if throttle is not None else None,
        brake_percent=normalize_control(brake) if brake is not None else None,
        steering=optional_float(data.get("steering")),
        gear=optional_int(data.get("gear")),
        rpm=optional_int(data.get("rpm")),
    )


def validate_points(points: list[TelemetryPoint]) -> None:
    if not points:
        raise TelemetryImportError("Reference lap did not contain telemetry points.")
    if not any(point.distance is not None or point.lap_progress is not None for point in points):
        raise TelemetryImportError("Reference lap needs distance or progress values.")


def optional_float(value) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def optional_int(value) -> int | None:
    if value is None or value == "":
        return None
    return int(float(value))


def optional_lap_time_ms(value) -> int | None:
    if value is None or value == "":
        return None
    numeric = float(value)
    return int(numeric if numeric > 1000 else numeric * 1000)
