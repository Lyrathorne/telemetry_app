import struct
import time
from dataclasses import dataclass

from PySide6.QtCore import QObject
from PySide6.QtNetwork import QHostAddress, QUdpSocket

from models import TelemetrySample
from telemetry.base import TelemetrySource


F1_2018_PACKET_FORMAT = 2018
F1_2018_HEADER_FORMAT = "<HBBQfIB"
F1_2018_HEADER_SIZE = struct.calcsize(F1_2018_HEADER_FORMAT)
F1_2018_NUMBER_OF_CARS = 20
F1_2018_CAR_TELEMETRY_PACKET_ID = 6
F1_2018_CAR_TELEMETRY_PACKET_SIZE = 1085
F1_2018_CAR_TELEMETRY_RECORD_SIZE = 53

CAR_SPEED_OFFSET = 0
CAR_THROTTLE_OFFSET = 2
CAR_BRAKE_OFFSET = 4
CAR_GEAR_OFFSET = 6
CAR_RPM_OFFSET = 7

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

    def start(self) -> None:
        if self.is_running():
            return

        self._reset_diagnostics()
        self._socket.close()
        address = QHostAddress(QHostAddress.SpecialAddress.AnyIPv4)

        if not self._socket.bind(address, self.port):
            message = self._socket.errorString()
            self.status_changed.emit("Error")
            self.error_occurred.emit(f"Could not listen on UDP port {self.port}: {message}")
            self._set_running(False)
            return

        self._set_running(True)
        self.status_changed.emit(f"Listening on UDP port {self.port}")
        self._emit_diagnostics({"udp_status": f"Listening on port {self.port}"})

    def stop(self) -> None:
        self._socket.close()

        if self.is_running():
            self.status_changed.emit("Stopped")

        self._set_running(False)
        self._emit_diagnostics({"udp_status": "Stopped"})

    def _read_pending_datagrams(self) -> None:
        while self._socket.hasPendingDatagrams():
            packet_size = self._socket.pendingDatagramSize()
            packet_data, _sender_address, _sender_port = self._socket.readDatagram(packet_size)
            self.process_packet(bytes(packet_data))

    def process_packet(self, packet: bytes) -> None:
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

        if header.packet_id != F1_2018_CAR_TELEMETRY_PACKET_ID:
            self._emit_diagnostics({"parser_status": "F1 2018 detected"})
            return

        telemetry = parse_car_telemetry_packet(packet, header.player_car_index)

        if telemetry is None:
            self._add_parser_error("Invalid car telemetry packet")
            return

        self._valid_telemetry_packets += 1
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
                source_name=self.display_name,
                session_state="UDP telemetry",
                timestamp=time.time(),
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
            }
        )

    def _emit_diagnostics(self, values: dict) -> None:
        self.diagnostics_changed.emit(values)

    def _handle_socket_error(self, _socket_error=None) -> None:
        message = self._socket.errorString()

        if not message:
            return

        self._set_running(False)
        self.status_changed.emit("Error")
        self.error_occurred.emit(message)
        self._emit_diagnostics({"udp_status": "Error"})


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
        brake = struct.unpack_from("<B", packet, player_data_start + CAR_BRAKE_OFFSET)[0]
        gear = struct.unpack_from("<b", packet, player_data_start + CAR_GEAR_OFFSET)[0]
        rpm = struct.unpack_from("<H", packet, player_data_start + CAR_RPM_OFFSET)[0]
    except struct.error:
        return None

    if speed > 500 or rpm > 25000 or throttle > 100 or brake > 100:
        return None

    return F1CarTelemetry(
        speed_kmh=speed,
        rpm=rpm,
        gear=gear,
        throttle_percent=throttle,
        brake_percent=brake,
    )


def get_packet_name(packet_id: int) -> str:
    return PACKET_NAMES.get(packet_id, f"Unknown ({packet_id})")
