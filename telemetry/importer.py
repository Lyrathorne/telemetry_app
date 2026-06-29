from __future__ import annotations

import csv
import json
from dataclasses import asdict
from pathlib import Path

from models import TelemetrySample, TelemetrySession


CANONICAL_COLUMNS = {
    "timestamp",
    "session_time",
    "lap_number",
    "lap_time",
    "lap_distance",
    "speed_kmh",
    "rpm",
    "gear",
    "throttle_percent",
    "brake_percent",
    "clutch_percent",
    "steering",
}

COLUMN_ALIASES = {
    "time": "session_time",
    "timestamp": "timestamp",
    "session_time": "session_time",
    "lap": "lap_number",
    "lap_number": "lap_number",
    "lap_time": "lap_time",
    "lap_distance": "lap_distance",
    "distance": "lap_distance",
    "speed": "speed_kmh",
    "speed_kph": "speed_kmh",
    "speed_kmh": "speed_kmh",
    "speed_mph": "speed_mph",
    "rpm": "rpm",
    "gear": "gear",
    "throttle": "throttle_percent",
    "accelerator": "throttle_percent",
    "brake": "brake_percent",
    "steering": "steering",
    "steer": "steering",
    "clutch": "clutch_percent",
}

METRIC_COLUMNS = {
    "speed_kmh",
    "speed_mph",
    "rpm",
    "gear",
    "throttle_percent",
    "brake_percent",
    "clutch_percent",
    "steering",
}

X_AXIS_COLUMNS = {"timestamp", "session_time", "lap_time", "lap_distance"}


class TelemetryImportError(ValueError):
    pass


def import_telemetry_file(path: str | Path, metadata: dict | None = None) -> TelemetrySession:
    file_path = Path(path)
    suffix = file_path.suffix.lower()
    if suffix == ".csv":
        return import_csv(file_path, metadata)
    if suffix == ".json":
        return import_json(file_path, metadata)
    raise TelemetryImportError(f"Unsupported telemetry format: {file_path.suffix or 'unknown'}")


