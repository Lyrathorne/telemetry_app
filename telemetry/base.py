from PySide6.QtCore import QObject, Signal


class TelemetrySource(QObject):
    sample_received = Signal(object)
    status_changed = Signal(str)
    error_occurred = Signal(str)
    diagnostics_changed = Signal(dict)

    source_id = "base"
    display_name = "Telemetry source"

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._running = False

    def start(self) -> None:
        raise NotImplementedError

    def stop(self) -> None:
        raise NotImplementedError

    def is_running(self) -> bool:
        return self._running

    def _set_running(self, running: bool) -> None:
        self._running = running
