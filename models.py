from dataclasses import dataclass, field
from datetime import datetime
from uuid import uuid4


@dataclass(slots=True)
class TelemetrySample:
    speed_kmh: float = 0.0
    rpm: int = 0
    gear: int = 0
    throttle_percent: float = 0.0
    brake_percent: float = 0.0
    source_name: str = ""
    car_name: str = ""
    track_name: str = ""
    session_state: str = ""
    timestamp: float = field(default=0.0)
    session_time: float | None = None
    lap_number: int | None = None
    lap_time: float | None = None
    lap_distance: float | None = None
    clutch_percent: float | None = None
    steering: float | None = None

    @property
    def speed_kph(self) -> float:
        return self.speed_kmh

    @property
    def throttle(self) -> float:
        return self.throttle_percent

    @property
    def brake(self) -> float:
        return self.brake_percent


@dataclass(slots=True)
class TelemetrySession:
    id: str = field(default_factory=lambda: uuid4().hex)
    source_type: str = "imported"
    session_name: str = "Imported session"
    driver_name: str = ""
    game: str = ""
    car: str = ""
    track: str = ""
    created_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    source_filename: str = ""
    notes: str = ""
    samples: list[TelemetrySample] = field(default_factory=list)

    @property
    def duration(self) -> float:
        if len(self.samples) < 2:
            return 0.0
        return max(0.0, float(self.samples[-1].timestamp) - float(self.samples[0].timestamp))

    @property
    def sample_count(self) -> int:
        return len(self.samples)

    @property
    def lap_label(self) -> str:
        laps = {sample.lap_number for sample in self.samples if sample.lap_number is not None}
        if len(laps) == 1:
            return f"Lap {next(iter(laps))}"
        return ""


METRICS = {
    "speed_kmh": "Speed",
    "rpm": "RPM",
    "throttle_percent": "Throttle",
    "brake_percent": "Brake",
    "gear": "Gear",
    "clutch_percent": "Clutch",
    "steering": "Steering",
}


def sample_metric_value(sample: TelemetrySample, metric: str) -> float | None:
    value = getattr(sample, metric, None)
    if value is None:
        return None
    return float(value)


def sample_x_value(sample: TelemetrySample, axis: str, first_timestamp: float | None = None) -> float | None:
    if axis == "lap_distance":
        return sample.lap_distance
    if axis == "lap_time":
        return sample.lap_time
    if axis == "session_time":
        return sample.session_time
    if first_timestamp is None:
        first_timestamp = 0.0
    return float(sample.timestamp) - float(first_timestamp)


def format_gear(gear: int) -> str:
    if gear == -1:
        return "R"

    if gear == 0:
        return "N"

    return str(gear)
