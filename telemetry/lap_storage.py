from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path

from app.paths import data_dir, ensure_user_directories
from models import LapResult, ReferenceLap, SectorResult, SessionSummary, TelemetryPoint, TelemetrySample


SCHEMA_VERSION = 5
LOGGER = logging.getLogger(__name__)


class LapStorage:
    def __init__(self, path: str | Path | None = None) -> None:
        ensure_user_directories()
        self.path = Path(path) if path is not None else data_dir() / "racing_telemetry.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA journal_mode = WAL")
            return connection
        except sqlite3.DatabaseError:
            connection.close()
            raise

    def _initialize(self) -> None:
        try:
            self._initialize_schema()
        except sqlite3.DatabaseError as error:
            LOGGER.warning("Lap database is corrupt; moving it aside and recreating: %s", error)
            self._move_corrupt_database()
            self._initialize_schema()

    def _initialize_schema(self) -> None:
        with closing(self.connect()) as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS telemetry_sessions (
                    id TEXT PRIMARY KEY,
                    game TEXT NOT NULL,
                    track TEXT,
                    track_metadata_id TEXT,
                    track_length_m REAL,
                    track_length_source TEXT,
                    car TEXT,
                    driver_name TEXT,
                    source_type TEXT,
                    started_at TEXT NOT NULL,
                    ended_at TEXT
                );

                CREATE TABLE IF NOT EXISTS laps (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    lap_number INTEGER NOT NULL,
                    lap_time_ms INTEGER,
                    valid INTEGER NOT NULL,
                    complete INTEGER NOT NULL,
                    game TEXT NOT NULL,
                    track TEXT,
                    car TEXT,
                    driver_name TEXT,
                    started_at TEXT NOT NULL,
                    completed_at TEXT,
                    notes TEXT,
                    fully_observed INTEGER NOT NULL DEFAULT 1,
                    raw_samples_recorded INTEGER NOT NULL DEFAULT 0,
                    FOREIGN KEY(session_id) REFERENCES telemetry_sessions(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS sectors (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    lap_id TEXT NOT NULL,
                    sector_number INTEGER NOT NULL,
                    start_distance_m REAL,
                    end_distance_m REAL,
                    time_ms INTEGER,
                    valid INTEGER NOT NULL,
                    comparison_status TEXT,
                    timing_source TEXT,
                    FOREIGN KEY(lap_id) REFERENCES laps(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS lap_samples (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    lap_id TEXT NOT NULL,
                    sample_index INTEGER NOT NULL,
                    timestamp REAL NOT NULL,
                    session_time REAL,
                    lap_time REAL,
                    lap_distance REAL,
                    track_length_m REAL,
                    track_metadata_id TEXT,
                    track_length_source TEXT,
                    normalized_track_position REAL,
                    speed_kmh REAL,
                    rpm REAL,
                    gear INTEGER,
                    throttle_percent REAL,
                    brake_percent REAL,
                    clutch_percent REAL,
                    steering REAL,
                    world_position_x REAL,
                    world_position_y REAL,
                    world_position_z REAL,
                    raw_json TEXT NOT NULL,
                    FOREIGN KEY(lap_id) REFERENCES laps(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS reference_laps (
                    id TEXT PRIMARY KEY,
                    game TEXT NOT NULL,
                    track_id TEXT NOT NULL,
                    track_display_name TEXT,
                    car_id TEXT NOT NULL,
                    car_display_name TEXT,
                    lap_time_ms INTEGER,
                    source TEXT,
                    player_name TEXT,
                    created_at TEXT,
                    imported_at TEXT,
                    telemetry_json TEXT NOT NULL,
                    metadata_json TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_laps_session ON laps(session_id);
                CREATE INDEX IF NOT EXISTS idx_laps_track_car ON laps(track, car);
                CREATE INDEX IF NOT EXISTS idx_laps_lap_time ON laps(lap_time_ms);
                CREATE INDEX IF NOT EXISTS idx_samples_lap_time ON lap_samples(lap_id, lap_time);
                CREATE INDEX IF NOT EXISTS idx_reference_scope ON reference_laps(game, track_id, car_id, lap_time_ms);
                """
            )
            columns = {row[1] for row in connection.execute("PRAGMA table_info(sectors)")}
            if "timing_source" not in columns:
                connection.execute("ALTER TABLE sectors ADD COLUMN timing_source TEXT")
            lap_columns = {row[1] for row in connection.execute("PRAGMA table_info(laps)")}
            if "fully_observed" not in lap_columns:
                connection.execute("ALTER TABLE laps ADD COLUMN fully_observed INTEGER NOT NULL DEFAULT 1")
            if "raw_samples_recorded" not in lap_columns:
                connection.execute("ALTER TABLE laps ADD COLUMN raw_samples_recorded INTEGER NOT NULL DEFAULT 0")
            for column, definition in (
                ("track_metadata_id", "TEXT"),
                ("track_length_m", "REAL"),
                ("track_length_source", "TEXT"),
            ):
                if column not in lap_columns:
                    connection.execute(f"ALTER TABLE laps ADD COLUMN {column} {definition}")
            sample_columns = {row[1] for row in connection.execute("PRAGMA table_info(lap_samples)")}
            for column, definition in (
                ("track_length_m", "REAL"),
                ("track_metadata_id", "TEXT"),
                ("track_length_source", "TEXT"),
            ):
                if column not in sample_columns:
                    connection.execute(f"ALTER TABLE lap_samples ADD COLUMN {column} {definition}")
            for column in ("world_position_x", "world_position_y", "world_position_z"):
                if column not in sample_columns:
                    connection.execute(f"ALTER TABLE lap_samples ADD COLUMN {column} REAL")
            connection.execute(
                """
                DELETE FROM sectors
                WHERE id NOT IN (
                    SELECT MIN(id) FROM sectors GROUP BY lap_id, sector_number
                )
                """
            )
            connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_sectors_lap_number ON sectors(lap_id, sector_number)")
            if version < SCHEMA_VERSION:
                connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            connection.commit()

    def _move_corrupt_database(self) -> None:
        if not self.path.exists():
            return
        stamp = datetime.now().strftime("%Y%m%d%H%M%S")
        corrupt_path = self.path.with_name(f"{self.path.name}.corrupt-{stamp}")
        try:
            self.path.replace(corrupt_path)
        except OSError:
            self.path.unlink(missing_ok=True)

    def ensure_session(
        self,
        session_id: str,
        game: str,
        track: str | None,
        car: str | None,
        driver_name: str | None = None,
        source_type: str = "live",
        started_at: str = "",
    ) -> None:
        with closing(self.connect()) as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO telemetry_sessions
                (id, game, track, car, driver_name, source_type, started_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (session_id, game, track, car, driver_name, source_type, started_at),
            )
            connection.commit()

    def update_session_metadata(
        self,
        session_id: str,
        game: str | None = None,
        track: str | None = None,
        car: str | None = None,
        driver_name: str | None = None,
    ) -> None:
        with closing(self.connect()) as connection:
            connection.execute(
                """
                UPDATE telemetry_sessions
                SET game = COALESCE(NULLIF(?, ''), game),
                    track = COALESCE(NULLIF(?, ''), track),
                    car = COALESCE(NULLIF(?, ''), car),
                    driver_name = COALESCE(NULLIF(?, ''), driver_name)
                WHERE id = ?
                """,
                (game, track, car, driver_name, session_id),
            )
            connection.commit()

    def end_session(self, session_id: str, ended_at: str) -> None:
        with closing(self.connect()) as connection:
            connection.execute(
                "UPDATE telemetry_sessions SET ended_at = ? WHERE id = ?",
                (ended_at, session_id),
            )
            connection.commit()

    def save_lap(self, lap: LapResult, include_samples: bool | None = None) -> None:
        save_samples = lap.raw_samples_recorded if include_samples is None else include_samples
        lap.raw_samples_recorded = bool(save_samples)
        with closing(self.connect()) as connection:
            connection.execute("BEGIN")
            connection.execute(
                """
                INSERT OR REPLACE INTO laps
                (id, session_id, lap_number, lap_time_ms, valid, complete, game, track,
                 track_metadata_id, track_length_m, track_length_source, car, driver_name,
                 started_at, completed_at, notes, fully_observed, raw_samples_recorded)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    lap.id,
                    lap.session_id,
                    lap.lap_number,
                    lap.lap_time_ms,
                    int(lap.valid),
                    int(lap.complete),
                    lap.game,
                    lap.track,
                    lap.track_metadata_id,
                    lap.track_length_m,
                    lap.track_length_source,
                    lap.car,
                    lap.driver_name,
                    lap.started_at,
                    lap.completed_at,
                    lap.notes,
                    int(lap.fully_observed),
                    int(lap.raw_samples_recorded),
                ),
            )
            connection.execute("DELETE FROM sectors WHERE lap_id = ?", (lap.id,))
            connection.execute("DELETE FROM lap_samples WHERE lap_id = ?", (lap.id,))
            connection.executemany(
                """
                INSERT INTO sectors
                (lap_id, sector_number, start_distance_m, end_distance_m, time_ms, valid, comparison_status, timing_source)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        lap.id,
                        sector.sector_number,
                        sector.start_distance_m,
                        sector.end_distance_m,
                        sector.time_ms,
                        int(sector.valid),
                        sector.comparison_status,
                        sector.timing_source,
                    )
                    for sector in lap.sectors
                ],
            )
            connection.executemany(
                """
                INSERT INTO lap_samples
                (lap_id, sample_index, timestamp, session_time, lap_time, lap_distance,
                 track_length_m, track_metadata_id, track_length_source,
                 normalized_track_position, speed_kmh, rpm, gear, throttle_percent, brake_percent,
                 clutch_percent, steering, world_position_x, world_position_y, world_position_z, raw_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    sample_row(lap.id, index, sample)
                    for index, sample in enumerate(lap.samples if save_samples else [])
                ],
            )
            connection.commit()
            LOGGER.info(
                "[SessionStorage] Saved session: track=%s car=%s laps=%s",
                lap.track,
                lap.car,
                self._session_lap_count(connection, lap.session_id),
            )

    def load_laps(self) -> list[LapResult]:
        try:
            connection_context = closing(self.connect())
        except sqlite3.DatabaseError:
            LOGGER.warning("Lap database could not be opened", exc_info=True)
            return []
        with connection_context as connection:
            try:
                return self._load_laps_from_connection(connection)
            except (sqlite3.DatabaseError, json.JSONDecodeError, TypeError) as error:
                LOGGER.warning("Lap database load failed; returning empty lap list: %s", error)
                return []

    def _load_laps_from_connection(self, connection: sqlite3.Connection) -> list[LapResult]:
        lap_rows = connection.execute(
            """
            SELECT id, session_id, lap_number, lap_time_ms, valid, complete, game, track,
                   track_metadata_id, track_length_m, track_length_source, car, driver_name,
                   started_at, completed_at, notes, fully_observed, raw_samples_recorded
            FROM laps
            ORDER BY started_at DESC, lap_number DESC
            """
        ).fetchall()
        laps: list[LapResult] = []
        for row in lap_rows:
            lap_id = row[0]
            sectors = [
                SectorResult(
                    sector_number=sector_row[0],
                    start_distance_m=sector_row[1],
                    end_distance_m=sector_row[2],
                    time_ms=sector_row[3],
                    valid=bool(sector_row[4]),
                    comparison_status=sector_row[5],
                    timing_source=sector_row[6] or "unavailable",
                )
                for sector_row in connection.execute(
                    """
                    SELECT sector_number, start_distance_m, end_distance_m, time_ms, valid, comparison_status, timing_source
                    FROM sectors WHERE lap_id = ? ORDER BY sector_number
                    """,
                    (lap_id,),
                )
            ]
            samples = [
                sample_from_json(sample_row_data[0])
                for sample_row_data in connection.execute(
                    "SELECT raw_json FROM lap_samples WHERE lap_id = ? ORDER BY sample_index",
                    (lap_id,),
                )
            ]
            laps.append(
                LapResult(
                    id=lap_id,
                    session_id=row[1],
                    lap_number=row[2],
                    lap_time_ms=row[3],
                    valid=bool(row[4]),
                    complete=bool(row[5]),
                    game=row[6],
                    track=row[7],
                    track_metadata_id=row[8],
                    track_length_m=row[9],
                    track_length_source=row[10],
                    car=row[11],
                    driver_name=row[12],
                    started_at=row[13],
                    completed_at=row[14],
                    notes=row[15] or "",
                    fully_observed=bool(row[16]),
                    raw_samples_recorded=bool(row[17]),
                    sectors=sectors,
                    samples=samples,
                )
            )
        return laps

    @staticmethod
    def _session_lap_count(connection: sqlite3.Connection, session_id: str) -> int:
        row = connection.execute(
            "SELECT COUNT(*) FROM laps WHERE session_id = ? AND complete = 1",
            (session_id,),
        ).fetchone()
        return int(row[0] or 0) if row else 0

    def load_session_summaries(self) -> list[SessionSummary]:
        try:
            with closing(self.connect()) as connection:
                rows = connection.execute(
                    """
                    SELECT
                        session.id,
                        session.game,
                        session.track,
                        session.car,
                        session.driver_name,
                        session.source_type,
                        session.started_at,
                        session.ended_at,
                        COUNT(laps.id) AS lap_count,
                        MIN(CASE WHEN laps.complete = 1 AND laps.valid = 1 THEN laps.lap_time_ms END) AS best_lap_time_ms,
                        SUM(CASE WHEN laps.complete = 1 AND laps.valid = 1 THEN 1 ELSE 0 END) AS valid_lap_count
                    FROM telemetry_sessions AS session
                    LEFT JOIN laps ON laps.session_id = session.id AND laps.complete = 1
                    GROUP BY session.id
                    ORDER BY session.started_at DESC
                    """
                ).fetchall()
        except sqlite3.DatabaseError:
            return []
        return [
            SessionSummary(
                session_id=row[0],
                game=row[1] or "",
                track=row[2],
                car=row[3],
                driver_name=row[4],
                source_type=row[5] or "",
                started_at=row[6] or "",
                ended_at=row[7],
                lap_count=int(row[8] or 0),
                best_lap_time_ms=row[9],
                valid_lap_count=int(row[10] or 0),
            )
            for row in rows
        ]

    def delete_lap(self, lap_id: str) -> None:
        with closing(self.connect()) as connection:
            connection.execute("DELETE FROM laps WHERE id = ?", (lap_id,))
            connection.commit()

    def save_reference_lap(self, reference: ReferenceLap) -> None:
        with closing(self.connect()) as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO reference_laps
                (id, game, track_id, track_display_name, car_id, car_display_name, lap_time_ms,
                 source, player_name, created_at, imported_at, telemetry_json, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    reference.id,
                    reference.game,
                    reference.track_id,
                    reference.track_display_name,
                    reference.car_id,
                    reference.car_display_name,
                    reference.lap_time_ms,
                    reference.source,
                    reference.player_name,
                    reference.created_at,
                    reference.imported_at,
                    json.dumps([telemetry_point_to_dict(point) for point in reference.telemetry_points]),
                    json.dumps(reference.metadata),
                ),
            )
            connection.commit()

    def load_reference_laps(self) -> list[ReferenceLap]:
        try:
            with closing(self.connect()) as connection:
                rows = connection.execute(
                    """
                    SELECT id, game, track_id, track_display_name, car_id, car_display_name,
                           lap_time_ms, source, player_name, created_at, imported_at,
                           telemetry_json, metadata_json
                    FROM reference_laps
                    ORDER BY imported_at DESC
                    """
                ).fetchall()
        except (sqlite3.DatabaseError, json.JSONDecodeError):
            return []
        references: list[ReferenceLap] = []
        for row in rows:
            try:
                points = [TelemetryPoint(**point) for point in json.loads(row[11] or "[]") if isinstance(point, dict)]
                metadata = json.loads(row[12] or "{}")
            except (json.JSONDecodeError, TypeError):
                LOGGER.warning("Skipping malformed reference lap: id=%s", row[0])
                continue
            references.append(
                ReferenceLap(
                    id=row[0],
                    game=row[1] or "",
                    track_id=row[2] or "",
                    track_display_name=row[3] or "",
                    car_id=row[4] or "",
                    car_display_name=row[5] or "",
                    lap_time_ms=row[6],
                    source=row[7] or "",
                    player_name=row[8] or "",
                    created_at=row[9] or "",
                    imported_at=row[10] or "",
                    telemetry_points=points,
                    metadata=metadata if isinstance(metadata, dict) else {},
                )
            )
        return references

    def best_reference_lap(self, game: str, track_id: str | None, car_id: str | None) -> ReferenceLap | None:
        game_key = (game or "").casefold()
        track_key = track_id or ""
        car_key = car_id or ""
        candidates = [
            reference
            for reference in self.load_reference_laps()
            if reference.game.casefold() == game_key
            and reference.track_id == track_key
            and reference.car_id == car_key
            and reference.lap_time_ms is not None
        ]
        if not candidates:
            return None
        return min(candidates, key=lambda reference: int(reference.lap_time_ms or 0))


def sample_row(lap_id: str, index: int, sample: TelemetrySample) -> tuple:
    return (
        lap_id,
        index,
        sample.timestamp,
        sample.session_time,
        sample.lap_time,
        sample.lap_distance,
        sample.track_length_m,
        sample.track_metadata_id,
        sample.track_length_source,
        sample.normalized_track_position,
        sample.speed_kmh,
        sample.rpm,
        sample.gear,
        sample.throttle_percent,
        sample.brake_percent,
        sample.clutch_percent,
        sample.steering,
        sample.world_position_x,
        sample.world_position_y,
        sample.world_position_z,
        json.dumps(sample_to_dict(sample)),
    )


def sample_to_dict(sample: TelemetrySample) -> dict:
    return {field: getattr(sample, field) for field in TelemetrySample.__dataclass_fields__}


def sample_from_json(raw_json: str) -> TelemetrySample:
    data = json.loads(raw_json)
    allowed = TelemetrySample.__dataclass_fields__
    return TelemetrySample(**{key: value for key, value in data.items() if key in allowed})


def telemetry_point_to_dict(point: TelemetryPoint) -> dict:
    return {field: getattr(point, field) for field in TelemetryPoint.__dataclass_fields__}
