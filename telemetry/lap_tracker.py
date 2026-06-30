from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from time import time
from uuid import uuid4

from PySide6.QtCore import QObject, Signal

from models import LapResult, SectorResult, TelemetrySample
from telemetry.lap_storage import LapStorage


MIN_REASONABLE_LAP_MS = 10_000
SECTOR_SUM_TOLERANCE_MS = 250


class TimingState(str, Enum):
    DISCONNECTED = "DISCONNECTED"
    WAITING_FOR_GAME = "WAITING_FOR_GAME"
    WAITING_FOR_SESSION = "WAITING_FOR_SESSION"
    WAITING_FOR_LAP = "WAITING_FOR_LAP"
    PARTIAL_LAP = "PARTIAL_LAP"
    TRACKING_LAP = "TRACKING_LAP"
    LAP_COMPLETED = "LAP_COMPLETED"
    IN_PITS = "IN_PITS"
    ERROR = "ERROR"


@dataclass(slots=True)
class TimingReference:
    compare_against: str = "personal_best"
    purple_scope: str = "current_session"


@dataclass(slots=True)
class StorageStatus:
    database_path: str = ""
    database_available: bool = False
    active_session_saved: bool = False
    active_lap_samples: int = 0
    completed_laps_in_memory: int = 0
    completed_laps_on_disk: int = 0
    last_save_timestamp: float | None = None
    last_save_result: str = "No save attempted"
    last_save_error: str = ""
    pending_operations: int = 0


class InMemoryLapRepository:
    def __init__(self) -> None:
        self._laps: list[LapResult] = []
        self._reference_lap_id: str | None = None

    def clear(self) -> None:
        self._laps.clear()
        self._reference_lap_id = None

    def add_completed_lap(self, lap: LapResult) -> None:
        if any(existing.id == lap.id for existing in self._laps):
            return
        self._laps.insert(0, lap)

    def get_session_laps(self, session_id: str) -> list[LapResult]:
        return [lap for lap in self._laps if lap.session_id == session_id]

    def get_lap(self, lap_id: str) -> LapResult | None:
        return next((lap for lap in self._laps if lap.id == lap_id), None)

    def set_reference_lap(self, lap_id: str) -> None:
        if self.get_lap(lap_id) is None:
            raise KeyError(lap_id)
        self._reference_lap_id = lap_id

    @property
    def laps(self) -> list[LapResult]:
        return self._laps


