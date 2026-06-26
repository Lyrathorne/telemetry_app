import random
import struct
import sys
from dataclasses import dataclass

from PySide6.QtCore import QTimer
from PySide6.QtGui import QCloseEvent
from PySide6.QtNetwork import QHostAddress, QUdpSocket
from PySide6.QtWidgets import (
    QApplication,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

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


@dataclass
class PacketHeader:
    packet_format: int
    packet_version: int
    packet_id: int
    session_time: float
    frame_identifier: int
    player_car_index: int


@dataclass
class TelemetryData:
    speed: int
    rpm: int
    gear: int
    throttle: int
    brake: int


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()

        self.setWindowTitle("Racing Telemetry")
        self.setMinimumSize(460, 380)

        self.current_mode = "Stopped"
        self.packet_count = 0
        self.valid_telemetry_packets = 0
        self.parser_errors = 0
        self.udp_port = 20777
        self.udp_listening = False

        self.telemetry_labels: dict[str, QLabel] = {}

        self.demo_timer = QTimer(self)
        self.demo_timer.setInterval(100)
        self.demo_timer.timeout.connect(self.update_fake_telemetry)

        self.udp_socket = QUdpSocket(self)
        self.udp_socket.readyRead.connect(self.read_pending_datagrams)
        self.udp_socket.errorOccurred.connect(self.handle_udp_error)

        self.build_interface()
        self.show_stopped_telemetry()
        self.set_mode("Stopped")
        self.update_button_states()

    def build_interface(self) -> None:
        central_widget = QWidget()
        main_layout = QVBoxLayout()

        self.mode_label = QLabel()
        self.mode_label.setStyleSheet("font-size: 16px; font-weight: bold;")

        self.udp_status_label = QLabel("UDP status: Stopped")
        self.parser_status_label = QLabel(
            "Parser status: Waiting for F1 2018 telemetry"
        )
        self.detected_format_label = QLabel("Detected format: --")
        self.packet_type_label = QLabel("Packet type: --")
        self.player_car_index_label = QLabel("Player car index: --")
        self.valid_telemetry_packets_label = QLabel("Valid telemetry packets: 0")
        self.parser_errors_label = QLabel("Parser errors: 0")
        self.packet_count_label = QLabel("Packets received: 0")
        self.latest_packet_size_label = QLabel("Latest packet size: 0 bytes")
        self.packet_preview_label = QLabel("Packet preview: --")
        self.error_label = QLabel("")

        self.packet_preview_label.setWordWrap(True)
        self.packet_preview_label.setStyleSheet("font-family: Consolas, monospace;")
        self.error_label.setWordWrap(True)
        self.error_label.setStyleSheet("color: #b00020;")

        main_layout.addWidget(self.mode_label)
        main_layout.addLayout(self.create_telemetry_layout())
        main_layout.addLayout(self.create_button_layout())
        main_layout.addWidget(self.udp_status_label)
        main_layout.addWidget(self.parser_status_label)
        main_layout.addWidget(self.detected_format_label)
        main_layout.addWidget(self.packet_type_label)
        main_layout.addWidget(self.player_car_index_label)
        main_layout.addWidget(self.valid_telemetry_packets_label)
        main_layout.addWidget(self.parser_errors_label)
        main_layout.addWidget(self.packet_count_label)
        main_layout.addWidget(self.latest_packet_size_label)
        main_layout.addWidget(self.packet_preview_label)
        main_layout.addWidget(self.error_label)

        central_widget.setLayout(main_layout)
        self.setCentralWidget(central_widget)

    def create_telemetry_layout(self) -> QGridLayout:
        telemetry_layout = QGridLayout()
        telemetry_names = ["Speed", "RPM", "Gear", "Throttle", "Brake"]

        for row, name in enumerate(telemetry_names):
            name_label = QLabel(f"{name}:")
            value_label = QLabel("--")

            name_label.setStyleSheet("font-size: 15px;")
            value_label.setStyleSheet("font-size: 15px; font-weight: bold;")

            telemetry_layout.addWidget(name_label, row, 0)
            telemetry_layout.addWidget(value_label, row, 1)

            self.telemetry_labels[name] = value_label

        return telemetry_layout

    def create_button_layout(self) -> QHBoxLayout:
        button_layout = QHBoxLayout()

        self.start_demo_button = QPushButton("Start Demo")
        self.stop_demo_button = QPushButton("Stop Demo")
        self.start_udp_button = QPushButton("Start UDP")
        self.stop_udp_button = QPushButton("Stop UDP")

        self.start_demo_button.clicked.connect(self.start_demo)
        self.stop_demo_button.clicked.connect(self.stop_demo)
        self.start_udp_button.clicked.connect(self.start_udp)
        self.stop_udp_button.clicked.connect(self.stop_udp)

        button_layout.addWidget(self.start_demo_button)
        button_layout.addWidget(self.stop_demo_button)
        button_layout.addWidget(self.start_udp_button)
        button_layout.addWidget(self.stop_udp_button)

        return button_layout

    def start_demo(self) -> None:
        if self.demo_timer.isActive():
            return

        # Demo and UDP use different data sources, so only one can run at a time.
        if self.udp_listening:
            self.stop_udp()

        self.error_label.setText("")
        self.demo_timer.start()
        self.set_mode("Demo")
        self.update_button_states()

    def stop_demo(self) -> None:
        if self.demo_timer.isActive():
            self.demo_timer.stop()

        if not self.udp_listening:
            self.set_mode("Stopped")

        self.update_button_states()

    def start_udp(self) -> None:
        if self.udp_listening:
            return

        if self.demo_timer.isActive():
            self.stop_demo()

        self.error_label.setText("")
        self.reset_udp_session()
        self.udp_socket.close()

        address = QHostAddress(QHostAddress.SpecialAddress.AnyIPv4)
        is_listening = self.udp_socket.bind(address, self.udp_port)

        if not is_listening:
            self.udp_listening = False
            self.set_mode("Stopped")
            self.udp_status_label.setText("UDP status: Error")
            self.error_label.setText(self.udp_socket.errorString())
            self.update_button_states()
            return

        self.udp_listening = True
        self.set_mode("UDP")
        self.udp_status_label.setText(
            f"UDP status: Listening on port {self.udp_port}"
        )
        self.update_button_states()

    def stop_udp(self) -> None:
        if self.udp_listening:
            self.udp_socket.close()
            self.udp_listening = False

        self.udp_status_label.setText("UDP status: Stopped")

        if not self.demo_timer.isActive():
            self.set_mode("Stopped")

        self.update_button_states()

    def read_pending_datagrams(self) -> None:
        # readyRead can mean more than one datagram is waiting, so read them all.
        while self.udp_socket.hasPendingDatagrams():
            packet_size = self.udp_socket.pendingDatagramSize()
            packet_data, _sender_address, _sender_port = self.udp_socket.readDatagram(
                packet_size
            )

            self.packet_count += 1
            self.update_packet_information(bytes(packet_data))
            self.process_f1_packet(bytes(packet_data))

    def update_packet_information(self, packet_data: bytes) -> None:
        packet_size = len(packet_data)
        preview = packet_data[:30].hex(" ").upper()

        if not preview:
            preview = "--"

        self.packet_count_label.setText(f"Packets received: {self.packet_count}")
        self.latest_packet_size_label.setText(
            f"Latest packet size: {packet_size} bytes"
        )
        self.packet_preview_label.setText(f"Packet preview: {preview}")

    def process_f1_packet(self, packet: bytes) -> None:
        header = self.parse_packet_header(packet)

        if header is None:
            self.add_parser_error("Parser status: Invalid packet")
            return

        packet_name = self.get_packet_name(header.packet_id)
        self.detected_format_label.setText(
            f"Detected format: {header.packet_format}"
        )
        self.packet_type_label.setText(f"Packet type: {packet_name}")
        self.player_car_index_label.setText(
            f"Player car index: {header.player_car_index}"
        )

        if header.packet_format != F1_2018_PACKET_FORMAT:
            self.set_parser_status(
                f"Parser status: Unsupported packet format: {header.packet_format}"
            )
            return

        if header.packet_id != F1_2018_CAR_TELEMETRY_PACKET_ID:
            # F1 sends several packet types; only packet 6 updates this dashboard.
            self.set_parser_status("Parser status: F1 2018 detected")
            return

        telemetry = self.parse_car_telemetry_packet(
            packet,
            header.player_car_index,
        )

        if telemetry is None:
            self.add_parser_error("Parser status: Invalid car telemetry packet")
            return

        self.valid_telemetry_packets += 1
        self.valid_telemetry_packets_label.setText(
            f"Valid telemetry packets: {self.valid_telemetry_packets}"
        )
        self.set_parser_status("Parser status: F1 2018 car telemetry decoded")
        self.update_real_telemetry(telemetry)

    def parse_packet_header(self, packet: bytes) -> PacketHeader | None:
        if len(packet) < F1_2018_HEADER_SIZE:
            return None

        try:
            # F1 2018 packets are packed and use little-endian byte order.
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

    def parse_car_telemetry_packet(
        self,
        packet: bytes,
        player_car_index: int,
    ) -> TelemetryData | None:
        if len(packet) != F1_2018_CAR_TELEMETRY_PACKET_SIZE:
            return None

        if player_car_index < 0 or player_car_index >= F1_2018_NUMBER_OF_CARS:
            return None

        # Each telemetry packet contains one fixed-size record per car.
        player_data_start = (
            F1_2018_HEADER_SIZE
            + player_car_index * F1_2018_CAR_TELEMETRY_RECORD_SIZE
        )

        try:
            speed = struct.unpack_from(
                "<H",
                packet,
                player_data_start + CAR_SPEED_OFFSET,
            )[0]
            throttle = struct.unpack_from(
                "<B",
                packet,
                player_data_start + CAR_THROTTLE_OFFSET,
            )[0]
            brake = struct.unpack_from(
                "<B",
                packet,
                player_data_start + CAR_BRAKE_OFFSET,
            )[0]
            gear = struct.unpack_from(
                "<b",
                packet,
                player_data_start + CAR_GEAR_OFFSET,
            )[0]
            rpm = struct.unpack_from(
                "<H",
                packet,
                player_data_start + CAR_RPM_OFFSET,
            )[0]
        except struct.error:
            return None

        if speed > 500 or rpm > 25000 or throttle > 100 or brake > 100:
            return None

        return TelemetryData(
            speed=speed,
            rpm=rpm,
            gear=gear,
            throttle=throttle,
            brake=brake,
        )

    def update_real_telemetry(self, telemetry: TelemetryData) -> None:
        self.telemetry_labels["Speed"].setText(f"{telemetry.speed} km/h")
        self.telemetry_labels["RPM"].setText(f"{telemetry.rpm} rpm")
        self.telemetry_labels["Gear"].setText(self.format_gear(telemetry.gear))
        self.telemetry_labels["Throttle"].setText(f"{telemetry.throttle}%")
        self.telemetry_labels["Brake"].setText(f"{telemetry.brake}%")

    def reset_udp_session(self) -> None:
        self.packet_count = 0
        self.valid_telemetry_packets = 0
        self.parser_errors = 0

        self.packet_count_label.setText("Packets received: 0")
        self.latest_packet_size_label.setText("Latest packet size: 0 bytes")
        self.packet_preview_label.setText("Packet preview: --")
        self.detected_format_label.setText("Detected format: --")
        self.packet_type_label.setText("Packet type: --")
        self.player_car_index_label.setText("Player car index: --")
        self.valid_telemetry_packets_label.setText("Valid telemetry packets: 0")
        self.parser_errors_label.setText("Parser errors: 0")
        self.set_parser_status("Parser status: Waiting for F1 2018 telemetry")

    def add_parser_error(self, status: str) -> None:
        self.parser_errors += 1
        self.parser_errors_label.setText(f"Parser errors: {self.parser_errors}")
        self.set_parser_status(status)

    def set_parser_status(self, status: str) -> None:
        if self.parser_status_label.text() != status:
            self.parser_status_label.setText(status)

    def get_packet_name(self, packet_id: int) -> str:
        packet_name = PACKET_NAMES.get(packet_id)

        if packet_name is None:
            return f"Unknown ({packet_id})"

        return packet_name

    def format_gear(self, gear: int) -> str:
        if gear == -1:
            return "R"

        if gear == 0:
            return "N"

        return str(gear)

    def handle_udp_error(self, _socket_error=None) -> None:
        error_message = self.udp_socket.errorString()

        if not error_message:
            return

        self.udp_listening = False
        self.udp_status_label.setText("UDP status: Error")
        self.error_label.setText(error_message)

        if not self.demo_timer.isActive():
            self.set_mode("Stopped")

        self.update_button_states()

    def update_fake_telemetry(self) -> None:
        speed = random.randint(0, 350)
        rpm = random.randint(1000, 15000)
        gear = random.choice(["N", "1", "2", "3", "4", "5", "6", "7", "8"])
        throttle = random.randint(0, 100)
        brake = random.randint(0, 100)

        self.telemetry_labels["Speed"].setText(f"{speed} km/h")
        self.telemetry_labels["RPM"].setText(f"{rpm} rpm")
        self.telemetry_labels["Gear"].setText(gear)
        self.telemetry_labels["Throttle"].setText(f"{throttle}%")
        self.telemetry_labels["Brake"].setText(f"{brake}%")

    def show_stopped_telemetry(self) -> None:
        self.telemetry_labels["Speed"].setText("0 km/h")
        self.telemetry_labels["RPM"].setText("0 rpm")
        self.telemetry_labels["Gear"].setText("N")
        self.telemetry_labels["Throttle"].setText("0%")
        self.telemetry_labels["Brake"].setText("0%")

    def set_mode(self, mode: str) -> None:
        self.current_mode = mode
        self.mode_label.setText(f"Mode: {mode}")

    def update_button_states(self) -> None:
        is_demo = self.current_mode == "Demo"
        is_udp = self.current_mode == "UDP"

        self.start_demo_button.setEnabled(not is_demo)
        self.stop_demo_button.setEnabled(is_demo)
        self.start_udp_button.setEnabled(not is_udp)
        self.stop_udp_button.setEnabled(is_udp)

    def closeEvent(self, event: QCloseEvent) -> None:
        self.demo_timer.stop()
        self.udp_socket.close()
        self.udp_listening = False
        event.accept()


def main() -> None:
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
