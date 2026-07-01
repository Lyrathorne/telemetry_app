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
    track_length_m: float | None = None
    track_metadata_id: str | None = None
    track_length_source: str | None = None
    normalized_track_position: float | None = None
    current_lap_time_ms: int | None = None
    completed_laps: int | None = None
    current_sector_index: int | None = None
    current_split_time_ms: int | None = None
    cumulative_split_1_ms: int | None = None
    cumulative_split_2_ms: int | None = None
    last_sector_time_ms: int | None = None
    last_lap_time_ms: int | None = None
    best_lap_time_ms: int | None = None
    lap_valid: bool | None = None
    invalid_lap: bool | None = None
    in_pit: bool | None = None
    in_pit_lane: bool | None = None
    clutch_percent: float | None = None
    steering: float | None = None
    world_position_x: float | None = None
    world_position_y: float | None = None
    world_position_z: float | None = None

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


@dataclass(slots=True)
class SectorResult:
    sector_number: int
    start_distance_m: float | None = None
    end_distance_m: float | None = None
    time_ms: int | None = None
    valid: bool = True
    comparison_status: str | None = None
    timing_source: str = "unavailable"


@dataclass(slots=True)
class LapTelemetrySeries:
    lap_id: str = ""
    lap_number: int = 0
    lap_time_ms: int | None = None
    fully_observed: bool = True
    valid: bool = True
    elapsed_time_s: list[float] = field(default_factory=list)
    lap_distance_m: list[float | None] = field(default_factory=list)
    normalized_position: list[float | None] = field(default_factory=list)
    speed_kmh: list[float | None] = field(default_factory=list)
    rpm: list[float | None] = field(default_factory=list)
    gear: list[int | None] = field(default_factory=list)
    throttle_percent: list[float | None] = field(default_factory=list)
    brake_percent: list[float | None] = field(default_factory=list)
    clutch_percent: list[float | None] = field(default_factory=list)
    steering: list[float | None] = field(default_factory=list)
    world_position_x: list[float | None] = field(default_factory=list)
    world_position_y: list[float | None] = field(default_factory=list)
    world_position_z: list[float | None] = field(default_factory=list)
    sector_boundary_elapsed_s: list[float] = field(default_factory=list)

    @property
    def sample_count(self) -> int:
        return len(self.elapsed_time_s)


@dataclass(slots=True)
class LapResult:
    id: str = field(default_factory=lambda: uuid4().hex)
    lap_number: int = 0
    lap_time_ms: int | None = None
    valid: bool = True
    complete: bool = False
    driver_name: str | None = None
    game: str = ""
    track: str | None = None
    track_metadata_id: str | None = None
    track_length_m: float | None = None
    track_length_source: str | None = None
    car: str | None = None
    session_id: str = ""
    sectors: list[SectorResult] = field(default_factory=list)
    samples: list[TelemetrySample] = field(default_factory=list)
    telemetry_series: LapTelemetrySeries | None = None
    fully_observed: bool = True
    raw_samples_recorded: bool = False
    started_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="milliseconds"))
    completed_at: str | None = None
    notes: str = ""

    @property
    def duration_seconds(self) -> float | None:
        if self.lap_time_ms is None:
            return None
        return self.lap_time_ms / 1000.0


@dataclass(slots=True)
class SessionSummary:
    session_id: str
    game: str = ""
    track: str | None = None
    car: str | None = None
    driver_name: str | None = None
    source_type: str = ""
    started_at: str = ""
    ended_at: str | None = None
    lap_count: int = 0
    best_lap_time_ms: int | None = None
    valid_lap_count: int = 0


@dataclass(slots=True)
class TelemetryPoint:
    timestamp: float = 0.0
    lap_time: float | None = None
    lap_progress: float | None = None
    distance: float | None = None
    speed_kmh: float | None = None
    throttle_percent: float | None = None
    brake_percent: float | None = None
    steering: float | None = None
    gear: int | None = None
    rpm: int | None = None
    world_position_x: float | None = None
    world_position_y: float | None = None
    world_position_z: float | None = None


@dataclass(slots=True)
class ReferenceLap:
    id: str = field(default_factory=lambda: uuid4().hex)
    game: str = ""
    track_id: str = ""
    track_display_name: str = ""
    car_id: str = ""
    car_display_name: str = ""
    lap_time_ms: int | None = None
    source: str = "imported"
    player_name: str = ""
    created_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="milliseconds"))
    imported_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="milliseconds"))
    telemetry_points: list[TelemetryPoint] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)


def format_time_ms(time_ms: int | None) -> str:
    if time_ms is None or int(time_ms) < 0:
        return "\u2014"
    total_ms = int(time_ms)
    hours, remainder = divmod(total_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60000)
    seconds, millis = divmod(remainder, 1000)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}.{millis:03d}"
    return f"{minutes:02d}:{seconds:02d}.{millis:03d}"


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
