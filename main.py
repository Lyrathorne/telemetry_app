import random
import sys

from PySide6.QtCore import QTimer
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


class TelemetryWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()

        self.setWindowTitle("Racing Telemetry")
        self.setMinimumSize(360, 260)

        self.telemetry_labels: dict[str, QLabel] = {}

        self.timer = QTimer(self)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self.update_telemetry)

        self.status_label = QLabel("Status: Stopped")
        self.status_label.setStyleSheet("font-size: 16px; font-weight: bold;")

        self.start_button = QPushButton("Start")
        self.stop_button = QPushButton("Stop")
        self.stop_button.setEnabled(False)

        self.start_button.clicked.connect(self.start_recording)
        self.stop_button.clicked.connect(self.stop_recording)

        self.build_layout()
        self.show_stopped_values()

    def build_layout(self) -> None:
        central_widget = QWidget()
        main_layout = QVBoxLayout()
        telemetry_layout = QGridLayout()
        button_layout = QHBoxLayout()

        telemetry_names = ["Speed", "RPM", "Gear", "Throttle", "Brake"]

        for row, name in enumerate(telemetry_names):
            name_label = QLabel(f"{name}:")
            value_label = QLabel("--")

            name_label.setStyleSheet("font-size: 15px;")
            value_label.setStyleSheet("font-size: 15px; font-weight: bold;")

            telemetry_layout.addWidget(name_label, row, 0)
            telemetry_layout.addWidget(value_label, row, 1)

            self.telemetry_labels[name] = value_label

        button_layout.addWidget(self.start_button)
        button_layout.addWidget(self.stop_button)

        main_layout.addWidget(self.status_label)
        main_layout.addLayout(telemetry_layout)
        main_layout.addLayout(button_layout)

        central_widget.setLayout(main_layout)
        self.setCentralWidget(central_widget)

    def start_recording(self) -> None:
        self.status_label.setText("Status: Recording")
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.timer.start()

    def stop_recording(self) -> None:
        self.timer.stop()
        self.status_label.setText("Status: Stopped")
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)

    def update_telemetry(self) -> None:
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

    def show_stopped_values(self) -> None:
        self.telemetry_labels["Speed"].setText("0 km/h")
        self.telemetry_labels["RPM"].setText("0 rpm")
        self.telemetry_labels["Gear"].setText("N")
        self.telemetry_labels["Throttle"].setText("0%")
        self.telemetry_labels["Brake"].setText("0%")


def main() -> None:
    app = QApplication(sys.argv)
    window = TelemetryWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
