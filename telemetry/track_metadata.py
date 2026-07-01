from __future__ import annotations

import json
import sqlite3
import unicodedata
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from app.paths import resource_path, settings_dir


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
    acc_track_id: str | None = None
    map_kind: str = ""
    map_accuracy: str = ""


@dataclass(frozen=True, slots=True)
class Point:
    x: float
    y: float


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
    def __init__(self, override_path: Path | None = None, builtin_dir: Path | None = None) -> None:
        self.override_path = override_path or settings_dir() / "track_overrides.json"
        self.builtin_dir = builtin_dir or resource_path("resources", "acc_tracks")
        self.sqlite_path = self.builtin_dir / "acc_tracks.sqlite"
        self.json_path = self.builtin_dir / "acc_tracks.json"
        self._definitions: dict[tuple[str, str, str | None], TrackDefinition] = {}
        self._acc_index: dict[str, TrackDefinition] = {}
        self._alias_index: dict[str, TrackDefinition] = {}
        self._map_points: dict[str, list[Point]] = {}
        self._svg: dict[str, str] = {}
        self.load_builtin()
        self.load_overrides()

    def load_builtin(self) -> None:
        if self.sqlite_path.exists():
            self._load_builtin_sqlite()
            return
        if self.json_path.exists():
            self._load_builtin_json()

    def _load_builtin_sqlite(self) -> None:
        with closing(sqlite3.connect(f"file:{self.sqlite_path}?mode=ro", uri=True)) as connection:
            track_rows = connection.execute(
                """
                SELECT id, acc_track_id, name, length_m, map_kind, map_accuracy,
                       length_source_name, layout_name
                FROM tracks
                """
            ).fetchall()
            for row in track_rows:
                definition = TrackDefinition(
                    game="ACC",
                    track_id=str(row[0]),
                    display_name=str(row[2]),
                    layout_id=str(row[7]) if row[7] else None,
                    track_length_m=float(row[3]),
                    sector_boundaries_normalized=(),
                    data_source=str(row[6] or "acc_tracks.sqlite"),
                    data_version="builtin",
                    acc_track_id=str(row[1]),
                    map_kind=str(row[4] or ""),
                    map_accuracy=str(row[5] or ""),
                )
                self._register_definition(definition)
            for track_id, alias in connection.execute("SELECT track_id, alias FROM track_aliases"):
                definition = self._acc_index.get(normalize_track_key(str(track_id)))
                if definition is not None:
                    self._alias_index[normalize_track_key(str(alias))] = definition
            for track_id, x, y in connection.execute(
                "SELECT track_id, x_normalized, y_normalized FROM track_map_points ORDER BY track_id, point_index"
            ):
                self._map_points.setdefault(str(track_id), []).append(Point(float(x), float(y)))
            for track_id, svg_text in connection.execute("SELECT track_id, svg_text FROM track_maps"):
                self._svg[str(track_id)] = str(svg_text)

    def _load_builtin_json(self) -> None:
        data = json.loads(self.json_path.read_text(encoding="utf-8"))
        for item in data.get("tracks", []):
            definition = TrackDefinition(
                game="ACC",
                track_id=str(item["id"]),
                display_name=str(item.get("name") or item["id"]),
                layout_id=str(item["layout_name"]) if item.get("layout_name") else None,
                track_length_m=float(item["length_m"]) if item.get("length_m") is not None else None,
                sector_boundaries_normalized=(),
                data_source=str(item.get("length_source_name") or "acc_tracks.json"),
                data_version=str(data.get("generated_at_utc") or "builtin"),
                acc_track_id=str(item.get("acc_track_id") or item["id"]),
                map_kind=str(item.get("map_kind") or ""),
                map_accuracy=str(item.get("map_accuracy") or ""),
            )
            self._register_definition(definition)
            for alias in item.get("aliases", []):
                self._alias_index[normalize_track_key(str(alias))] = definition

    def load_overrides(self) -> None:
        if not self.override_path.exists():
            return
        data = json.loads(self.override_path.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            raise TrackMetadataError("Track override file must contain a list")
        for item in data:
            definition = track_definition_from_dict(item)
            self._register_definition(definition)

    def find(self, game: str, track_id: str, layout_id: str | None = None) -> TrackDefinition | None:
        return self._definitions.get((game, track_id, layout_id)) or self._definitions.get((game, track_id, None))

    def find_by_acc_id(self, track_id: str) -> TrackDefinition | None:
        key = normalize_track_key(track_id)
        return self._acc_index.get(key) or self._alias_index.get(key)

    def find_by_alias(self, name: str) -> TrackDefinition | None:
        return self._alias_index.get(normalize_track_key(name))

    def get_length_m(self, track_id: str) -> int | None:
        definition = self.find_by_acc_id(track_id) or self.find_by_alias(track_id)
        if definition is None or definition.track_length_m is None:
            return None
        return int(round(definition.track_length_m))

    def get_map_points(self, track_id: str) -> list[Point]:
        definition = self.find_by_acc_id(track_id) or self.find_by_alias(track_id)
        if definition is None:
            return []
        return list(self._map_points.get(definition.track_id, []))

    def get_svg(self, track_id: str) -> str | None:
        definition = self.find_by_acc_id(track_id) or self.find_by_alias(track_id)
        if definition is None:
            return None
        if definition.track_id in self._svg:
            return self._svg[definition.track_id]
        svg_path = self.builtin_dir / "maps" / f"{definition.track_id}.svg"
        if svg_path.exists():
            return svg_path.read_text(encoding="utf-8")
        return None

    def _register_definition(self, definition: TrackDefinition) -> None:
        self._definitions[(definition.game, definition.track_id, definition.layout_id)] = definition
        for key in (definition.track_id, definition.acc_track_id, definition.display_name):
            if key:
                self._acc_index[normalize_track_key(key)] = definition
                self._alias_index[normalize_track_key(key)] = definition


def normalize_track_key(value: str) -> str:
    text = unicodedata.normalize("NFKD", value.strip().casefold())
    text = "".join(char for char in text if not unicodedata.combining(char))
    normalized = []
    for char in text:
        if char.isalnum():
            normalized.append(char)
        elif char in {" ", "-", "_"}:
            normalized.append(" ")
    return " ".join("".join(normalized).split())
