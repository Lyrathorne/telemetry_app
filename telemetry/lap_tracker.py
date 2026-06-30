from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from time import time
from uuid import uuid4

from PySide6.QtCore import QObject, Signal

from models import LapResult, LapTelemetrySeries, SectorResult, TelemetrySample
from telemetry.lap_storage import LapStorage
from telemetry.timing_status import (
    TIMING_STATUS_GREEN,
    TIMING_STATUS_INVALID,
    TIMING_STATUS_NEUTRAL,
    TIMING_STATUS_PURPLE,
    TIMING_STATUS_UNAVAILABLE,
    TIMING_STATUS_YELLOW,
    recalculate_sector_statuses,
    same_scope,
)


MIN_REASONABLE_LAP_MS = 10_000
SECTOR_SUM_TOLERANCE_MS = 250
MAX_SAVED_LAP_SAMPLES = 1000


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
    def __init__(self, max_full_telemetry_laps: int = 20) -> None:
        self._laps: list[LapResult] = []
        self._reference_lap_id: str | None = None
        self.max_full_telemetry_laps = max(1, max_full_telemetry_laps)

    def clear(self) -> None:
        self._laps.clear()
        self._reference_lap_id = None

    def add_completed_lap(self, lap: LapResult) -> None:
        if any(existing.id == lap.id for existing in self._laps):
            return
        self._laps.insert(0, lap)
        self._evict_old_graphs()

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

    def completed_graphs(self) -> list[LapTelemetrySeries]:
        return [lap.telemetry_series for lap in self._laps if lap.telemetry_series is not None]

    def _evict_old_graphs(self) -> None:
        graphs = [lap for lap in self._laps if lap.telemetry_series is not None]
        for lap in graphs[self.max_full_telemetry_laps:]:
            lap.telemetry_series = None


