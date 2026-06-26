from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QHostAddress, QUdpSocket


class F1UdpCollector(QObject):
    packet_received = Signal(bytes, int)
    status_changed = Signal(str)
    error_occurred = Signal(str)

    def __init__(self) -> None:
        super().__init__()

        self.socket = QUdpSocket(self)
        self.socket.readyRead.connect(self.read_pending_packets)
        self.socket.errorOccurred.connect(self.handle_socket_error)

        self.is_listening = False

    def start(self, port: int = 20777) -> None:
        if self.is_listening:
            return

        self.socket.close()

        started = self.socket.bind(QHostAddress.SpecialAddress.AnyIPv4, port)

        if not started:
            message = self.socket.errorString()
            self.status_changed.emit("Error")
            self.error_occurred.emit(f"Could not listen on UDP port {port}: {message}")
            return

        self.is_listening = True
        self.status_changed.emit("Listening")

    def stop(self) -> None:
        if not self.is_listening:
            self.socket.close()
            self.status_changed.emit("Stopped")
            return

        self.socket.close()
        self.is_listening = False
        self.status_changed.emit("Stopped")

    def read_pending_packets(self) -> None:
        while self.socket.hasPendingDatagrams():
            packet_size = self.socket.pendingDatagramSize()
            packet_data, _sender_address, _sender_port = self.socket.readDatagram(
                packet_size
            )

            self.packet_received.emit(bytes(packet_data), len(packet_data))

    def handle_socket_error(self) -> None:
        if self.is_listening:
            self.is_listening = False

        self.status_changed.emit("Error")
        self.error_occurred.emit(self.socket.errorString())
