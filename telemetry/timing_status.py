from __future__ import annotations

from models import LapResult


TIMING_STATUS_PURPLE = "PURPLE"
TIMING_STATUS_GREEN = "GREEN"
TIMING_STATUS_YELLOW = "YELLOW"
TIMING_STATUS_NEUTRAL = "NEUTRAL"
TIMING_STATUS_INVALID = "INVALID"
TIMING_STATUS_UNAVAILABLE = "UNAVAILABLE"


def recalculate_sector_statuses(laps: list[LapResult], changed_lap: LapResult | None = None) -> None:
    compatible_laps = [lap for lap in laps if changed_lap is None or same_scope(lap, changed_lap)]
    for lap in compatible_laps:
        for sector in lap.sectors:
            if not lap.valid or not sector.valid:
                sector.comparison_status = TIMING_STATUS_INVALID
            elif sector.time_ms is None:
                sector.comparison_status = TIMING_STATUS_UNAVAILABLE
            else:
                sector.comparison_status = TIMING_STATUS_NEUTRAL

    for sector_number in (1, 2, 3):
        valid_sectors = [
            (lap, sector)
            for lap in compatible_laps
            for sector in lap.sectors
            if sector.sector_number == sector_number
            and lap.valid
            and sector.valid
            and sector.time_ms is not None
        ]
        if not valid_sectors:
            continue

        fastest_time_ms = min(int(sector.time_ms) for _lap, sector in valid_sectors if sector.time_ms is not None)
        purple_assigned = False
        historical_best_ms: int | None = None
        for lap, sector in sorted(valid_sectors, key=lambda pair: (pair[0].started_at, pair[0].lap_number, pair[0].id)):
            assert sector.time_ms is not None
            current_time_ms = int(sector.time_ms)
            was_new_best = historical_best_ms is None or current_time_ms < historical_best_ms
            if current_time_ms == fastest_time_ms and not purple_assigned:
                sector.comparison_status = TIMING_STATUS_PURPLE
                purple_assigned = True
            elif was_new_best:
                sector.comparison_status = TIMING_STATUS_GREEN
            elif historical_best_ms is not None and current_time_ms < historical_best_ms:
                sector.comparison_status = TIMING_STATUS_GREEN
            elif len(valid_sectors) > 1:
                sector.comparison_status = TIMING_STATUS_YELLOW
            else:
                sector.comparison_status = TIMING_STATUS_NEUTRAL
            historical_best_ms = current_time_ms if historical_best_ms is None else min(historical_best_ms, current_time_ms)


def same_scope(a: LapResult, b: LapResult) -> bool:
    return a.track == b.track and a.car == b.car and a.driver_name == b.driver_name
