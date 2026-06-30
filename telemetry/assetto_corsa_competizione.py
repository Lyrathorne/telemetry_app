import struct
import time
import logging
import os

from PySide6.QtCore import QObject, QTimer

from models import TelemetrySample
from telemetry.base import SourceState, TelemetrySource
from telemetry.windows_shared_memory import NamedSharedMemory


ACC_OFF = 0
ACC_REPLAY = 1
ACC_LIVE = 2
ACC_PAUSE = 3

ACC_STATUS_NAMES = {
    ACC_OFF: "Off",
    ACC_REPLAY: "Replay",
    ACC_LIVE: "Live",
    ACC_PAUSE: "Paused",
}

MAP_NAMES = {
    "physics": "Local\\acpmf_physics",
    "graphics": "Local\\acpmf_graphics",
    "static": "Local\\acpmf_static",
}

PHYSICS_MAP_SIZE = 800
GRAPHICS_MAP_SIZE = 1588
STATIC_MAP_SIZE = 784

PHYSICS_FORMAT = "=ifffii"
PHYSICS_HEADER_SIZE = struct.calcsize(PHYSICS_FORMAT)
SPEED_KMH_OFFSET = PHYSICS_HEADER_SIZE + 4

GRAPHICS_HEADER_FORMAT = "=iii"
GRAPHICS_CURRENT_TIME_TEXT_OFFSET = 12
GRAPHICS_LAST_TIME_TEXT_OFFSET = 42
GRAPHICS_BEST_TIME_TEXT_OFFSET = 72
GRAPHICS_SPLIT_TEXT_OFFSET = 102
GRAPHICS_COMPLETED_LAPS_OFFSET = 132
GRAPHICS_POSITION_OFFSET = 136
GRAPHICS_CURRENT_TIME_OFFSET = 140
GRAPHICS_LAST_TIME_OFFSET = 144
GRAPHICS_BEST_TIME_OFFSET = 148
GRAPHICS_SESSION_TIME_LEFT_OFFSET = 152
GRAPHICS_DISTANCE_TRAVELED_OFFSET = 156
GRAPHICS_IS_IN_PIT_OFFSET = 160
GRAPHICS_CURRENT_SECTOR_OFFSET = 164
GRAPHICS_LAST_SECTOR_TIME_OFFSET = 168
GRAPHICS_NUMBER_OF_LAPS_OFFSET = 172
GRAPHICS_TYRE_COMPOUND_OFFSET = 176
GRAPHICS_REPLAY_TIME_MULTIPLIER_OFFSET = 244
GRAPHICS_NORMALIZED_POSITION_OFFSET = 248


