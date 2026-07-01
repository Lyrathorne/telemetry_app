import struct
import time
from dataclasses import dataclass

from PySide6.QtCore import QObject, QTimer
from PySide6.QtNetwork import QHostAddress, QUdpSocket

from models import TelemetrySample
from telemetry.base import SourceState, TelemetrySource


F1_2018_PACKET_FORMAT = 2018
F1_2018_HEADER_FORMAT = "<HBBQfIB"
F1_2018_HEADER_SIZE = struct.calcsize(F1_2018_HEADER_FORMAT)
F1_2018_NUMBER_OF_CARS = 20
F1_2018_MOTION_PACKET_ID = 0
F1_2018_LAP_DATA_PACKET_ID = 2
F1_2018_CAR_TELEMETRY_PACKET_ID = 6
F1_2018_MOTION_PACKET_SIZE = 1341
F1_2018_LAP_DATA_PACKET_SIZE = 841
F1_2018_CAR_TELEMETRY_PACKET_SIZE = 1085
F1_2018_MOTION_RECORD_SIZE = 60
F1_2018_LAP_DATA_RECORD_SIZE = 41
F1_2018_CAR_TELEMETRY_RECORD_SIZE = 53

CAR_SPEED_OFFSET = 0
CAR_THROTTLE_OFFSET = 2
CAR_STEERING_OFFSET = 3
CAR_BRAKE_OFFSET = 4
CAR_CLUTCH_OFFSET = 5
CAR_GEAR_OFFSET = 6
CAR_RPM_OFFSET = 7

LAP_LAST_LAP_TIME_OFFSET = 0
LAP_CURRENT_LAP_TIME_OFFSET = 4
LAP_BEST_LAP_TIME_OFFSET = 8
LAP_DISTANCE_OFFSET = 20
LAP_CURRENT_LAP_NUMBER_OFFSET = 33
LAP_CURRENT_SECTOR_OFFSET = 35
LAP_INVALID_OFFSET = 36

PACKET_NAMES = {
    0: "Motion",
    1: "Session",
    2: "Lap Data",
    3: "Event",
    4: "Participants",
    5: "Car Setups",
    6: "Car Telemetry",
    7: "Car Status",
}


@dataclass(slots=True)
class PacketHeader:
    packet_format: int
    packet_version: int
    packet_id: int
    session_time: float
    frame_identifier: int
    player_car_index: int


@dataclass(slots=True)
class F1CarTelemetry:
    speed_kmh: int
    rpm: int
    gear: int
    throttle_percent: int
    brake_percent: int
    clutch_percent: int
    steering: int


@dataclass(slots=True)
class F1LapData:
    last_lap_time_ms: int | None
    current_lap_time_ms: int | None
    best_lap_time_ms: int | None
    lap_distance: float | None
    lap_number: int
    current_sector_index: int | None
    invalid_lap: bool


@dataclass(slots=True)
class F1MotionData:
    world_position_x: float
    world_position_y: float
    world_position_z: float