class LapTracker(QObject):
    lap_completed = Signal(object)
    lap_updated = Signal(object)
    storage_error = Signal(str)
    timing_state_changed = Signal(str, str)
    diagnostics_changed = Signal(dict)

    def __init__(
        self,
        storage: LapStorage | None = None,
        repository: InMemoryLapRepository | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.storage = storage or LapStorage()
        self.repository = repository or InMemoryLapRepository()
        self.session_id = uuid4().hex
        self.active_lap: LapResult | None = None
        self.completed_laps = self.repository.laps
        self.reference = TimingReference()
        self.timing_state = TimingState.DISCONNECTED
        self.waiting_reason = "Waiting for telemetry source"
        self.last_event = "No timing event"
        self.storage_status = StorageStatus(database_path=str(self.storage.path))
        self._last_completed_laps: int | None = None
        self._last_position: float | None = None
        self._last_sector_index: int | None = None
        self._last_sector_start_ms: int | None = None
        self._last_current_lap_time_ms: int | None = None
        self._last_last_lap_time_ms: int | None = None
        self._completed_sector_total_ms = 0
        self._cumulative_splits_ms: dict[int, int] = {}
        self._partial_start_sector_index: int | None = None
        self._completed_lap_keys: set[tuple[str, int | str]] = set()
        self._metadata_updated = False
        self._logger = logging.getLogger(__name__)

    def start_session(self, game: str, track: str | None, car: str | None, driver_name: str | None = None) -> None:
        self.session_id = uuid4().hex
        self.repository.clear()
        self.active_lap = None
        self._last_completed_laps = None
        self._last_position = None
        self._last_sector_index = None
        self._last_sector_start_ms = None
        self._last_current_lap_time_ms = None
        self._last_last_lap_time_ms = None
        self._completed_sector_total_ms = 0
        self._cumulative_splits_ms = {}
        self._partial_start_sector_index = None
        self._completed_lap_keys.clear()
        self._metadata_updated = False
        self.storage_status = StorageStatus(database_path=str(self.storage.path))
        try:
            self.storage.ensure_session(
                self.session_id,
                game=game,
                track=track,
                car=car,
                driver_name=driver_name,
                source_type="live",
                started_at=datetime.now().isoformat(timespec="milliseconds"),
            )
            self.storage_status.database_available = True
            self.storage_status.active_session_saved = True
        except Exception as error:  # pragma: no cover - defensive storage boundary.
            self.storage_status.last_save_error = str(error)
            self.storage_error.emit(str(error))
        self._set_timing_state(TimingState.WAITING_FOR_LAP, "Waiting: no lap timing sample received")
        self._emit_diagnostics()

    def process_sample(self, sample: TelemetrySample) -> LapResult | None:
        self._update_session_metadata_from_sample(sample)
        if sample.in_pit is True:
            if self.active_lap is not None:
                self.active_lap.valid = False
                self.active_lap.notes = append_note(self.active_lap.notes, "Entered pits before completion")
            self._remember_sample_state(sample)
            self._set_timing_state(TimingState.IN_PITS, "Waiting: player is in pit or garage")
            self._emit_diagnostics()
            return None

        if not self._sample_has_lap_timing(sample):
            self._remember_sample_state(sample)
            self._set_timing_state(TimingState.WAITING_FOR_LAP, self._waiting_reason(sample))
            self._emit_diagnostics()
            return None

        if self.active_lap is None:
            self._start_lap(sample, incomplete_first=True)

        assert self.active_lap is not None
        self.active_lap.samples.append(sample)
        if sample.invalid_lap is True or sample.lap_valid is False:
            self.active_lap.valid = False

        completed = self._detect_completion(sample)
        self._last_position = sample.normalized_track_position
        if self.active_lap is None:
            self._remember_sample_state(sample)
            self._emit_diagnostics()
            return None

        if completed:
            if self.active_lap.notes == "Started mid-lap":
                self._finalize_partial_lap(sample)
                self._start_lap(sample, incomplete_first=False)
                self._remember_sample_state(sample)
                self._emit_diagnostics()
                return None
            lap = self._complete_active_lap(sample)
            if lap is not None:
                self.lap_completed.emit(lap)
                self._start_lap(sample, incomplete_first=False)
                self._remember_sample_state(sample)
                self._emit_diagnostics()
                return lap

        self._update_sector_state(sample)

        self._set_timing_state(
            TimingState.PARTIAL_LAP if self.active_lap.notes == "Started mid-lap" else TimingState.TRACKING_LAP,
            self._tracking_reason(sample),
        )
        self._remember_sample_state(sample)
        self._emit_diagnostics()
        self.lap_updated.emit(self.active_lap)
        return None

    def stop_session(self) -> None:
        if self.active_lap is not None and self.active_lap.samples:
            self.active_lap.complete = False
            self.active_lap.completed_at = datetime.now().isoformat(timespec="milliseconds")
            self._save_lap(self.active_lap)
            self.active_lap = None
        self._set_timing_state(TimingState.DISCONNECTED, "Waiting for telemetry source")
        self._emit_diagnostics()

    def _start_lap(self, sample: TelemetrySample, incomplete_first: bool) -> None:
        lap_number = sample.lap_number
        if lap_number is None and sample.completed_laps is not None:
            lap_number = int(sample.completed_laps) + 1
        if lap_number is None:
            lap_number = len(self.completed_laps) + 1

        partial_first = self._is_partial_first_sample(sample, incomplete_first)
        self.active_lap = LapResult(
            lap_number=int(lap_number),
            valid=not bool(sample.invalid_lap),
            complete=False,
            game=sample.source_name or "",
            track=sample.track_name or None,
            car=sample.car_name or None,
            session_id=self.session_id,
            fully_observed=not partial_first,
            raw_samples_recorded=False,
            started_at=datetime.now().isoformat(timespec="milliseconds"),
            notes="Started mid-lap" if partial_first else "",
        )
        self._last_sector_index = sample.current_sector_index
        self._last_sector_start_ms = sample.current_lap_time_ms
        self._completed_sector_total_ms = 0
        self._cumulative_splits_ms = {}
        self._partial_start_sector_index = sample.current_sector_index if partial_first else None
        self.last_event = "Partial lap started" if partial_first else "Lap started"
        self._set_timing_state(
            TimingState.PARTIAL_LAP if partial_first else TimingState.TRACKING_LAP,
            self._tracking_reason(sample, partial_first=partial_first),
        )

    @staticmethod
    def _is_partial_first_sample(sample: TelemetrySample, incomplete_first: bool) -> bool:
        if not incomplete_first:
            return False
        if sample.current_sector_index not in (None, 0):
            return True
        return bool(sample.current_lap_time_ms and sample.current_lap_time_ms > 2000)

    def _detect_completion(self, sample: TelemetrySample) -> bool:
        if sample.completed_laps is not None:
            if self._last_completed_laps is None:
                self._last_completed_laps = int(sample.completed_laps)
                return False
            if int(sample.completed_laps) > self._last_completed_laps:
                self._last_completed_laps = int(sample.completed_laps)
                return self._completion_key_is_new(sample, int(sample.completed_laps))

            if int(sample.completed_laps) < self._last_completed_laps:
                self._last_completed_laps = int(sample.completed_laps)
                self._reset_active_lap("Session restart or lap counter reset")
                return False

        if (
            sample.last_lap_time_ms is not None
            and sample.last_lap_time_ms != self._last_last_lap_time_ms
            and sample.current_sector_index == 0
            and self._last_sector_index == 2
        ):
            return self._completion_key_is_new(sample, f"last-{sample.last_lap_time_ms}")

        position = sample.normalized_track_position
        if position is not None and self._last_position is not None and self._last_current_lap_time_ms is not None:
            if self._last_position > 0.95 and position < 0.05:
                timer_reset = sample.current_lap_time_ms is not None and sample.current_lap_time_ms < self._last_current_lap_time_ms
                final_sector = self._last_sector_index == 2 and sample.current_sector_index in (0, None)
                if timer_reset and final_sector:
                    return self._completion_key_is_new(sample, f"wrap-{sample.timestamp:.3f}")
        return False

    def _completion_key_is_new(self, sample: TelemetrySample, lap_marker: int | str) -> bool:
        key = (self.session_id, lap_marker)
        if key in self._completed_lap_keys:
            return False
        self._completed_lap_keys.add(key)
        return True

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
        self._validate_completed_sector_timing(lap)
        self._apply_timing_colors(lap)
        self.repository.add_completed_lap(lap)
        self.last_event = f"Lap completed: lap={lap.lap_number} time_ms={lap.lap_time_ms}"
        self._set_timing_state(TimingState.LAP_COMPLETED, self.last_event)
        self._save_lap(lap)
        return lap

    def _finalize_partial_lap(self, sample: TelemetrySample) -> None:
        if self.active_lap is None:
            return
        self.active_lap.complete = False
        self.active_lap.valid = False
        self.active_lap.fully_observed = False
        self.active_lap.completed_at = datetime.now().isoformat(timespec="milliseconds")
        self.active_lap.notes = append_note(self.active_lap.notes, "First finish after connection; not saved as complete")
        self.last_event = "Partial lap discarded at first confirmed finish"

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
        self._append_completed_sector(int(self._last_sector_index), sample)
        self._last_sector_index = sector_index
        self._last_sector_start_ms = sample.current_lap_time_ms

    def _finalize_open_sector(self, lap: LapResult, sample: TelemetrySample) -> None:
        if self._last_sector_index is None:
            return
        self._append_completed_sector(int(self._last_sector_index), sample, lap=lap, lap_time_ms=lap.lap_time_ms)

    def _append_completed_sector(
        self,
        sector_index: int,
        sample: TelemetrySample,
        lap: LapResult | None = None,
        lap_time_ms: int | None = None,
    ) -> None:
        target_lap = lap or self.active_lap
        if target_lap is None:
            return
        sector_number = sector_index + 1
        if sector_number in {sector.sector_number for sector in target_lap.sectors}:
            return
        sector_time, source = self._sector_time_from_sample(sector_index, sample, lap_time_ms)
        if sector_time is not None:
            self._completed_sector_total_ms += sector_time
        if source == "acc_cumulative_split" and sample.current_split_time_ms is not None and sector_index in (0, 1):
            self._cumulative_splits_ms[sector_index + 1] = int(sample.current_split_time_ms)
        target_lap.sectors.append(
            SectorResult(
                sector_number=sector_number,
                end_distance_m=sample.lap_distance,
                time_ms=sector_time,
                valid=target_lap.valid,
                timing_source=source,
            )
        )
        self.last_event = f"Sector completed: S{sector_number} source={source} time_ms={sector_time}"

    def _sector_time_from_sample(
        self,
        sector_index: int,
        sample: TelemetrySample,
        lap_time_ms: int | None = None,
    ) -> tuple[int | None, str]:
        if self._partial_start_sector_index == sector_index:
            return None, "partial_lap_unavailable"
        if sector_index in (0, 1):
            cumulative_split_ms = sample.current_split_time_ms
            if cumulative_split_ms is not None:
                previous_cumulative_ms = self._cumulative_splits_ms.get(sector_index, 0)
                if cumulative_split_ms > previous_cumulative_ms:
                    return cumulative_split_ms - previous_cumulative_ms, "acc_cumulative_split"
        if sector_index == 2:
            final_lap_time = sample.last_lap_time_ms or lap_time_ms
            cumulative_split_2_ms = self._cumulative_splits_ms.get(2)
            if final_lap_time is not None and cumulative_split_2_ms is not None and final_lap_time > cumulative_split_2_ms:
                return final_lap_time - cumulative_split_2_ms, "acc_cumulative_split"
            if final_lap_time is not None and final_lap_time > self._completed_sector_total_ms:
                return final_lap_time - self._completed_sector_total_ms, "sector_transition_derived"
        if sample.last_sector_time_ms is not None and sample.last_sector_time_ms > 0:
            if lap_time_ms is None or self._completed_sector_total_ms + sample.last_sector_time_ms <= lap_time_ms + SECTOR_SUM_TOLERANCE_MS:
                return sample.last_sector_time_ms, "acc_direct_sector"
        start_ms = self._last_sector_start_ms
        current_ms = sample.current_lap_time_ms
        if start_ms is not None and current_ms is not None and current_ms >= start_ms:
            return current_ms - start_ms, "sector_transition_derived"
        return None, "unavailable"

    def _validate_completed_sector_timing(self, lap: LapResult) -> None:
        if lap.lap_time_ms is None:
            return
        sectors = sorted(lap.sectors, key=lambda sector: sector.sector_number)
        if len(sectors) < 3:
            return
        first_three = sectors[:3]
        sector_times = [sector.time_ms for sector in first_three]
        if any(time_ms is None or time_ms <= 0 for time_ms in sector_times):
            lap.notes = append_note(lap.notes, "Sector timing inconsistent; sector values unavailable")
            for sector in first_three:
                if sector.time_ms is None or sector.time_ms <= 0:
                    sector.valid = False
                    sector.timing_source = "unavailable"
            return
        sector_sum_ms = sum(int(time_ms) for time_ms in sector_times if time_ms is not None)
        if abs(sector_sum_ms - lap.lap_time_ms) <= SECTOR_SUM_TOLERANCE_MS:
            return
        self._logger.warning(
            "Rejecting inconsistent sector timing: lap_id=%s lap_time_ms=%s sectors=%s sum=%s",
            lap.id,
            lap.lap_time_ms,
            sector_times,
            sector_sum_ms,
        )
        lap.notes = append_note(lap.notes, "Sector timing inconsistent; sector values unavailable")
        for sector in first_three:
            sector.time_ms = None
            sector.valid = False
            sector.comparison_status = "neutral"
            sector.timing_source = "unavailable"

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
            self.storage_status.pending_operations += 1
            self._logger.info(
                "Saving completed lap: session_id=%s lap_id=%s lap_number=%s lap_time_ms=%s sector_count=%s sample_count=%s",
                lap.session_id,
                lap.id,
                lap.lap_number,
                lap.lap_time_ms,
                len(lap.sectors),
                len(lap.samples),
            )
            self.storage.save_lap(lap, include_samples=lap.raw_samples_recorded)
            self.storage_status.database_available = True
            self.storage_status.last_save_timestamp = time()
            self.storage_status.last_save_result = "Lap saved successfully"
            self.storage_status.last_save_error = ""
            self.storage_status.completed_laps_on_disk = len(self.storage.load_laps())
            self._logger.info("Lap saved successfully: lap_id=%s", lap.id)
        except Exception as error:  # pragma: no cover - defensive storage boundary.
            self.storage_status.last_save_timestamp = time()
            self.storage_status.last_save_result = "Lap save failed"
            self.storage_status.last_save_error = str(error)
            self._logger.exception("Lap save failed: %s", error)
            self.storage_error.emit(str(error))
        finally:
            self.storage_status.pending_operations = max(0, self.storage_status.pending_operations - 1)

    def _update_session_metadata_from_sample(self, sample: TelemetrySample) -> None:
        if self._metadata_updated:
            return
        if not (sample.track_name or sample.car_name or sample.source_name):
            return
        try:
            self.storage.update_session_metadata(
                self.session_id,
                game=sample.source_name or None,
                track=sample.track_name or None,
                car=sample.car_name or None,
            )
            self._metadata_updated = True
        except Exception as error:  # pragma: no cover - defensive storage boundary.
            self.storage_status.last_save_error = str(error)
            self.storage_error.emit(str(error))

    def _sample_has_lap_timing(self, sample: TelemetrySample) -> bool:
        return (
            sample.current_lap_time_ms is not None
            or sample.last_lap_time_ms is not None
            or sample.completed_laps is not None
            or sample.current_sector_index is not None
        )

    def _waiting_reason(self, sample: TelemetrySample) -> str:
        if not sample.source_name:
            return "Waiting: no telemetry source sample"
        if sample.session_state and sample.session_state not in {"Live", "Paused"}:
            return f"Waiting: ACC session state is {sample.session_state}"
        if sample.current_lap_time_ms is None:
            return "Waiting: current lap timer has not started"
        if sample.current_sector_index is None:
            return "Waiting: ACC sector index is unavailable"
        return "Waiting: no valid session identity"

    def _tracking_reason(self, sample: TelemetrySample, partial_first: bool = False) -> str:
        sector = "--" if sample.current_sector_index is None else str(int(sample.current_sector_index) + 1)
        if partial_first:
            return f"Partial lap - sectors before connection are unavailable; sector {sector}"
        return f"Tracking lap {self.active_lap.lap_number if self.active_lap else '--'} - sector {sector}"

    def _reset_active_lap(self, reason: str) -> None:
        self.active_lap = None
        self._last_sector_index = None
        self._last_sector_start_ms = None
        self._completed_sector_total_ms = 0
        self._cumulative_splits_ms = {}
        self._partial_start_sector_index = None
        self.last_event = reason
        self._set_timing_state(TimingState.WAITING_FOR_SESSION, f"Waiting: {reason}")

    def _remember_sample_state(self, sample: TelemetrySample) -> None:
        self._last_current_lap_time_ms = sample.current_lap_time_ms
        self._last_last_lap_time_ms = sample.last_lap_time_ms
        self.storage_status.active_lap_samples = len(self.active_lap.samples) if self.active_lap else 0
        self.storage_status.completed_laps_in_memory = len(self.repository.laps)

    def _set_timing_state(self, state: TimingState, reason: str) -> None:
        changed = state != self.timing_state or reason != self.waiting_reason
        self.timing_state = state
        self.waiting_reason = reason
        if changed:
            self.timing_state_changed.emit(state.value, reason)

    def _emit_diagnostics(self) -> None:
        self.diagnostics_changed.emit(
            {
                "timing_state": self.timing_state.value,
                "timing_waiting_reason": self.waiting_reason,
                "current_session_id": self.session_id,
                "current_active_lap_id": self.active_lap.id if self.active_lap else "",
                "active_lap_samples": self.storage_status.active_lap_samples,
                "completed_laps_in_memory": self.storage_status.completed_laps_in_memory,
                "database_path": self.storage_status.database_path,
                "database_available": self.storage_status.database_available,
                "completed_laps_on_disk": self.storage_status.completed_laps_on_disk,
                "last_save_result": self.storage_status.last_save_result,
                "last_save_error": self.storage_status.last_save_error,
                "pending_storage_operations": self.storage_status.pending_operations,
                "last_timing_event": self.last_event,
            }
        )


def same_scope(a: LapResult, b: LapResult) -> bool:
    return a.track == b.track and a.car == b.car and a.driver_name == b.driver_name


def personal_best_time(laps: list[LapResult], lap: LapResult) -> int:
    return min([other.lap_time_ms for other in laps if same_scope(other, lap) and other.lap_time_ms is not None], default=10**12)


def append_note(existing: str, note: str) -> str:
    if not existing:
        return note
    if note in existing:
        return existing
    return f"{existing}; {note}"
