import random
import sys

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


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()

        self.setWindowTitle("Racing Telemetry")
        self.setMinimumSize(460, 380)

        self.current_mode = "Stopped"
        self.packet_count = 0
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
