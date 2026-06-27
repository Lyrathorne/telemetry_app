import struct
import time

from PySide6.QtCore import QObject, QTimer

from models import TelemetrySample
from telemetry.base import TelemetrySource
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

    def start(self) -> None:
        if self.is_running():
            return

        self._set_running(True)
        self.status_changed.emit("Waiting for Assetto Corsa Competizione")
        self.diagnostics_changed.emit({"shared_memory": "Waiting"})
        self._try_connect()

        if self.is_running() and self._physics_map is None:
            self._retry_timer.start()

    def stop(self) -> None:
        self._retry_timer.stop()
        self._poll_timer.stop()
        self._close_maps()

        if self.is_running():
            self.status_changed.emit("Stopped")

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
            self.status_changed.emit("Waiting for Assetto Corsa Competizione")
            self.diagnostics_changed.emit(
                {"shared_memory": "Waiting", "last_error": readable_error(error)}
            )
            return

        self._retry_timer.stop()
        self._poll_timer.start()
        self.status_changed.emit("Connected to Assetto Corsa Competizione")
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
            }
        )

        if graphics["status"] not in (ACC_LIVE, ACC_PAUSE):
            return

        if physics["packet_id"] == self._last_packet_id:
            return

        self._last_packet_id = physics["packet_id"]
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
            )
        )

    def _handle_mapping_lost(self, message: str) -> None:
        self._poll_timer.stop()
        self._close_maps()
        self.status_changed.emit("Waiting for Assetto Corsa Competizione")
        self.diagnostics_changed.emit({"shared_memory": "Waiting", "last_error": message})

        if self.is_running():
            self._retry_timer.start()

    def _close_maps(self) -> None:
        self._last_packet_id = None

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
    return {
        "packet_id": int(packet_id),
        "status": int(status),
        "session_type": int(session_type),
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