def import_json(path: str | Path, metadata: dict | None = None) -> TelemetrySession:
    file_path = Path(path)
    try:
        raw = json.loads(file_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise TelemetryImportError(f"Could not read JSON telemetry: {error}") from error

    if isinstance(raw, dict) and "samples" in raw:
        session_data = raw
        samples_raw = raw.get("samples", [])
    elif isinstance(raw, list):
        session_data = {}
        samples_raw = raw
    else:
        raise TelemetryImportError("JSON telemetry must contain a samples list.")

    samples = [_sample_from_mapping(row) for row in samples_raw if isinstance(row, dict)]
    _validate_samples(samples)
    session = TelemetrySession(
        source_type=str(session_data.get("source_type", "json")),
        session_name=str(session_data.get("session_name", file_path.stem)),
        driver_name=str(session_data.get("driver_name", "")),
        game=str(session_data.get("game", "")),
        car=str(session_data.get("car", "")),
        track=str(session_data.get("track", "")),
        source_filename=str(file_path),
        notes=str(session_data.get("notes", "")),
        samples=samples,
    )
    apply_metadata(session, metadata)
    return session


def import_csv(path: str | Path, metadata: dict | None = None) -> TelemetrySession:
    file_path = Path(path)
    text = read_text_with_fallbacks(file_path)
    if not text.strip():
        raise TelemetryImportError("Telemetry file is empty.")

    delimiter = detect_delimiter(text)
    reader = csv.DictReader(text.splitlines(), delimiter=delimiter)
    if not reader.fieldnames:
        raise TelemetryImportError("CSV file does not contain a header row.")

    mapping = map_columns(reader.fieldnames)
    validate_column_mapping(mapping)

    samples: list[TelemetrySample] = []
    invalid_rows = 0
    for row in reader:
        try:
            samples.append(sample_from_csv_row(row, mapping))
        except (TypeError, ValueError):
            invalid_rows += 1

    _validate_samples(samples)
    session = TelemetrySession(
        source_type="csv",
        session_name=file_path.stem,
        source_filename=str(file_path),
        samples=samples,
        notes=f"Imported from CSV. Skipped invalid rows: {invalid_rows}",
    )
    apply_metadata(session, metadata)
    return session


def read_text_with_fallbacks(path: Path) -> str:
    for encoding in ("utf-8-sig", "utf-8", "cp1251", "latin-1"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    raise TelemetryImportError("Could not decode telemetry file.")


def detect_delimiter(text: str) -> str:
    sample = "\n".join(text.splitlines()[:10])
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error as error:
        raise TelemetryImportError("Could not detect CSV delimiter.") from error
    if dialect.delimiter not in {",", ";", "\t"}:
        raise TelemetryImportError("Unsupported CSV delimiter.")
    return dialect.delimiter


def map_columns(fieldnames: list[str]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    used_targets: set[str] = set()
    for field in fieldnames:
        key = normalize_column_name(field)
        target = COLUMN_ALIASES.get(key)
        if target and target not in used_targets:
            mapping[field] = target
            used_targets.add(target)
    return mapping


def validate_column_mapping(mapping: dict[str, str]) -> None:
    targets = set(mapping.values())
    if not targets & X_AXIS_COLUMNS:
        raise TelemetryImportError("CSV import requires a time or distance column.")
    if not targets & METRIC_COLUMNS:
        raise TelemetryImportError("CSV import requires at least one telemetry metric.")


def sample_from_csv_row(row: dict[str, str], mapping: dict[str, str]) -> TelemetrySample:
    values: dict[str, float | int | None] = {}
    for source, target in mapping.items():
        value = parse_number(row.get(source, ""))
        if value is None:
            continue
        values[target] = value

    if "speed_mph" in values:
        values["speed_kmh"] = float(values.pop("speed_mph")) * 1.609344

    for percent_key in ("throttle_percent", "brake_percent", "clutch_percent"):
        if percent_key in values and values[percent_key] is not None:
            values[percent_key] = normalize_control(float(values[percent_key]))

    timestamp = float(values.get("timestamp") or values.get("session_time") or 0.0)
    return TelemetrySample(
        timestamp=timestamp,
        session_time=optional_float(values.get("session_time")),
        lap_number=optional_int(values.get("lap_number")),
        lap_time=optional_float(values.get("lap_time")),
        lap_distance=optional_float(values.get("lap_distance")),
        speed_kmh=float(values.get("speed_kmh") or 0.0),
        rpm=int(values.get("rpm") or 0),
        gear=int(values.get("gear") or 0),
        throttle_percent=float(values.get("throttle_percent") or 0.0),
        brake_percent=float(values.get("brake_percent") or 0.0),
        clutch_percent=optional_float(values.get("clutch_percent")),
        steering=optional_float(values.get("steering")),
    )


def _sample_from_mapping(row: dict) -> TelemetrySample:
    return TelemetrySample(
        timestamp=float(row.get("timestamp") or row.get("session_time") or 0.0),
        session_time=optional_float(row.get("session_time")),
        lap_number=optional_int(row.get("lap_number")),
        lap_time=optional_float(row.get("lap_time")),
        lap_distance=optional_float(row.get("lap_distance")),
        speed_kmh=float(row.get("speed_kmh") or row.get("speed_kph") or row.get("speed") or 0.0),
        rpm=int(float(row.get("rpm") or 0)),
        gear=int(float(row.get("gear") or 0)),
        throttle_percent=normalize_control(float(row.get("throttle_percent") or row.get("throttle") or 0.0)),
        brake_percent=normalize_control(float(row.get("brake_percent") or row.get("brake") or 0.0)),
        clutch_percent=optional_float(row.get("clutch_percent") or row.get("clutch")),
        steering=optional_float(row.get("steering")),
    )


def normalize_column_name(name: str) -> str:
    return name.strip().lower().replace(" ", "_").replace("-", "_")


def parse_number(value: str | None) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return float(text.replace(",", "."))


def normalize_control(value: float) -> float:
    if 0.0 <= value <= 1.0:
        return value * 100.0
    return max(0.0, min(100.0, value))


def optional_float(value) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def optional_int(value) -> int | None:
    if value is None or value == "":
        return None
    return int(float(value))


def apply_metadata(session: TelemetrySession, metadata: dict | None) -> None:
    if not metadata:
        return
    for key in ("session_name", "driver_name", "game", "car", "track", "notes"):
        if key in metadata:
            setattr(session, key, str(metadata[key]))


def session_to_dict(session: TelemetrySession) -> dict:
    data = asdict(session)
    data["samples"] = [asdict(sample) for sample in session.samples]
    return data


def session_from_dict(data: dict) -> TelemetrySession:
    samples = [TelemetrySample(**sample) for sample in data.get("samples", [])]
    return TelemetrySession(
        id=str(data.get("id") or ""),
        source_type=str(data.get("source_type") or "imported"),
        session_name=str(data.get("session_name") or "Imported session"),
        driver_name=str(data.get("driver_name") or ""),
        game=str(data.get("game") or ""),
        car=str(data.get("car") or ""),
        track=str(data.get("track") or ""),
        created_at=str(data.get("created_at") or ""),
        source_filename=str(data.get("source_filename") or ""),
        notes=str(data.get("notes") or ""),
        samples=samples,
    )


def _validate_samples(samples: list[TelemetrySample]) -> None:
    if not samples:
        raise TelemetryImportError("Telemetry file did not contain any valid samples.")
