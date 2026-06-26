import random

from PySide6.QtCore import QTimer
from PySide6.QtGui import QIntValidator
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from collectors.f1_udp_collector import F1UdpCollector


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()

        self.setWindowTitle("Racing Telemetry")
        self.setMinimumSize(460, 420)

        self.telemetry_labels: dict[str, QLabel] = {}
        self.packet_count = 0

        self.demo_timer = QTimer(self)
        self.demo_timer.setInterval(100)
        self.demo_timer.timeout.connect(self.update_demo_telemetry)

        self.udp_collector = F1UdpCollector()
        self.udp_collector.packet_received.connect(self.handle_udp_packet)
        self.udp_collector.status_changed.connect(self.handle_udp_status)
        self.udp_collector.error_occurred.connect(self.show_udp_error)

        self.build_layout()
        self.show_stopped_telemetry()
        self.update_mode("Stopped mode")

    def build_layout(self) -> None:
        central_widget = QWidget()
        main_layout = QVBoxLayout()

        self.mode_label = QLabel()
        self.mode_label.setStyleSheet("font-size: 16px; font-weight: bold;")

        main_layout.addWidget(self.mode_label)
        main_layout.addLayout(self.create_telemetry_layout())
        main_layout.addLayout(self.create_demo_buttons())
        main_layout.addLayout(self.create_udp_layout())

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

    def create_demo_buttons(self) -> QHBoxLayout:
        button_layout = QHBoxLayout()

        self.start_demo_button = QPushButton("Start Demo")
        self.stop_demo_button = QPushButton("Stop Demo")
        self.stop_demo_button.setEnabled(False)

        self.start_demo_button.clicked.connect(self.start_demo_mode)
        self.stop_demo_button.clicked.connect(self.stop_demo_mode)

        button_layout.addWidget(self.start_demo_button)
        button_layout.addWidget(self.stop_demo_button)

        return button_layout

    def create_udp_layout(self) -> QGridLayout:
        udp_layout = QGridLayout()

        self.port_input = QLineEdit("20777")
        self.port_input.setValidator(QIntValidator(1, 65535, self))

        self.start_udp_button = QPushButton("Start UDP")
        self.stop_udp_button = QPushButton("Stop UDP")
        self.stop_udp_button.setEnabled(False)

        self.udp_status_label = QLabel("UDP status: Stopped")
        self.packet_count_label = QLabel("Packets received: 0")
        self.latest_size_label = QLabel("Latest packet size: 0 bytes")
        self.preview_label = QLabel("Latest packet preview: --")
        self.error_label = QLabel("")

        self.preview_label.setWordWrap(True)
        self.preview_label.setStyleSheet("font-family: Consolas, monospace;")
        self.error_label.setWordWrap(True)
        self.error_label.setStyleSheet("color: #b00020;")

        self.start_udp_button.clicked.connect(self.start_udp_mode)
        self.stop_udp_button.clicked.connect(self.stop_udp_mode)

        udp_layout.addWidget(QLabel("UDP port:"), 0, 0)
        udp_layout.addWidget(self.port_input, 0, 1)
        udp_layout.addWidget(self.start_udp_button, 1, 0)
        udp_layout.addWidget(self.stop_udp_button, 1, 1)
        udp_layout.addWidget(self.udp_status_label, 2, 0, 1, 2)
        udp_layout.addWidget(self.packet_count_label, 3, 0, 1, 2)
        udp_layout.addWidget(self.latest_size_label, 4, 0, 1, 2)
        udp_layout.addWidget(self.preview_label, 5, 0, 1, 2)
        udp_layout.addWidget(self.error_label, 6, 0, 1, 2)

        return udp_layout

    def start_demo_mode(self) -> None:
        self.stop_udp_mode()

        self.demo_timer.start()
        self.update_mode("Demo mode")
        self.start_demo_button.setEnabled(False)
        self.stop_demo_button.setEnabled(True)

    def stop_demo_mode(self) -> None:
        self.demo_timer.stop()
        self.show_stopped_telemetry()

        self.start_demo_button.setEnabled(True)
        self.stop_demo_button.setEnabled(False)

        if not self.udp_collector.is_listening:
            self.update_mode("Stopped mode")

    def start_udp_mode(self) -> None:
        port = self.get_port_from_input()

        if port is None:
            return

        self.stop_demo_mode()
        self.error_label.setText("")
        self.packet_count = 0
        self.update_packet_labels(0, b"")

        self.udp_collector.start(port)

    def stop_udp_mode(self) -> None:
        self.udp_collector.stop()

    def get_port_from_input(self) -> int | None:
        port_text = self.port_input.text().strip()

        if not port_text:
            self.show_udp_error("Please enter a UDP port number.")
            return None

        try:
            port = int(port_text)
        except ValueError:
            self.show_udp_error("UDP port must be a number.")
            return None

        if port < 1 or port > 65535:
            self.show_udp_error("UDP port must be between 1 and 65535.")
            return None

        return port

    def handle_udp_status(self, status: str) -> None:
        self.udp_status_label.setText(f"UDP status: {status}")

        if status == "Listening":
            self.update_mode("UDP listening mode")
            self.port_input.setEnabled(False)
            self.start_udp_button.setEnabled(False)
            self.stop_udp_button.setEnabled(True)
            return

        self.port_input.setEnabled(True)
        self.start_udp_button.setEnabled(True)
        self.stop_udp_button.setEnabled(False)

        if not self.demo_timer.isActive():
            self.update_mode("Stopped mode")

    def handle_udp_packet(self, packet_data: bytes, packet_size: int) -> None:
        self.packet_count += 1
        self.update_packet_labels(packet_size, packet_data)

    def update_packet_labels(self, packet_size: int, packet_data: bytes) -> None:
        preview = packet_data[:30].hex(" ")

        if not preview:
            preview = "--"

        self.packet_count_label.setText(f"Packets received: {self.packet_count}")
        self.latest_size_label.setText(f"Latest packet size: {packet_size} bytes")
        self.preview_label.setText(f"Latest packet preview: {preview}")

    def show_udp_error(self, message: str) -> None:
        self.error_label.setText(message)

    def update_mode(self, mode: str) -> None:
        self.mode_label.setText(f"Mode: {mode}")

    def update_demo_telemetry(self) -> None:
        speed = random.randint(0, 340)
        rpm = random.randint(800, 12000)
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
