from dataclasses import dataclass, field


@dataclass(slots=True)
class TelemetrySample:
    speed_kmh: float
    rpm: int
    gear: int
    throttle_percent: float
    brake_percent: float
    source_name: str = ""
    car_name: str = ""
    track_name: str = ""
    session_state: str = ""
    timestamp: float = field(default=0.0)


def format_gear(gear: int) -> str:
    if gear == -1:
        return "R"

    if gear == 0:
        return "N"

    return str(gear)
