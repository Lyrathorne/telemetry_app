from __future__ import annotations

import logging
import math
from dataclasses import dataclass


LOGGER = logging.getLogger(__name__)
START_LINE_TOLERANCE_M = 25.0


@dataclass(frozen=True, slots=True)
class ValidatedLapDistance:
    lap_distance_m: float | None
    source: str
    rejected_reason: str = ""


def validate_lap_distance(
    raw_distance_m: float | None,
    track_length_m: float | None,
    previous_distance_m: float | None = None,
    lap_number: int | None = None,
) -> ValidatedLapDistance:
    if raw_distance_m is None:
        return ValidatedLapDistance(None, "unavailable")
    try:
        distance = float(raw_distance_m)
    except (TypeError, ValueError):
        LOGGER.warning("Rejecting non-numeric lap distance: value=%r lap=%r", raw_distance_m, lap_number)
        return ValidatedLapDistance(None, "rejected", "non_numeric")
    if not math.isfinite(distance):
        LOGGER.warning("Rejecting non-finite lap distance: value=%r lap=%r", raw_distance_m, lap_number)
        return ValidatedLapDistance(None, "rejected", "non_finite")
    if distance < 0.0:
        LOGGER.warning("Rejecting negative lap distance: value=%r lap=%r", raw_distance_m, lap_number)
        return ValidatedLapDistance(None, "rejected", "negative")
    if track_length_m is not None and math.isfinite(float(track_length_m)) and float(track_length_m) > 0.0:
        length = float(track_length_m)
        if distance > length + START_LINE_TOLERANCE_M:
            LOGGER.warning(
                "Rejecting out-of-range lap distance: value=%.3f length=%.3f lap=%r",
                distance,
                length,
                lap_number,
            )
            return ValidatedLapDistance(None, "rejected", "out_of_range")
        if previous_distance_m is not None and math.isfinite(float(previous_distance_m)):
            previous = float(previous_distance_m)
            if previous - distance > START_LINE_TOLERANCE_M and not (previous > length - START_LINE_TOLERANCE_M and distance < START_LINE_TOLERANCE_M):
                LOGGER.warning(
                    "Rejecting unexpected backward lap distance jump: previous=%.3f current=%.3f length=%.3f lap=%r",
                    previous,
                    distance,
                    length,
                    lap_number,
                )
                return ValidatedLapDistance(None, "rejected", "backward_jump")
    return ValidatedLapDistance(max(0.0, distance), "telemetry")


def distance_from_normalized_progress(
    normalized_position: float | None,
    track_length_m: float | None,
    lap_number: int | None = None,
) -> ValidatedLapDistance:
    if normalized_position is None or track_length_m is None:
        return ValidatedLapDistance(None, "unavailable")
    try:
        progress = float(normalized_position)
        length = float(track_length_m)
    except (TypeError, ValueError):
        return ValidatedLapDistance(None, "rejected", "non_numeric")
    if not math.isfinite(progress) or not math.isfinite(length) or length <= 0.0:
        LOGGER.warning(
            "Rejecting invalid normalized distance inputs: progress=%r length=%r lap=%r",
            normalized_position,
            track_length_m,
            lap_number,
        )
        return ValidatedLapDistance(None, "rejected", "non_finite")
    if not 0.0 <= progress <= 1.0:
        LOGGER.warning("Rejecting out-of-range normalized lap progress: value=%r lap=%r", normalized_position, lap_number)
        return ValidatedLapDistance(None, "rejected", "progress_out_of_range")
    return ValidatedLapDistance(progress * length, "normalized_progress")
