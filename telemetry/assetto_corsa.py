import ctypes
import time
from ctypes import c_float, c_int32, c_wchar

from PySide6.QtCore import QObject, QTimer

from models import TelemetrySample
from telemetry.base import TelemetrySource
from telemetry.windows_shared_memory import NamedSharedMemory


AC_OFF = 0
AC_REPLAY = 1
AC_LIVE = 2
AC_PAUSE = 3

AC_STATUS_NAMES = {
    AC_OFF: "Off",
    AC_REPLAY: "Replay",
    AC_LIVE: "Live",
    AC_PAUSE: "Paused",
}

MAP_NAMES = {
    "physics": "Local\\acpmf_physics",
    "graphics": "Local\\acpmf_graphics",
    "static": "Local\\acpmf_static",
}


class SPageFilePhysics(ctypes.Structure):
    # Kunos AC Python apps use 4-byte packing for the C shared-memory structs.
    _pack_ = 4
    _fields_ = [
        ("packetId", c_int32),
        ("gas", c_float),
        ("brake", c_float),
        ("fuel", c_float),
        ("gear", c_int32),
        ("rpms", c_int32),
        ("steerAngle", c_float),
        ("speedKmh", c_float),
        ("velocity", c_float * 3),
        ("accG", c_float * 3),
        ("wheelSlip", c_float * 4),
        ("wheelLoad", c_float * 4),
        ("wheelsPressure", c_float * 4),
        ("wheelAngularSpeed", c_float * 4),
        ("tyreWear", c_float * 4),
        ("tyreDirtyLevel", c_float * 4),
        ("tyreCoreTemperature", c_float * 4),
        ("camberRAD", c_float * 4),
        ("suspensionTravel", c_float * 4),
        ("drs", c_float),
        ("tc", c_float),
        ("heading", c_float),
        ("pitch", c_float),
        ("roll", c_float),
        ("cgHeight", c_float),
        ("carDamage", c_float * 5),
        ("numberOfTyresOut", c_int32),
        ("pitLimiterOn", c_int32),
        ("abs", c_float),
        ("kersCharge", c_float),
        ("kersInput", c_float),
        ("autoShifterOn", c_int32),
        ("rideHeight", c_float * 2),
        ("turboBoost", c_float),
        ("ballast", c_float),
        ("airDensity", c_float),
        ("airTemp", c_float),
        ("roadTemp", c_float),
        ("localAngularVel", c_float * 3),
        ("finalFF", c_float),
        ("performanceMeter", c_float),
        ("engineBrake", c_int32),
        ("ersRecoveryLevel", c_int32),
        ("ersPowerLevel", c_int32),
        ("ersHeatCharging", c_int32),
        ("ersIsCharging", c_int32),
        ("kersCurrentKJ", c_float),
        ("drsAvailable", c_int32),
        ("drsEnabled", c_int32),
        ("brakeTemp", c_float * 4),
        ("clutch", c_float),
        ("tyreTempI", c_float * 4),
        ("tyreTempM", c_float * 4),
        ("tyreTempO", c_float * 4),
        ("isAIControlled", c_int32),
        ("tyreContactPoint", (c_float * 3) * 4),
        ("tyreContactNormal", (c_float * 3) * 4),
        ("tyreContactHeading", (c_float * 3) * 4),
        ("brakeBias", c_float),
        ("localVelocity", c_float * 3),
    ]


class SPageFileGraphic(ctypes.Structure):
    _pack_ = 4
    _fields_ = [
        ("packetId", c_int32),
        ("status", c_int32),
        ("session", c_int32),
        ("currentTime", c_wchar * 15),
        ("lastTime", c_wchar * 15),
        ("bestTime", c_wchar * 15),
        ("split", c_wchar * 15),
        ("completedLaps", c_int32),
        ("position", c_int32),
        ("iCurrentTime", c_int32),
        ("iLastTime", c_int32),
        ("iBestTime", c_int32),
        ("sessionTimeLeft", c_float),
        ("distanceTraveled", c_float),
        ("isInPit", c_int32),
        ("currentSectorIndex", c_int32),
        ("lastSectorTime", c_int32),
        ("numberOfLaps", c_int32),
        ("tyreCompound", c_wchar * 33),
        ("replayTimeMultiplier", c_float),
        ("normalizedCarPosition", c_float),
        ("carCoordinates", c_float * 3),
        ("penaltyTime", c_float),
        ("flag", c_int32),
        ("idealLineOn", c_int32),
        ("isInPitLine", c_int32),
        ("surfaceGrip", c_float),
        ("mandatoryPitDone", c_int32),
        ("windSpeed", c_float),
        ("windDirection", c_float),
    ]