class AccTelemetrySource(TelemetrySource):
    source_id = "assetto_corsa_competizione"
    display_name = "Assetto Corsa Competizione"

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._retry_timer = QTimer(self)
        self._retry_timer.setInterval(1000)
        self._retry_timer.timeout.connect(self._try_connect)
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(33)
        self._poll_timer.timeout.connect(self._poll)
        self._physics_map: NamedSharedMemory | None = None
        self._graphics_map: NamedSharedMemory | None = None
        self._static_map: NamedSharedMemory | None = None
        self._last_packet_id: int | None = None
        self._last_sample_time = 0.0
        self._last_timing_diagnostics: tuple | None = None
        self._timing_diagnostics_enabled = os.environ.get("RACING_TELEMETRY_ACC_TIMING_DIAGNOSTICS") == "1"
        self._logger = logging.getLogger(__name__)

    def start(self) -> None:
        if self.is_running():
            return

        self._set_running(True)
        self._set_state(SourceState.WAITING_FOR_DATA, "Waiting for game")
        self.diagnostics_changed.emit({"shared_memory": "Waiting", "last_error": ""})
        self._try_connect()

        if self.is_running() and self._physics_map is None:
            self._retry_timer.start()

    def stop(self) -> None:
        self._retry_timer.stop()
        self._poll_timer.stop()
        self._close_maps()

        if self.is_running():
            self._set_state(SourceState.STOPPED, "Stopped")

        self._set_running(False)
        self.diagnostics_changed.emit({"shared_memory": "Stopped"})

    def _try_connect(self) -> None:
        if not self.is_running() or self._physics_map is not None:
            return

        try:
            self._physics_map = NamedSharedMemory(MAP_NAMES["physics"], PHYSICS_MAP_SIZE)
            self._graphics_map = NamedSharedMemory(MAP_NAMES["graphics"], GRAPHICS_MAP_SIZE)
            self._static_map = NamedSharedMemory(MAP_NAMES["static"], STATIC_MAP_SIZE)
            self._physics_map.open()
            self._graphics_map.open()
            self._static_map.open()
        except (FileNotFoundError, OSError, ValueError) as error:
            self._close_maps()
            self._set_state(SourceState.WAITING_FOR_DATA, "Waiting for game")
            self.diagnostics_changed.emit(
                {"shared_memory": "Waiting", "last_error": readable_error(error)}
            )
            return

        self._retry_timer.stop()
        self._poll_timer.start()
        self._set_state(SourceState.WAITING_FOR_DATA, "Waiting for telemetry")
        self.diagnostics_changed.emit({"shared_memory": "Connected"})

    def _poll(self) -> None:
        if self._physics_map is None or self._graphics_map is None or self._static_map is None:
            self._handle_mapping_lost("Shared memory is not connected")
            return

        try:
            physics = read_acc_physics(self._physics_map)
            graphics = read_acc_graphics(self._graphics_map)
            statics = read_acc_static(self._static_map)
        except (BufferError, OSError, struct.error, ValueError) as error:
            self._handle_mapping_lost(readable_error(error))
            return

        status_name = ACC_STATUS_NAMES.get(graphics["status"], f"Unknown ({graphics['status']})")
        self.diagnostics_changed.emit(
            {
                "shared_memory": "Connected",
                "game_state": status_name,
                "car_name": statics["car_name"],
                "track_name": statics["track_name"],
                "acc_sector_index": graphics.get("current_sector_index"),
                "acc_raw_current_lap_time_ms": graphics.get("raw_current_lap_time_ms"),
                "acc_current_lap_time_ms": graphics.get("current_lap_time_ms"),
                "acc_raw_last_lap_time_ms": graphics.get("raw_last_lap_time_ms"),
                "acc_last_lap_time_ms": graphics.get("last_lap_time_ms"),
                "acc_split_ms": graphics.get("current_split_time_ms"),
                "acc_last_sector_time_ms": graphics.get("last_sector_time_ms"),
                "acc_completed_laps": graphics.get("completed_laps"),
                "acc_normalized_position": graphics.get("normalized_track_position"),
                "acc_in_pit": graphics.get("is_in_pit"),
            }
        )

        if graphics["status"] not in (ACC_LIVE, ACC_PAUSE):
            self._set_state(SourceState.WAITING_FOR_DATA, "Waiting for live session")
            return

        if physics["packet_id"] == self._last_packet_id:
            if self._last_sample_time and time.monotonic() - self._last_sample_time >= 1.0:
                self._set_state(SourceState.WAITING_FOR_DATA, "No telemetry received")
            return

        self._last_packet_id = physics["packet_id"]
        self._last_sample_time = time.monotonic()
        self._set_state(SourceState.CONNECTED, "Connected")
        if self._timing_diagnostics_enabled:
            self._log_timing_transition(graphics)
        self.sample_received.emit(
            TelemetrySample(
                speed_kmh=max(0.0, physics["speed_kmh"]),
                rpm=max(0, physics["rpm"]),
                gear=normalize_acc_gear(physics["gear"]),
                throttle_percent=to_percent(physics["gas"]),
                brake_percent=to_percent(physics["brake"]),
                source_name=self.display_name,
                car_name=statics["car_name"],
                track_name=statics["track_name"],
                session_state=status_name,
                timestamp=time.time(),
                current_lap_time_ms=graphics.get("current_lap_time_ms"),
                last_lap_time_ms=graphics.get("last_lap_time_ms"),
                completed_laps=graphics.get("completed_laps"),
                current_sector_index=graphics.get("current_sector_index"),
                current_split_time_ms=graphics.get("current_split_time_ms"),
                last_sector_time_ms=graphics.get("last_sector_time_ms"),
                lap_distance=graphics.get("distance_traveled_m"),
                normalized_track_position=graphics.get("normalized_track_position"),
                in_pit=graphics.get("is_in_pit"),
            )
        )

    def _log_timing_transition(self, graphics: dict) -> None:
        current = (
            graphics.get("completed_laps"),
            graphics.get("current_sector_index"),
            graphics.get("current_lap_time_ms"),
            graphics.get("last_lap_time_ms"),
            graphics.get("current_split_time_ms"),
            graphics.get("last_sector_time_ms"),
            graphics.get("distance_traveled_m"),
            graphics.get("is_in_pit"),
            graphics.get("status"),
        )
        if current == self._last_timing_diagnostics:
            return
        previous_sector = None if self._last_timing_diagnostics is None else self._last_timing_diagnostics[1]
        self._logger.info(
            "ACC timing transition: lap=%s sector=%s -> %s current_lap_time_ms=%s "
            "raw_split_ms=%s last_sector_time_ms=%s last_lap_time_ms=%s in_pit=%s status=%s",
            graphics.get("completed_laps"),
            previous_sector,
            graphics.get("current_sector_index"),
            graphics.get("current_lap_time_ms"),
            graphics.get("current_split_time_ms"),
            graphics.get("last_sector_time_ms"),
            graphics.get("last_lap_time_ms"),
            graphics.get("is_in_pit"),
            graphics.get("status"),
        )
        self._last_timing_diagnostics = current

    def _handle_mapping_lost(self, message: str) -> None:
        self._poll_timer.stop()
        self._close_maps()
        self._set_state(SourceState.WAITING_FOR_DATA, "Waiting for game")
        self.diagnostics_changed.emit({"shared_memory": "Waiting", "last_error": message})

        if self.is_running():
            self._retry_timer.start()

    def _close_maps(self) -> None:
        self._last_packet_id = None
        self._last_sample_time = 0.0

        for mapping_name in ("_physics_map", "_graphics_map", "_static_map"):
            mapping = getattr(self, mapping_name)
            if mapping is not None:
                mapping.close()
                setattr(self, mapping_name, None)


