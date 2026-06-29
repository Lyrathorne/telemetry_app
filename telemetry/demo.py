import math
import time

from PySide6.QtCore import QObject, QTimer

from models import TelemetrySample
from telemetry.base import SourceState, TelemetrySource


class DemoTelemetrySource(TelemetrySource):
    source_id = "demo"
    display_name = "Demo"

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._timer = QTimer(self)
        self._timer.setInterval(75)
        self._timer.timeout.connect(self._emit_sample)
        self._start_time = 0.0

    def start(self) -> None:
        if self.is_running():
            return

        self._start_time = time.monotonic()
        self._set_running(True)
        self._set_state(SourceState.CONNECTED, "Connected")
        self.diagnostics_changed.emit({"updates_per_second": "--", "last_error": ""})
        self._timer.start()

    def stop(self) -> None:
        if self._timer.isActive():
            self._timer.stop()

        if self.is_running():
            self._set_state(SourceState.STOPPED, "Stopped")

        self._set_running(False)

    def _emit_sample(self) -> None:
        elapsed = time.monotonic() - self._start_time
        wave = (math.sin(elapsed * 1.7) + 1.0) / 2.0
        throttle = max(0.0, min(100.0, 18.0 + wave * 82.0))
        brake = max(0.0, math.sin(elapsed * 0.9 - 2.1) * 45.0)
        speed = max(0.0, 45.0 + math.sin(elapsed * 0.45) * 35.0 + throttle * 2.2 - brake)
        gear = self._gear_for_speed(speed)
        rpm = int(900 + min(speed, 340.0) * 38 + throttle * 35)

        self.sample_received.emit(
            TelemetrySample(
                speed_kmh=speed,
                rpm=rpm,
                gear=gear,
                throttle_percent=throttle,
                brake_percent=brake,
                source_name=self.display_name,
                session_state="Demo",
                timestamp=time.time(),
            )
        )

    @staticmethod
    def _gear_for_speed(speed_kmh: float) -> int:
        if speed_kmh < 2.0:
            return 0

        return max(1, min(8, int(speed_kmh // 45) + 1))