class SPageFileStatic(ctypes.Structure):
    _pack_ = 4
    _fields_ = [
        ("smVersion", c_wchar * 15),
        ("acVersion", c_wchar * 15),
        ("numberOfSessions", c_int32),
        ("numCars", c_int32),
        ("carModel", c_wchar * 33),
        ("track", c_wchar * 33),
        ("playerName", c_wchar * 33),
        ("playerSurname", c_wchar * 33),
        ("playerNick", c_wchar * 33),
        ("sectorCount", c_int32),
        ("maxTorque", c_float),
        ("maxPower", c_float),
        ("maxRpm", c_int32),
        ("maxFuel", c_float),
        ("suspensionMaxTravel", c_float * 4),
        ("tyreRadius", c_float * 4),
        ("maxTurboBoost", c_float),
        ("airTemp", c_float),
        ("roadTemp", c_float),
        ("penaltiesEnabled", c_int32),
        ("aidFuelRate", c_float),
        ("aidTireRate", c_float),
        ("aidMechanicalDamage", c_float),
        ("aidAllowTyreBlankets", c_int32),
        ("aidStability", c_float),
        ("aidAutoClutch", c_int32),
        ("aidAutoBlip", c_int32),
        ("hasDRS", c_int32),
        ("hasERS", c_int32),
        ("hasKERS", c_int32),
        ("kersMaxJ", c_float),
        ("engineBrakeSettingsCount", c_int32),
        ("ersPowerControllerCount", c_int32),
        ("trackSPlineLength", c_float),
        ("trackConfiguration", c_wchar * 33),
        ("ersMaxJ", c_float),
        ("isTimedRace", c_int32),
        ("hasExtraLap", c_int32),
        ("carSkin", c_wchar * 33),
        ("reversedGridPositions", c_int32),
        ("pitWindowStart", c_int32),
        ("pitWindowEnd", c_int32),
    ]


class AssettoCorsaTelemetrySource(TelemetrySource):
    source_id = "assetto_corsa"
    display_name = "Assetto Corsa"

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
        self._physics: SPageFilePhysics | None = None
        self._graphics: SPageFileGraphic | None = None
        self._static: SPageFileStatic | None = None
        self._last_packet_id: int | None = None

    def start(self) -> None:
        if self.is_running():
            return

        self._set_running(True)
        self.status_changed.emit("Waiting for Assetto Corsa")
        self.diagnostics_changed.emit({"shared_memory": "Waiting"})
        self._try_connect()

        if self.is_running() and self._physics is None:
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
        if not self.is_running() or self._physics is not None:
            return

        try:
            self._physics_map = NamedSharedMemory(
                MAP_NAMES["physics"], ctypes.sizeof(SPageFilePhysics)
            )
            self._graphics_map = NamedSharedMemory(
                MAP_NAMES["graphics"], ctypes.sizeof(SPageFileGraphic)
            )
            self._static_map = NamedSharedMemory(
                MAP_NAMES["static"], ctypes.sizeof(SPageFileStatic)
            )
            self._physics_map.open()
            self._graphics_map.open()
            self._static_map.open()
        except (FileNotFoundError, OSError, ValueError) as error:
            self._close_maps()
            self.status_changed.emit("Waiting for Assetto Corsa")
            self.diagnostics_changed.emit(
                {"shared_memory": "Waiting", "last_error": readable_error(error)}
            )
            return

        self._retry_timer.stop()
        self._poll_timer.start()
        self.status_changed.emit("Connected to Assetto Corsa")
        self.diagnostics_changed.emit({"shared_memory": "Connected"})

    def _poll(self) -> None:
        if self._physics_map is None or self._graphics_map is None or self._static_map is None:
            self._handle_mapping_lost("Shared memory is not connected")
            return

        try:
            self._physics = self._physics_map.read_structure(SPageFilePhysics)
            self._graphics = self._graphics_map.read_structure(SPageFileGraphic)
            self._static = self._static_map.read_structure(SPageFileStatic)
            packet_id = int(self._physics.packetId)
            status = int(self._graphics.status)
            car_name = clean_wide_string(self._static.carModel)
            track_name = clean_wide_string(self._static.track)
        except (BufferError, OSError, ValueError) as error:
            self._handle_mapping_lost(readable_error(error))
            return

        self.diagnostics_changed.emit(
            {
                "shared_memory": "Connected",
                "game_state": AC_STATUS_NAMES.get(status, f"Unknown ({status})"),
                "car_name": car_name,
                "track_name": track_name,
            }
        )

        if status not in (AC_LIVE, AC_PAUSE) or packet_id == self._last_packet_id:
            return

        self._last_packet_id = packet_id
        self.sample_received.emit(
            TelemetrySample(
                speed_kmh=max(0.0, float(self._physics.speedKmh)),
                rpm=max(0, int(self._physics.rpms)),
                gear=normalize_ac_gear(int(self._physics.gear)),
                throttle_percent=to_percent(float(self._physics.gas)),
                brake_percent=to_percent(float(self._physics.brake)),
                source_name=self.display_name,
                car_name=car_name,
                track_name=track_name,
                session_state=AC_STATUS_NAMES.get(status, ""),
                timestamp=time.time(),
            )
        )

    def _handle_mapping_lost(self, message: str) -> None:
        self._poll_timer.stop()
        self._close_maps()
        self.status_changed.emit("Waiting for Assetto Corsa")
        self.diagnostics_changed.emit({"shared_memory": "Waiting", "last_error": message})

        if self.is_running():
            self._retry_timer.start()

    def _close_maps(self) -> None:
        self._physics = None
        self._graphics = None
        self._static = None
        self._last_packet_id = None

        for mapping_name in ("_physics_map", "_graphics_map", "_static_map"):
            mapping = getattr(self, mapping_name)
            if mapping is not None:
                mapping.close()
                setattr(self, mapping_name, None)


def normalize_ac_gear(raw_gear: int) -> int:
    # AC shared memory reports reverse as 0, neutral as 1, and first gear as 2.
    if raw_gear == 0:
        return -1

    if raw_gear == 1:
        return 0

    return raw_gear - 1


def to_percent(value: float) -> float:
    return max(0.0, min(100.0, value * 100.0))


def clean_wide_string(value: str) -> str:
    return value.split("\x00", 1)[0].strip()


def readable_error(error: BaseException) -> str:
    message = str(error).strip()
    return message or error.__class__.__name__