def read_acc_physics(mapping: NamedSharedMemory) -> dict:
    packet_id, gas, brake, _fuel, gear, rpm = struct.unpack(
        PHYSICS_FORMAT, mapping.read_bytes(0, PHYSICS_HEADER_SIZE)
    )
    speed_kmh = struct.unpack("=f", mapping.read_bytes(SPEED_KMH_OFFSET, 4))[0]

    return {
        "packet_id": int(packet_id),
        "gas": float(gas),
        "brake": float(brake),
        "gear": int(gear),
        "rpm": int(rpm),
        "speed_kmh": float(speed_kmh),
    }


def read_acc_graphics(mapping: NamedSharedMemory) -> dict:
    packet_id, status, session_type = struct.unpack(
        GRAPHICS_HEADER_FORMAT,
        mapping.read_bytes(0, struct.calcsize(GRAPHICS_HEADER_FORMAT)),
    )
    completed_laps = read_int32(mapping, GRAPHICS_COMPLETED_LAPS_OFFSET)
    current_lap_time_raw = read_int32(mapping, GRAPHICS_CURRENT_TIME_OFFSET)
    last_lap_time_raw = read_int32(mapping, GRAPHICS_LAST_TIME_OFFSET)
    best_lap_time_raw = read_int32(mapping, GRAPHICS_BEST_TIME_OFFSET)
    current_lap_time_text_ms = parse_acc_time_text(read_utf16(mapping, GRAPHICS_CURRENT_TIME_TEXT_OFFSET, 15))
    last_lap_time_text_ms = parse_acc_time_text(read_utf16(mapping, GRAPHICS_LAST_TIME_TEXT_OFFSET, 15))
    best_lap_time_text_ms = parse_acc_time_text(read_utf16(mapping, GRAPHICS_BEST_TIME_TEXT_OFFSET, 15))
    current_split_time_ms = parse_acc_time_text(read_utf16(mapping, GRAPHICS_SPLIT_TEXT_OFFSET, 15))
    distance_traveled_m = read_float32(mapping, GRAPHICS_DISTANCE_TRAVELED_OFFSET)
    is_in_pit = bool(read_int32(mapping, GRAPHICS_IS_IN_PIT_OFFSET))
    current_sector_index = read_int32(mapping, GRAPHICS_CURRENT_SECTOR_OFFSET)
    last_sector_time_ms = read_int32(mapping, GRAPHICS_LAST_SECTOR_TIME_OFFSET)
    normalized_position = read_float32(mapping, GRAPHICS_NORMALIZED_POSITION_OFFSET)
    return {
        "packet_id": int(packet_id),
        "status": int(status),
        "session_type": int(session_type),
        "completed_laps": max(0, int(completed_laps)),
        "raw_current_lap_time_ms": current_lap_time_raw,
        "raw_last_lap_time_ms": last_lap_time_raw,
        "raw_best_lap_time_ms": best_lap_time_raw,
        "current_lap_time_ms": first_time_or_none(current_lap_time_text_ms, current_lap_time_raw),
        "last_lap_time_ms": first_time_or_none(last_lap_time_text_ms, last_lap_time_raw),
        "best_lap_time_ms": first_time_or_none(best_lap_time_text_ms, best_lap_time_raw),
        "current_split_time_ms": positive_time_or_none(current_split_time_ms),
        "last_sector_time_ms": positive_time_or_none(last_sector_time_ms),
        "distance_traveled_m": max(0.0, float(distance_traveled_m)),
        "normalized_track_position": normalized_position if 0.0 <= normalized_position <= 1.0 else None,
        "is_in_pit": is_in_pit,
        "current_sector_index": current_sector_index if 0 <= current_sector_index <= 2 else None,
    }


