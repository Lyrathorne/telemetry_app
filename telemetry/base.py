from enum import StrEnum

from PySide6.QtCore import QObject, Signal


class SourceState(StrEnum):
    STOPPED = "Stopped"
    STARTING = "Starting"
    WAITING_FOR_DATA = "Waiting for data"
    CONNECTED = "Connected"
    ERROR = "Error"
    STOPPING = "Stopping"


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
        self._state = SourceState.STOPPED
        self._last_error = ""

    def start(self) -> None:
        raise NotImplementedError

    def stop(self) -> None:
        raise NotImplementedError

    def is_running(self) -> bool:
        return self._running

    def state(self) -> SourceState:
        return self._state

    def last_error(self) -> str:
        return self._last_error

    def _set_running(self, running: bool) -> None:
        self._running = running

    def _set_state(self, state: SourceState, status_text: str | None = None) -> None:
        self._state = state
        self.status_changed.emit(status_text or state.value)

    def _set_error(self, message: str) -> None:
        self._last_error = message
        self._set_running(False)
        self._set_state(SourceState.ERROR, "Error")
        self.error_occurred.emit(message)
        self.diagnostics_changed.emit({"last_error": message})