class LapGraphBuffer:
    def __init__(self, lap_id: str, lap_number: int, fully_observed: bool, valid: bool) -> None:
        self.lap_id = lap_id
        self.lap_number = lap_number
        self.fully_observed = fully_observed
        self.valid = valid
        self._start_time: float | None = None
        self._samples: list[TelemetrySample] = []
        self.last_reset_reason = "Lap buffer created"

    @property
    def sample_count(self) -> int:
        return len(self._samples)

    @property
    def start_time(self) -> float | None:
        return self._start_time

    def add_sample(self, sample: TelemetrySample) -> None:
        if self._start_time is None:
            self._start_time = sample.timestamp
        self._samples.append(sample)

    def freeze(self, lap_time_ms: int | None, sector_boundary_times_ms: list[int]) -> LapTelemetrySeries:
        start_time = self._start_time
        elapsed: list[float] = []
        for sample in self._samples:
            if sample.current_lap_time_ms is not None:
                elapsed.append(max(0.0, sample.current_lap_time_ms / 1000.0))
            elif start_time is not None:
                elapsed.append(max(0.0, float(sample.timestamp) - float(start_time)))
            else:
                elapsed.append(0.0)
        return LapTelemetrySeries(
            lap_id=self.lap_id,
            lap_number=self.lap_number,
            lap_time_ms=lap_time_ms,
            fully_observed=self.fully_observed,
            valid=self.valid,
            elapsed_time_s=elapsed,
            lap_distance_m=[sample.lap_distance for sample in self._samples],
            normalized_position=[sample.normalized_track_position for sample in self._samples],
            speed_kmh=[sample.speed_kmh for sample in self._samples],
            rpm=[sample.rpm for sample in self._samples],
            gear=[sample.gear for sample in self._samples],
            throttle_percent=[sample.throttle_percent for sample in self._samples],
            brake_percent=[sample.brake_percent for sample in self._samples],
            clutch_percent=[sample.clutch_percent for sample in self._samples],
            steering=[sample.steering for sample in self._samples],
            sector_boundary_elapsed_s=[boundary / 1000.0 for boundary in sector_boundary_times_ms],
        )


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
        self.current_lap_graph: LapGraphBuffer | None = None
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
        self._last_frozen_lap_graph_id = ""
        self._last_graph_reset_reason = "No graph reset"
        self._timing_diagnostics_enabled = os.environ.get("RACING_TELEMETRY_ACC_TIMING_DIAGNOSTICS") == "1"
        self._logger = logging.getLogger(__name__)

    def start_session(self, game: str, track: str | None, car: str | None, driver_name: str | None = None) -> None:
        self.session_id = uuid4().hex
        self.repository.clear()
        self.active_lap = None
        self.current_lap_graph = None
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
        self._last_frozen_lap_graph_id = ""
        self._last_graph_reset_reason = "Session started"
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
        if self.current_lap_graph is not None:
            self.current_lap_graph.add_sample(sample)
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
                self._start_lap(sample, incomplete_first=False)
                self.lap_completed.emit(lap)
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
        ended_at = datetime.now().isoformat(timespec="milliseconds")
        if self.active_lap is not None and self.active_lap.samples:
            self.active_lap.complete = False
            self.active_lap.completed_at = ended_at
            self._save_lap(self.active_lap)
            self.active_lap = None
        try:
            self.storage.end_session(self.session_id, ended_at)
        except Exception as error:  # pragma: no cover - defensive storage boundary.
            self.storage_status.last_save_error = str(error)
            self.storage_error.emit(str(error))
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
        self.current_lap_graph = LapGraphBuffer(
            lap_id=self.active_lap.id,
            lap_number=self.active_lap.lap_number,
            fully_observed=self.active_lap.fully_observed,
            valid=self.active_lap.valid,
        )
        self._last_graph_reset_reason = "Confirmed lap completion" if not incomplete_first else "First lap buffer started"
        self._last_sector_index = sample.current_sector_index
        self._last_sector_start_ms = sample.current_lap_time_ms
        self._completed_sector_total_ms = 0
        self._cumulative_splits_ms = {}
        self._partial_start_sector_index = sample.current_sector_index if partial_first else None
        self.last_event = "Partial lap started" if partial_first else "Lap started"
        self._logger.info("[Timing] Lap started: lap=%s sector=%s", self.active_lap.lap_number, sample.current_sector_index)
        self._log_timing_debug(
            "Lap Started",
            sample,
            extra=f"lap_start_time_ms={self._last_sector_start_ms}",
        )
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
        self._ensure_complete_sector_set(lap)
        self._validate_completed_sector_timing(lap)
        self._freeze_lap_graph(lap)
        lap.samples = downsample_samples(lap.samples, MAX_SAVED_LAP_SAMPLES)
        lap.raw_samples_recorded = True
        self.repository.add_completed_lap(lap)
        self._recalculate_sector_timing_statuses(lap)
        self.last_event = f"Lap completed: lap={lap.lap_number} time_ms={lap.lap_time_ms}"
        self._logger.info(
            "[Timing] Lap completed: lap=%s lap_time=%s s1=%s s2=%s s3=%s",
            lap.lap_number,
            lap.lap_time_ms,
            sector_time(lap, 1),
            sector_time(lap, 2),
            sector_time(lap, 3),
        )
        self._set_timing_state(TimingState.LAP_COMPLETED, self.last_event)
        self._save_lap(lap)
        return lap

    def _finalize_partial_lap(self, sample: TelemetrySample) -> None:
        if self.active_lap is None:
            return
        self.active_lap.complete = False
        self.active_lap.valid = False
        self.active_lap.fully_observed = False
        self._freeze_lap_graph(self.active_lap)
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
        self._logger.info("[Timing] Sector changed: old=%s new=%s", self._last_sector_index, sector_index)
        self._append_completed_sector(int(self._last_sector_index), sample)
        self._last_sector_index = sector_index
        self._last_sector_start_ms = sample.current_lap_time_ms
        self._log_timing_debug(f"Entered Sector {sector_index + 1}", sample)

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
            self._logger.warning(
                "[Timing] Duplicate sector event ignored: lap=%s sector=%s",
                target_lap.lap_number,
                sector_number,
            )
            return
        sector_time, source = self._sector_time_from_sample(sector_index, sample, lap_time_ms)
        if sector_time is not None:
            self._completed_sector_total_ms += sector_time
        boundary_lap_time_ms = self._boundary_lap_time_ms(sector_index, sample, lap_time_ms)
        if boundary_lap_time_ms is not None and sector_index in (0, 1):
            self._cumulative_splits_ms[sector_index + 1] = int(boundary_lap_time_ms)
        target_lap.sectors.append(
            SectorResult(
                sector_number=sector_number,
                end_distance_m=sample.lap_distance,
                time_ms=sector_time,
                valid=target_lap.valid,
                timing_source=source,
            )
        )
        self._logger.info(
            "[Timing] Sector recorded: lap=%s sector=%s time=%s source=%s",
            target_lap.lap_number,
            sector_number,
            sector_time,
            source,
        )
        self.last_event = f"Sector completed: S{sector_number} source={source} time_ms={sector_time}"
        self._log_timing_debug(
            "Lap Finished" if sector_index == 2 else f"Sector {sector_number} completed",
            sample,
            extra=(
                f"Sector{sector_number}={sector_time} source={source} "
                f"boundary_ms={boundary_lap_time_ms} lap_ms={lap_time_ms}"
            ),
        )

    def _sector_time_from_sample(
        self,
        sector_index: int,
        sample: TelemetrySample,
        lap_time_ms: int | None = None,
    ) -> tuple[int | None, str]:
        if self._partial_start_sector_index == sector_index:
            return None, "partial_lap_unavailable"
        if sample.last_sector_time_ms is not None and sample.last_sector_time_ms > 0:
            if self._direct_sector_time_is_plausible(sector_index, int(sample.last_sector_time_ms), sample, lap_time_ms):
                return int(sample.last_sector_time_ms), "acc_direct_sector"
        if sector_index in (0, 1):
            boundary_lap_time_ms = self._boundary_lap_time_ms(sector_index, sample, lap_time_ms)
            if boundary_lap_time_ms is not None:
                previous_boundary_ms = self._cumulative_splits_ms.get(sector_index, 0)
                if boundary_lap_time_ms > previous_boundary_ms:
                    source = (
                        "acc_cumulative_split"
                        if self._split_matches_boundary(sample.current_split_time_ms, boundary_lap_time_ms)
                        else "sector_transition_derived"
                    )
                    return boundary_lap_time_ms - previous_boundary_ms, source
        if sector_index == 2:
            final_lap_time = sample.last_lap_time_ms or lap_time_ms
            cumulative_split_2_ms = self._cumulative_splits_ms.get(2)
            if final_lap_time is not None and cumulative_split_2_ms is not None and final_lap_time > cumulative_split_2_ms:
                return final_lap_time - cumulative_split_2_ms, "sector_transition_derived"
            if final_lap_time is not None and final_lap_time > self._completed_sector_total_ms:
                return final_lap_time - self._completed_sector_total_ms, "sector_transition_derived"
        return None, "unavailable"

    def _direct_sector_time_is_plausible(
        self,
        sector_index: int,
        sector_time_ms: int,
        sample: TelemetrySample,
        lap_time_ms: int | None,
    ) -> bool:
        if sector_time_ms <= 0:
            return False
        if sector_index == 2:
            final_lap_time = sample.last_lap_time_ms or lap_time_ms
            return final_lap_time is None or self._completed_sector_total_ms + sector_time_ms <= final_lap_time + SECTOR_SUM_TOLERANCE_MS
        boundary_lap_time_ms = self._boundary_lap_time_ms(sector_index, sample, lap_time_ms)
        if boundary_lap_time_ms is None:
            return True
        previous_boundary_ms = self._cumulative_splits_ms.get(sector_index, 0)
        expected_sector_ms = boundary_lap_time_ms - previous_boundary_ms
        return abs(sector_time_ms - expected_sector_ms) <= SECTOR_SUM_TOLERANCE_MS

    def _boundary_lap_time_ms(
        self,
        sector_index: int,
        sample: TelemetrySample,
        lap_time_ms: int | None = None,
    ) -> int | None:
        if sector_index == 2:
            return sample.last_lap_time_ms or lap_time_ms
        if self._split_matches_boundary(sample.current_split_time_ms, sample.current_lap_time_ms):
            return int(sample.current_split_time_ms)
        if sample.current_lap_time_ms is not None:
            return int(sample.current_lap_time_ms)
        return None

    @staticmethod
    def _split_matches_boundary(split_ms: int | None, boundary_ms: int | None) -> bool:
        return split_ms is not None and boundary_ms is not None and abs(int(split_ms) - int(boundary_ms)) <= SECTOR_SUM_TOLERANCE_MS

    def _ensure_complete_sector_set(self, lap: LapResult) -> None:
        if lap.lap_time_ms is None:
            return
        boundaries = self._observed_sector_boundaries(lap)
        if 1 in self._cumulative_splits_ms:
            boundaries[1] = self._cumulative_splits_ms[1]
        if 2 in self._cumulative_splits_ms:
            boundaries[2] = self._cumulative_splits_ms[2]
        if 3 not in boundaries:
            boundaries[3] = int(lap.lap_time_ms)
        if 1 not in boundaries or 2 not in boundaries:
            missing = [number for number in (1, 2) if number not in boundaries]
            self._logger.warning(
                "[Timing] Missing sector boundaries before save: lap=%s missing=%s",
                lap.lap_number,
                missing,
            )
        existing = {sector.sector_number: sector for sector in lap.sectors}
        previous_boundary = 0
        for sector_number in (1, 2, 3):
            boundary = boundaries.get(sector_number)
            sector = existing.get(sector_number)
            if sector is not None and sector.time_ms is not None and sector.time_ms > 0:
                previous_boundary += int(sector.time_ms)
                continue
            if boundary is not None and boundary > previous_boundary:
                calculated = int(boundary - previous_boundary)
                if sector is None:
                    lap.sectors.append(
                        SectorResult(
                            sector_number=sector_number,
                            time_ms=calculated,
                            valid=lap.valid,
                            timing_source="normalized_sector_boundary",
                        )
                    )
                else:
                    sector.time_ms = calculated
                    sector.valid = lap.valid
                    sector.timing_source = "normalized_sector_boundary"
                self._completed_sector_total_ms += calculated if sector is None else 0
                self._logger.info(
                    "[Timing] Sector recorded: lap=%s sector=%s time=%s source=normalized_sector_boundary",
                    lap.lap_number,
                    sector_number,
                    calculated,
                )
                previous_boundary = int(boundary)
            elif sector is None:
                lap.sectors.append(
                    SectorResult(
                        sector_number=sector_number,
                        time_ms=None,
                        valid=False,
                        timing_source="unavailable",
                    )
                )
        lap.sectors.sort(key=lambda sector: sector.sector_number)

    def _observed_sector_boundaries(self, lap: LapResult) -> dict[int, int]:
        boundaries: dict[int, int] = {}
        previous_sector: int | None = None
        for sample in lap.samples:
            sector = sample.current_sector_index
            if sector is None:
                continue
            sector_index = int(sector)
            if previous_sector is None:
                previous_sector = sector_index
                continue
            if sector_index == previous_sector:
                continue
            if sector_index in (1, 2) and sample.current_lap_time_ms is not None:
                boundaries[sector_index] = int(sample.current_lap_time_ms)
            previous_sector = sector_index
        return boundaries

    def _validate_completed_sector_timing(self, lap: LapResult) -> None:
        if lap.lap_time_ms is None:
            return
        sectors = sorted(lap.sectors, key=lambda sector: sector.sector_number)
        if len(sectors) < 3:
            self._logger.warning(
                "[Timing] Incomplete sector data on completed lap: lap_id=%s sector_count=%s",
                lap.id,
                len(sectors),
            )
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
            sector.comparison_status = TIMING_STATUS_UNAVAILABLE
            sector.timing_source = "unavailable"

    def _freeze_lap_graph(self, lap: LapResult) -> None:
        if self.current_lap_graph is None:
            return
        boundaries = [
            self._cumulative_splits_ms[index]
            for index in (1, 2)
            if index in self._cumulative_splits_ms
        ]
        lap.telemetry_series = self.current_lap_graph.freeze(lap.lap_time_ms, boundaries)
        self._last_frozen_lap_graph_id = lap.id

    def _recalculate_sector_timing_statuses(self, changed_lap: LapResult) -> None:
        recalculate_sector_statuses(self.completed_laps, changed_lap)

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
        self.current_lap_graph = None
        self._last_sector_index = None
        self._last_sector_start_ms = None
        self._completed_sector_total_ms = 0
        self._cumulative_splits_ms = {}
        self._partial_start_sector_index = None
        self.last_event = reason
        self._set_timing_state(TimingState.WAITING_FOR_SESSION, f"Waiting: {reason}")

    def _log_timing_debug(self, event: str, sample: TelemetrySample, extra: str = "") -> None:
        if not self._timing_diagnostics_enabled:
            return
        lap_number = self.active_lap.lap_number if self.active_lap is not None else sample.lap_number
        sectors = {}
        if self.active_lap is not None:
            sectors = {sector.sector_number: sector.time_ms for sector in self.active_lap.sectors}
        self._logger.info(
            "ACC lap timing debug: Lap %s | %s | Timestamp=%s current_lap_time_ms=%s "
            "last_lap_time_ms=%s sector_index=%s completed_laps=%s S1=%s S2=%s S3=%s %s",
            lap_number,
            event,
            sample.timestamp,
            sample.current_lap_time_ms,
            sample.last_lap_time_ms,
            sample.current_sector_index,
            sample.completed_laps,
            sectors.get(1),
            sectors.get(2),
            sectors.get(3),
            extra,
        )

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
                "current_lap_graph_samples": self.current_lap_graph.sample_count if self.current_lap_graph else 0,
                "current_lap_graph_start_time": self.current_lap_graph.start_time if self.current_lap_graph else "",
                "last_frozen_lap_graph_id": self._last_frozen_lap_graph_id,
                "completed_graphs_in_memory": len(self.repository.completed_graphs()),
                "lap_graph_memory_limit": self.repository.max_full_telemetry_laps,
                "last_graph_reset_reason": self._last_graph_reset_reason,
            }
        )


def personal_best_time(laps: list[LapResult], lap: LapResult) -> int:
    return min([other.lap_time_ms for other in laps if same_scope(other, lap) and other.lap_time_ms is not None], default=10**12)


def sector_time(lap: LapResult, sector_number: int) -> int | None:
    sector = next((item for item in lap.sectors if item.sector_number == sector_number), None)
    return sector.time_ms if sector is not None else None


def downsample_samples(samples: list[TelemetrySample], max_samples: int) -> list[TelemetrySample]:
    if len(samples) <= max_samples:
        return list(samples)
    if max_samples <= 1:
        return samples[:1]
    step = (len(samples) - 1) / (max_samples - 1)
    indexes = sorted({round(index * step) for index in range(max_samples)})
    return [samples[index] for index in indexes]


def append_note(existing: str, note: str) -> str:
    if not existing:
        return note
    if note in existing:
        return existing
    return f"{existing}; {note}"