def read_acc_static(mapping: NamedSharedMemory) -> dict:
    offset = 0
    sm_version = read_utf16(mapping, offset, 15)
    offset += 15 * 2
    ac_version = read_utf16(mapping, offset, 15)
    offset += 15 * 2
    offset += 8
    car_name = read_utf16(mapping, offset, 33)
    offset += 33 * 2
    track_name = read_utf16(mapping, offset, 33)

    return {
        "sm_version": sm_version,
        "ac_version": ac_version,
        "car_name": car_name,
        "track_name": track_name,
    }


def read_utf16(mapping: NamedSharedMemory, offset: int, wchar_count: int) -> str:
    raw = mapping.read_bytes(offset, wchar_count * 2)
    return raw.decode("utf-16-le", errors="ignore").split("\x00", 1)[0].strip()


def read_int32(mapping: NamedSharedMemory, offset: int) -> int:
    return int(struct.unpack("=i", mapping.read_bytes(offset, 4))[0])


def read_float32(mapping: NamedSharedMemory, offset: int) -> float:
    return float(struct.unpack("=f", mapping.read_bytes(offset, 4))[0])


def positive_time_or_none(value: int) -> int | None:
    return int(value) if value > 0 else None


def first_time_or_none(*values: int | None) -> int | None:
    for value in values:
        if value is not None and int(value) > 0:
            return int(value)
    return None


def parse_acc_time_text(value: str) -> int | None:
    text = value.strip().replace("+", "").replace("-", "")
    if not text or text in {"--", "-:--.---"}:
        return None
    try:
        parts = text.split(":")
        seconds_text = parts[-1]
        seconds = float(seconds_text)
        minutes = int(parts[-2]) if len(parts) >= 2 else 0
        hours = int(parts[-3]) if len(parts) >= 3 else 0
        return int(round(((hours * 60 + minutes) * 60 + seconds) * 1000))
    except (ValueError, IndexError):
        return None


def normalize_acc_gear(raw_gear: int) -> int:
    # ACC 1.8.12 documents the same gear encoding as AC: 0=R, 1=N, 2=1st.
    if raw_gear == 0:
        return -1

    if raw_gear == 1:
        return 0

    return raw_gear - 1


def to_percent(value: float) -> float:
    return max(0.0, min(100.0, value * 100.0))


def readable_error(error: BaseException) -> str:
    message = str(error).strip()
    return message or error.__class__.__name__
