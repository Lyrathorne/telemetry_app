from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from app.paths import settings_dir


@dataclass(frozen=True, slots=True)
class TrackDefinition:
    game: str
    track_id: str
    display_name: str
    layout_id: str | None
    track_length_m: float | None
    sector_boundaries_normalized: tuple[float, ...]
    data_source: str
    data_version: str


class TrackMetadataError(ValueError):
    pass


def validate_boundaries(boundaries: tuple[float, ...]) -> None:
    previous = 0.0
    for boundary in boundaries:
        if not 0.0 < boundary < 1.0:
            raise TrackMetadataError("Sector boundaries must be between 0.0 and 1.0")
        if boundary <= previous:
            raise TrackMetadataError("Sector boundaries must be strictly increasing")
        previous = boundary


def track_definition_from_dict(data: dict) -> TrackDefinition:
    boundaries = tuple(float(value) for value in data.get("sector_boundaries_normalized", []))
    validate_boundaries(boundaries)
    return TrackDefinition(
        game=str(data["game"]),
        track_id=str(data["track_id"]),
        display_name=str(data.get("display_name") or data["track_id"]),
        layout_id=str(data["layout_id"]) if data.get("layout_id") else None,
        track_length_m=float(data["track_length_m"]) if data.get("track_length_m") is not None else None,
        sector_boundaries_normalized=boundaries,
        data_source=str(data.get("data_source") or "user"),
        data_version=str(data.get("data_version") or "user"),
    )


class TrackMetadataRepository:
    def __init__(self, override_path: Path | None = None) -> None:
        self.override_path = override_path or settings_dir() / "track_overrides.json"
        self._definitions: dict[tuple[str, str, str | None], TrackDefinition] = {}
        self.load_overrides()

    def load_overrides(self) -> None:
        self._definitions.clear()
        if not self.override_path.exists():
            return
        data = json.loads(self.override_path.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            raise TrackMetadataError("Track override file must contain a list")
        for item in data:
            definition = track_definition_from_dict(item)
            self._definitions[(definition.game, definition.track_id, definition.layout_id)] = definition

    def find(self, game: str, track_id: str, layout_id: str | None = None) -> TrackDefinition | None:
        return self._definitions.get((game, track_id, layout_id)) or self._definitions.get((game, track_id, None))
