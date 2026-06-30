from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from uuid import uuid4

from PySide6.QtCore import QObject, Signal

from models import LapResult, SectorResult, TelemetrySample
from telemetry.lap_storage import LapStorage


MIN_REASONABLE_LAP_MS = 10_000


@dataclass(slots=True)
class TimingReference:
    compare_against: str = "personal_best"
    purple_scope: str = "current_session"


class LapTracker(QObject):
    lap_completed = Signal(object)
    lap_updated = Signal(object)
    storage_error = Signal(str)

    def __init__(self, storage: LapStorage | None = None, parent=None) -> None:
        super().__init__(parent)
        self.storage = storage or LapStorage()
        self.session_id = uuid4().hex
        self.active_lap: LapResult | None = None
        self.completed_laps: list[LapResult] = []
        self.reference = TimingReference()
        self._last_completed_laps: int | None = None
        self._last_position: float | None = None
        self._last_sector_index: int | None = None
        self._last_sector_start_ms: int | None = None

    def start_session(self, game: str, track: str | None, car: str | None, driver_name: str | None = None) -> None:
        self.session_id = uuid4().hex
        self.completed_laps.clear()
        self.active_lap = None
        self._last_completed_laps = None
        self._last_position = None
        self._last_sector_index = None
        self._last_sector_start_ms = None
        self.storage.ensure_session(
            self.session_id,
            game=game,
            track=track,
            car=car,
            driver_name=driver_name,
            source_type="live",
            started_at=datetime.now().isoformat(timespec="milliseconds"),
        )

    def process_sample(self, sample: TelemetrySample) -> LapResult | None:
        if self.active_lap is None:
            self._start_lap(sample, incomplete_first=True)

        assert self.active_lap is not None
        self.active_lap.samples.append(sample)
        if sample.invalid_lap is True:
            self.active_lap.valid = False

        self._update_sector_state(sample)
        completed = self._detect_completion(sample)
        self._last_position = sample.normalized_track_position

        if completed:
            lap = self._complete_active_lap(sample)
            if lap is not None:
                self.lap_completed.emit(lap)
                self._start_lap(sample, incomplete_first=False)
                return lap

        self.lap_updated.emit(self.active_lap)
        return None

    def stop_session(self) -> None:
        if self.active_lap is not None and self.active_lap.samples:
            self.active_lap.complete = False
            self.active_lap.completed_at = datetime.now().isoformat(timespec="milliseconds")
            self._save_lap(self.active_lap)
            self.active_lap = None

    def _start_lap(self, sample: TelemetrySample, incomplete_first: bool) -> None:
        lap_number = sample.lap_number
        if lap_number is None and sample.completed_laps is not None:
            lap_number = int(sample.completed_laps) + 1
        if lap_number is None:
            lap_number = len(self.completed_laps) + 1

        self.active_lap = LapResult(
            lap_number=int(lap_number),
            valid=not bool(sample.invalid_lap),
            complete=False,
            game=sample.source_name or "",
            track=sample.track_name or None,
            car=sample.car_name or None,
            session_id=self.session_id,
            started_at=datetime.now().isoformat(timespec="milliseconds"),
            notes="Started mid-lap" if incomplete_first else "",
        )
        self._last_sector_index = sample.current_sector_index
        self._last_sector_start_ms = sample.current_lap_time_ms

    def _detect_completion(self, sample: TelemetrySample) -> bool:
        if sample.completed_laps is not None:
            if self._last_completed_laps is None:
                self._last_completed_laps = int(sample.completed_laps)
                return False
            if int(sample.completed_laps) > self._last_completed_laps:
                self._last_completed_laps = int(sample.completed_laps)
                return True

        position = sample.normalized_track_position
        if position is not None and self._last_position is not None:
            if self._last_position > 0.95 and position < 0.05:
                return True
        return False

    def _complete_active_lap(self, sample: TelemetrySample) -> LapResult | None:
        if self.active_lap is None:
            return None
        lap = self.active_lap
        lap_time_ms = sample.last_lap_time_ms or sample.current_lap_time_ms
        if lap_time_ms is None and len(lap.samples) >= 2:
            lap_time_ms = int(max(0.0, lap.samples[-1].timestamp - lap.samples[0].timestamp) * 1000)
        if lap_time_ms is not None and lap_time_ms < MIN_REASONABLE_LAP_MS:
            return None

        lap.lap_time_ms = lap_time_ms
        lap.complete = True
        lap.completed_at = datetime.now().isoformat(timespec="milliseconds")
        self._finalize_open_sector(lap, sample)
        self._apply_timing_colors(lap)
        self.completed_laps.append(lap)
        self._save_lap(lap)
        return lap

    def _update_sector_state(self, sample: TelemetrySample) -> None:
        if self.active_lap is None or sample.current_sector_index is None:
            return
        sector_index = int(sample.current_sector_index)
        if self._last_sector_index is None:
            self._last_sector_index = sector_index
            self._last_sector_start_ms = sample.current_lap_time_ms
            return
        if sector_index == self._last_sector_index:
            return
        start_ms = self._last_sector_start_ms
        current_ms = sample.current_lap_time_ms
        sector_time = None
        if start_ms is not None and current_ms is not None and current_ms >= start_ms:
            sector_time = current_ms - start_ms
        self.active_lap.sectors.append(
            SectorResult(
                sector_number=int(self._last_sector_index) + 1,
                end_distance_m=sample.lap_distance,
                time_ms=sector_time,
                valid=self.active_lap.valid,
            )
        )
        self._last_sector_index = sector_index
        self._last_sector_start_ms = current_ms

    def _finalize_open_sector(self, lap: LapResult, sample: TelemetrySample) -> None:
        if self._last_sector_index is None:
            return
        existing = {sector.sector_number for sector in lap.sectors}
        sector_number = int(self._last_sector_index) + 1
        if sector_number in existing:
            return
        start_ms = self._last_sector_start_ms
        end_ms = sample.last_lap_time_ms or sample.current_lap_time_ms or lap.lap_time_ms
        sector_time = None
        if start_ms is not None and end_ms is not None and end_ms >= start_ms:
            sector_time = end_ms - start_ms
        lap.sectors.append(
            SectorResult(
                sector_number=sector_number,
                end_distance_m=sample.lap_distance,
                time_ms=sector_time,
                valid=lap.valid,
            )
        )

    def _apply_timing_colors(self, lap: LapResult) -> None:
        if not lap.valid or lap.lap_time_ms is None:
            for sector in lap.sectors:
                sector.comparison_status = "neutral"
            return

        valid_laps = [
            other for other in self.completed_laps
            if other.valid and other.lap_time_ms is not None and same_scope(other, lap)
        ]
        best_time = min([other.lap_time_ms for other in valid_laps], default=None)
        if best_time is None:
            status = "neutral"
        elif lap.lap_time_ms < best_time:
            status = "purple"
        elif lap.lap_time_ms < personal_best_time(valid_laps, lap):
            status = "green"
        else:
            status = "yellow"

        for sector in lap.sectors:
            sector.comparison_status = status if sector.valid else "neutral"

    def _save_lap(self, lap: LapResult) -> None:
        try:
            self.storage.save_lap(lap)
        except Exception as error:  # pragma: no cover - defensive storage boundary.
            self.storage_error.emit(str(error))


def same_scope(a: LapResult, b: LapResult) -> bool:
    return a.track == b.track and a.car == b.car and a.driver_name == b.driver_name


def personal_best_time(laps: list[LapResult], lap: LapResult) -> int:
    return min([other.lap_time_ms for other in laps if same_scope(other, lap) and other.lap_time_ms is not None], default=10**12)
