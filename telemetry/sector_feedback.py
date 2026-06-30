from __future__ import annotations

from models import LapResult, ReferenceLap


def sector_feedback(lap: LapResult, reference: LapResult | ReferenceLap | None) -> list[str]:
    if reference is None:
        return []
    reference_sectors = reference_sector_times(reference)
    messages: list[str] = []
    losses: list[tuple[int, int]] = []
    for sector in lap.sectors:
        if sector.time_ms is None:
            continue
        reference_ms = reference_sectors.get(sector.sector_number)
        if reference_ms is None:
            continue
        delta_ms = int(sector.time_ms) - int(reference_ms)
        if delta_ms == 0:
            continue
        verb = "lost" if delta_ms > 0 else "gained"
        messages.append(f"Sector {sector.sector_number}: {verb} {format_signed_seconds(delta_ms)} compared to reference")
        if delta_ms > 0:
            losses.append((sector.sector_number, delta_ms))
    if losses:
        sector_number, delta_ms = max(losses, key=lambda item: item[1])
        messages.append(f"Most time lost in Sector {sector_number}: {format_signed_seconds(delta_ms)}")
    return messages[:2]


def reference_sector_times(reference: LapResult | ReferenceLap) -> dict[int, int]:
    if isinstance(reference, LapResult):
        return {
            sector.sector_number: int(sector.time_ms)
            for sector in reference.sectors
            if sector.time_ms is not None
        }
    if reference.lap_time_ms is None:
        return {}
    sector_count = 3
    if len(reference.telemetry_points) >= 3:
        return {}
    return {sector_number: int(reference.lap_time_ms / sector_count) for sector_number in (1, 2, 3)}


def format_signed_seconds(delta_ms: int) -> str:
    sign = "+" if delta_ms >= 0 else "-"
    return f"{sign}{abs(delta_ms) / 1000.0:.3f}"