class F12018TelemetrySource(TelemetrySource):
    source_id = "f1_2018"
    display_name = "F1 2018"

    def __init__(self, port: int = 20777, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.port = port
        self._socket = QUdpSocket(self)
        self._socket.readyRead.connect(self._read_pending_datagrams)
        self._socket.errorOccurred.connect(self._handle_socket_error)
        self._packet_count = 0
        self._valid_telemetry_packets = 0
        self._parser_errors = 0
        self._last_packet_time = 0.0
        self._latest_lap_data: F1LapData | None = None
        self._latest_motion_data: F1MotionData | None = None
        self._timeout_timer = QTimer(self)
        self._timeout_timer.setInterval(1000)
        self._timeout_timer.timeout.connect(self._check_timeout)

    def start(self) -> None:
        if self.is_running():
            return

        self._reset_diagnostics()
        self._socket.close()
        address = QHostAddress(QHostAddress.SpecialAddress.AnyIPv4)

        if not self._socket.bind(address, self.port):
            message = self._socket.errorString()
            self._set_error(f"Could not listen on UDP port {self.port}: {message}")
            self._emit_diagnostics({"udp_status": "Error"})
            return

        self._set_running(True)
        self._last_packet_time = 0.0
        self._set_state(SourceState.WAITING_FOR_DATA, f"Listening for UDP on port {self.port}")
        self._timeout_timer.start()
        self._emit_diagnostics({"udp_status": f"Listening on port {self.port}"})

    def stop(self) -> None:
        self._timeout_timer.stop()
        self._socket.close()

        if self.is_running():
            self._set_state(SourceState.STOPPED, "Stopped")

        self._set_running(False)
        self._emit_diagnostics({"udp_status": "Stopped"})

    def _read_pending_datagrams(self) -> None:
        while self._socket.hasPendingDatagrams():
            packet_size = self._socket.pendingDatagramSize()
            packet_data, _sender_address, _sender_port = self._socket.readDatagram(packet_size)
            self.process_packet(bytes(packet_data))

    def process_packet(self, packet: bytes) -> None:
        self._last_packet_time = time.monotonic()
        self._packet_count += 1
        self._emit_diagnostics(
            {
                "packets_received": self._packet_count,
                "latest_packet_size": len(packet),
                "packet_preview": packet[:30].hex(" ").upper() or "--",
            }
        )

        header = parse_packet_header(packet)

        if header is None:
            self._add_parser_error("Invalid packet")
            return

        self._emit_diagnostics(
            {
                "detected_format": header.packet_format,
                "packet_type": get_packet_name(header.packet_id),
                "player_car_index": header.player_car_index,
            }
        )

        if header.packet_format != F1_2018_PACKET_FORMAT:
            self._emit_diagnostics({"parser_status": f"Unsupported packet format: {header.packet_format}"})
            return

        if header.packet_id == F1_2018_LAP_DATA_PACKET_ID:
            self._latest_lap_data = parse_lap_data_packet(packet, header.player_car_index)
            self._emit_diagnostics({"parser_status": "F1 2018 lap data decoded"})
            return

        if header.packet_id == F1_2018_MOTION_PACKET_ID:
            self._latest_motion_data = parse_motion_packet(packet, header.player_car_index)
            self._emit_diagnostics({"parser_status": "F1 2018 motion data decoded"})
            return

        if header.packet_id != F1_2018_CAR_TELEMETRY_PACKET_ID:
            self._emit_diagnostics({"parser_status": "F1 2018 detected"})
            return

        telemetry = parse_car_telemetry_packet(packet, header.player_car_index)

        if telemetry is None:
            self._add_parser_error("Invalid car telemetry packet")
            return

        self._valid_telemetry_packets += 1
        self._set_state(SourceState.CONNECTED, "Connected")
        self._emit_diagnostics(
            {
                "valid_telemetry_packets": self._valid_telemetry_packets,
                "parser_status": "F1 2018 car telemetry decoded",
            }
        )
        self.sample_received.emit(
            TelemetrySample(
                speed_kmh=float(telemetry.speed_kmh),
                rpm=telemetry.rpm,
                gear=telemetry.gear,
                throttle_percent=float(telemetry.throttle_percent),
                brake_percent=float(telemetry.brake_percent),
                clutch_percent=float(telemetry.clutch_percent),
                steering=float(telemetry.steering),
                source_name=self.display_name,
                session_state="UDP telemetry",
                timestamp=time.time(),
                session_time=header.session_time,
                lap_number=self._latest_lap_data.lap_number if self._latest_lap_data else None,
                lap_time=self._latest_lap_data.current_lap_time_ms / 1000.0 if self._latest_lap_data and self._latest_lap_data.current_lap_time_ms is not None else None,
                current_lap_time_ms=self._latest_lap_data.current_lap_time_ms if self._latest_lap_data else None,
                last_lap_time_ms=self._latest_lap_data.last_lap_time_ms if self._latest_lap_data else None,
                best_lap_time_ms=self._latest_lap_data.best_lap_time_ms if self._latest_lap_data else None,
                completed_laps=max(0, self._latest_lap_data.lap_number - 1) if self._latest_lap_data else None,
                current_sector_index=self._latest_lap_data.current_sector_index if self._latest_lap_data else None,
                lap_distance=self._latest_lap_data.lap_distance if self._latest_lap_data else None,
                invalid_lap=self._latest_lap_data.invalid_lap if self._latest_lap_data else None,
                lap_valid=not self._latest_lap_data.invalid_lap if self._latest_lap_data else None,
                world_position_x=self._latest_motion_data.world_position_x if self._latest_motion_data else None,
                world_position_y=self._latest_motion_data.world_position_y if self._latest_motion_data else None,
                world_position_z=self._latest_motion_data.world_position_z if self._latest_motion_data else None,
            )
        )

    def _add_parser_error(self, status: str) -> None:
        self._parser_errors += 1
        self._emit_diagnostics({"parser_errors": self._parser_errors, "parser_status": status})

    def _reset_diagnostics(self) -> None:
        self._packet_count = 0
        self._valid_telemetry_packets = 0
        self._parser_errors = 0
        self._emit_diagnostics(
            {
                "udp_status": "Starting",
                "packets_received": 0,
                "latest_packet_size": 0,
                "packet_preview": "--",
                "detected_format": "--",
                "packet_type": "--",
                "player_car_index": "--",
                "valid_telemetry_packets": 0,
                "parser_errors": 0,
                "parser_status": "Waiting for F1 2018 telemetry",
                "updates_per_second": "--",
            }
        )

    def _emit_diagnostics(self, values: dict) -> None:
        self.diagnostics_changed.emit(values)

    def _handle_socket_error(self, _socket_error=None) -> None:
        message = self._socket.errorString()

        if not message:
            return

        self._timeout_timer.stop()
        self._set_error(message)
        self._emit_diagnostics({"udp_status": "Error"})

    def _check_timeout(self) -> None:
        if not self.is_running():
            return

        if self._last_packet_time == 0.0:
            self._set_state(SourceState.WAITING_FOR_DATA, f"Listening for UDP on port {self.port}")
            self._emit_diagnostics({"udp_status": f"Listening on port {self.port}"})
            return

        elapsed = time.monotonic() - self._last_packet_time
        if elapsed >= 1.0:
            self._set_state(SourceState.WAITING_FOR_DATA, "No telemetry received")
            self._emit_diagnostics({"udp_status": "No telemetry received"})


def parse_packet_header(packet: bytes) -> PacketHeader | None:
    if len(packet) < F1_2018_HEADER_SIZE:
        return None

    try:
        (
            packet_format,
            packet_version,
            packet_id,
            _session_uid,
            session_time,
            frame_identifier,
            player_car_index,
        ) = struct.unpack_from(F1_2018_HEADER_FORMAT, packet, 0)
    except struct.error:
        return None

    return PacketHeader(
        packet_format=packet_format,
        packet_version=packet_version,
        packet_id=packet_id,
        session_time=session_time,
        frame_identifier=frame_identifier,
        player_car_index=player_car_index,
    )


def parse_car_telemetry_packet(packet: bytes, player_car_index: int) -> F1CarTelemetry | None:
    if len(packet) != F1_2018_CAR_TELEMETRY_PACKET_SIZE:
        return None

    if player_car_index < 0 or player_car_index >= F1_2018_NUMBER_OF_CARS:
        return None

    player_data_start = (
        F1_2018_HEADER_SIZE + player_car_index * F1_2018_CAR_TELEMETRY_RECORD_SIZE
    )

    try:
        speed = struct.unpack_from("<H", packet, player_data_start + CAR_SPEED_OFFSET)[0]
        throttle = struct.unpack_from("<B", packet, player_data_start + CAR_THROTTLE_OFFSET)[0]
        steering = struct.unpack_from("<b", packet, player_data_start + CAR_STEERING_OFFSET)[0]
        brake = struct.unpack_from("<B", packet, player_data_start + CAR_BRAKE_OFFSET)[0]
        clutch = struct.unpack_from("<B", packet, player_data_start + CAR_CLUTCH_OFFSET)[0]
        gear = struct.unpack_from("<b", packet, player_data_start + CAR_GEAR_OFFSET)[0]
        rpm = struct.unpack_from("<H", packet, player_data_start + CAR_RPM_OFFSET)[0]
    except struct.error:
        return None

    if speed > 500 or rpm > 25000 or throttle > 100 or brake > 100 or clutch > 100:
        return None

    return F1CarTelemetry(
        speed_kmh=speed,
        rpm=rpm,
        gear=gear,
        throttle_percent=throttle,
        brake_percent=brake,
        clutch_percent=clutch,
        steering=steering,
    )


def parse_lap_data_packet(packet: bytes, player_car_index: int) -> F1LapData | None:
    if len(packet) != F1_2018_LAP_DATA_PACKET_SIZE:
        return None
    if player_car_index < 0 or player_car_index >= F1_2018_NUMBER_OF_CARS:
        return None
    player_data_start = F1_2018_HEADER_SIZE + player_car_index * F1_2018_LAP_DATA_RECORD_SIZE
    try:
        last_lap_time = struct.unpack_from("<f", packet, player_data_start + LAP_LAST_LAP_TIME_OFFSET)[0]
        current_lap_time = struct.unpack_from("<f", packet, player_data_start + LAP_CURRENT_LAP_TIME_OFFSET)[0]
        best_lap_time = struct.unpack_from("<f", packet, player_data_start + LAP_BEST_LAP_TIME_OFFSET)[0]
        lap_distance = struct.unpack_from("<f", packet, player_data_start + LAP_DISTANCE_OFFSET)[0]
        lap_number = struct.unpack_from("<B", packet, player_data_start + LAP_CURRENT_LAP_NUMBER_OFFSET)[0]
        sector = struct.unpack_from("<B", packet, player_data_start + LAP_CURRENT_SECTOR_OFFSET)[0]
        invalid_lap = struct.unpack_from("<B", packet, player_data_start + LAP_INVALID_OFFSET)[0]
    except struct.error:
        return None
    return F1LapData(
        last_lap_time_ms=seconds_to_ms(last_lap_time),
        current_lap_time_ms=seconds_to_ms(current_lap_time),
        best_lap_time_ms=seconds_to_ms(best_lap_time),
        lap_distance=max(0.0, float(lap_distance)) if lap_distance >= 0.0 else None,
        lap_number=int(lap_number),
        current_sector_index=int(sector) if 0 <= int(sector) <= 2 else None,
        invalid_lap=bool(invalid_lap),
    )


def parse_motion_packet(packet: bytes, player_car_index: int) -> F1MotionData | None:
    if len(packet) != F1_2018_MOTION_PACKET_SIZE:
        return None
    if player_car_index < 0 or player_car_index >= F1_2018_NUMBER_OF_CARS:
        return None
    player_data_start = F1_2018_HEADER_SIZE + player_car_index * F1_2018_MOTION_RECORD_SIZE
    try:
        x, y, z = struct.unpack_from("<fff", packet, player_data_start)
    except struct.error:
        return None
    return F1MotionData(float(x), float(y), float(z))


def seconds_to_ms(value: float) -> int | None:
    return int(round(value * 1000.0)) if value > 0.0 else None


def get_packet_name(packet_id: int) -> str:
    return PACKET_NAMES.get(packet_id, f"Unknown ({packet_id})")
